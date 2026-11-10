#!/usr/bin/env python3
"""Download external artifacts atomically with exact SHA-256/length verification."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import tempfile
import urllib.parse
import urllib.request


class HTTPSOnly(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urllib.parse.urlsplit(newurl).scheme != 'https':
            raise ValueError('Refusing a non-HTTPS redirect')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest', type=Path)
    parser.add_argument('--out', type=Path, default=Path('downloads'))
    args = parser.parse_args()
    spec = json.loads(args.manifest.read_text())
    if spec.get('schema') != 'reversedb-external-downloads/v1':
        parser.error('Unknown manifest schema')
    assets = spec.get('artifacts', [])
    if not assets:
        parser.error('No artifacts supplied')
    names = set()
    for asset in assets:
        name = asset.get('filename')
        url = urllib.parse.urlsplit(asset.get('url') or '')
        if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', name) or name in names:
            parser.error('Expected unique simple artifact filenames')
        names.add(name)
        if url.scheme != 'https' or not url.hostname or url.username or url.password:
            parser.error('Supply a real HTTPS URL without embedded credentials; template URLs are not downloads')
        if not re.fullmatch(r'[a-f0-9]{64}', asset.get('sha256') or ''):
            parser.error('Supply the exact SHA-256 from the archive publisher')
        if type(asset.get('bytes')) is not int or asset['bytes'] <= 0:
            parser.error('Supply the exact positive artifact byte length')
    args.out.mkdir(parents=True, exist_ok=True)
    opener = urllib.request.build_opener(HTTPSOnly())
    for asset in assets:
        target = args.out / asset['filename']
        if target.exists():
            raise SystemExit(f'Refusing to overwrite {target}')
        temp = None
        try:
            with tempfile.NamedTemporaryFile(dir=args.out, prefix='.download-', delete=False) as out:
                temp = Path(out.name)
                digest = hashlib.sha256()
                total = 0
                with opener.open(asset['url'], timeout=60) as response:
                    while chunk := response.read(1024 * 1024):
                        total += len(chunk)
                        if total > asset['bytes']:
                            raise ValueError('Download exceeds declared length')
                        digest.update(chunk)
                        out.write(chunk)
                if total != asset['bytes'] or digest.hexdigest() != asset['sha256']:
                    raise ValueError('Artifact length/checksum mismatch')
            # Exclusive create prevents replacing a file created during download.
            import os
            os.link(temp, target)
            print('Verified', target.name, total, 'bytes')
        finally:
            if temp is not None:
                temp.unlink(missing_ok=True)
    print('No archives extracted and no downloaded code executed.')


if __name__ == '__main__':
    main()
