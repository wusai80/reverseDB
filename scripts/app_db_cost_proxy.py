"""Loopback PostgreSQL byte counter for separate, untimed diagnostics only."""
import json
import socket
import socketserver
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class CountingProxy:
    def __init__(self, target):
        self.target = target
        self.lock = threading.Lock()
        self.counts = {"client_to_server_bytes": 0, "server_to_client_bytes": 0}
        owner = self

        class Handler(socketserver.BaseRequestHandler):
            def handle(self):
                with socket.create_connection(owner.target, timeout=10) as upstream:
                    upstream.settimeout(None)

                    def pump(source, destination, direction):
                        try:
                            while True:
                                data = source.recv(65536)
                                if not data:
                                    break
                                # Count before delivery: a completed client read
                                # implies its bytes are already reflected here.
                                with owner.lock:
                                    owner.counts[direction] += len(data)
                                destination.sendall(data)
                        except OSError:
                            pass
                        finally:
                            try:
                                destination.shutdown(socket.SHUT_WR)
                            except OSError:
                                pass

                    downstream = threading.Thread(target=pump, args=(upstream, self.request, "server_to_client_bytes"), daemon=True)
                    downstream.start()
                    pump(self.request, upstream, "client_to_server_bytes")
                    downstream.join(timeout=10)

        class Counter(BaseHTTPRequestHandler):
            def do_GET(self):
                with owner.lock:
                    body = json.dumps(owner.counts).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.admin = ThreadingHTTPServer(("127.0.0.1", 0), Counter)
        self.threads = [threading.Thread(target=s.serve_forever, daemon=True) for s in (self.server, self.admin)]
        for thread in self.threads:
            thread.start()

    @property
    def address(self):
        return "%s:%d" % self.server.server_address

    @property
    def counter_url(self):
        return "http://%s:%d/" % self.admin.server_address

    def close(self):
        for server in (self.server, self.admin):
            server.shutdown()
            server.server_close()
        for thread in self.threads:
            thread.join(timeout=5)
