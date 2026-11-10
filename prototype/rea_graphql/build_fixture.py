#!/usr/bin/env python3
"""Synthetic protocol fixture, NOT a Saleor capture or application execution.

Used to check whether REA distinguishes GraphQL operations sharing one HTTP
endpoint. Responses and timings are intentionally authored test data.
"""
import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path


def fixture():
    list_query = "query Orders { orders { id note } }"
    detail_query = "query Order($id: ID!) { order(id: $id) { id note } }"
    update = "mutation UpdateOrder($id: ID!, $note: String!) { updateOrder(id: $id, note: $note) { id note } }"
    orders = [{"id": "order-alpha", "note": "Initial Alpha"}, {"id": "order-beta", "note": "Initial Beta"}]
    entries = []

    def add(operation, query, variables, response):
        request_text = json.dumps({"operationName": operation, "query": query, "variables": variables})
        response_text = json.dumps(response)
        stamp = datetime(2026, 11, 3, tzinfo=timezone.utc) + timedelta(seconds=len(entries) * 2)
        entries.append({"startedDateTime": stamp.isoformat(), "time": 1,
            "request": {"method": "POST", "url": "https://protocol-fixture.invalid/graphql/",
                "httpVersion": "HTTP/1.1", "headers": [{"name": "Content-Type", "value": "application/json"}],
                "cookies": [], "queryString": [], "headersSize": -1, "bodySize": len(request_text.encode()),
                "postData": {"mimeType": "application/json", "text": request_text}},
            "response": {"status": 200, "statusText": "OK", "httpVersion": "HTTP/1.1",
                "redirectURL": "",
                "headers": [{"name": "Content-Type", "value": "application/json"}], "cookies": [],
                "headersSize": -1, "bodySize": len(response_text.encode()),
                "content": {"mimeType": "application/json", "size": len(response_text.encode()), "text": response_text}},
            "cache": {}, "timings": {"send": 0, "wait": 1, "receive": 0},
            "comment": "SYNTHETIC protocol unit fixture; not executed against an app."})

    for _ in range(2):
        add("Orders", list_query, {}, {"data": {"orders": orders}})
        for order in orders:
            add("Order", detail_query, {"id": order["id"]}, {"data": {"order": order}})
    for order in orders:
        order["note"] = "Updated " + order["id"]
        add("UpdateOrder", update, {"id": order["id"], "note": order["note"]}, {"data": {"updateOrder": order}})
        add("Order", detail_query, {"id": order["id"]}, {"data": {"order": order}})
        add("Orders", list_query, {}, {"data": {"orders": orders}})
    # HTTP success is not GraphQL operation success. This is synthetic as well.
    add("UpdateOrder", update, {"id": "order-alpha", "note": "Denied"},
        {"data": {"updateOrder": None}, "errors": [{"message": "Forbidden", "extensions": {"code": "FORBIDDEN"}}]})
    return {"log": {"version": "1.2", "creator": {"name": "ReverseDB synthetic GraphQL protocol fixture", "version": "1"},
                    "comment": "All bodies, statuses and timings authored; no network calls or Saleor schema validation. One identity. Tests protocol separation only.",
                    "entries": entries}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with args.output.open("x") as target:
        json.dump(fixture(), target, indent=2)
        target.write("\n")


if __name__ == "__main__":
    main()
