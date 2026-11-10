"""Failure controls: matching URL/order must never substitute for request identity."""
import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"scripts"))
from correlate_http_sql import correlate


class CorrelationTests(unittest.TestCase):
    def setUp(self):
        self.entries = [{"request": {"method": "GET", "url": "http://127.0.0.1/api/tags/?x=1"},
                         "response": {"status": 200, "headers": [
                             {"name": "X-ReverseDB-Trace-ID", "value": rid}]}}
                        for rid in ("one", "two")]
        self.requests = [{"request_id": rid, "method": "GET", "path": "/api/tags/",
                          "query_string": "x=1", "status": 200, "queries": [
                              {"phase": rid, "sql": "SELECT 1", "stack": []}] * count}
                         for rid, count in [("two", 2), ("one", 1)]]

    def test_identical_urls_reordered_server_records(self):
        self.assertEqual([r["sql_count"] for r in correlate(self.entries, self.requests)], [1, 2])

    def test_missing_id_rejected(self):
        self.entries[0]["response"]["headers"] = []
        with self.assertRaises(ValueError):
            correlate(self.entries, self.requests)

    def test_duplicate_id_rejected(self):
        self.entries[1] = copy.deepcopy(self.entries[0])
        with self.assertRaises(ValueError):
            correlate(self.entries, self.requests)

    def test_missing_server_evidence_rejected(self):
        with self.assertRaises(ValueError):
            correlate(self.entries, self.requests[:1])

    def test_foreign_query_rejected(self):
        self.requests[0]["queries"][0]["phase"] = "one"
        with self.assertRaises(ValueError):
            correlate(self.entries, self.requests)


if __name__ == "__main__":
    unittest.main()
