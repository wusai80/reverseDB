#!/usr/bin/env python3
"""Small designed multi-actor scenario; NOT TPC-C or a reconstructed application.

This in-memory model is an executable workload design. Actors are selected by
the trusted experiment driver, not authenticated network clients. An application
adapter and real-application differential checks are separate future work.
"""
import argparse
import copy
import hashlib
import json
import random
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


def generate(seed=42, warehouses=2, districts=2, customers=10, items=100):
    if warehouses < 2 or districts < 1 or customers < 1 or items < 3:
        raise ValueError("Scenario needs >=2 warehouses, >=1 district/customer, >=3 items")
    if warehouses * districts * customers > 100000 or warehouses * items > 100000:
        raise ValueError("Local fixture budget exceeded; use an explicit larger-scale adapter")
    rng = random.Random(seed)
    data = {"seed": seed, "warehouses": [], "districts": [], "customers": [],
            "items": [{"id": i, "price_cents": rng.randint(100, 10000)} for i in range(1, items + 1)],
            "stock": [], "actors": []}
    for w in range(1, warehouses + 1):
        data["warehouses"].append({"id": w, "name": f"Warehouse {w}"})
        for d in range(1, districts + 1):
            district = f"w{w}-d{d}"
            data["districts"].append({"id": district, "warehouse": w})
            for c in range(1, customers + 1):
                data["customers"].append({"id": f"{district}-c{c}", "district": district, "warehouse": w})
        for item in data["items"]:
            data["stock"].append({"warehouse": w, "item": item["id"], "quantity": 100})
        for role in ("manager", "sales", "cashier", "fulfillment"):
            data["actors"].append({"id": f"w{w}-{role}", "role": role, "warehouse": w})
        data["actors"].append({"id": f"w{w}-customer", "role": "customer", "warehouse": w,
                               "customer": f"w{w}-d1-c1"})
    data["actors"].append({"id": "external-auditor", "role": "auditor", "warehouse": None})
    return data


class BusinessModel:
    """Explicitly designed rules, using a coarse lock for a deterministic state boundary."""
    def __init__(self, data):
        self.actors = {a["id"]: a for a in data["actors"]}
        self.customers = {c["id"]: c for c in data["customers"]}
        self.prices = {i["id"]: i["price_cents"] for i in data["items"]}
        self.stock = {(s["warehouse"], s["item"]): s["quantity"] for s in data["stock"]}
        self.orders, self.grants, self.payments, self.events = {}, set(), {}, []
        self.lock = threading.Lock()

    def can_read(self, actor, order):
        if actor["role"] in ("manager", "sales", "cashier", "fulfillment"):
            return actor["warehouse"] == order["warehouse"]
        return actor.get("customer") == order["customer"] or (actor["id"], order["id"]) in self.grants

    def state(self):
        return {"orders": copy.deepcopy(self.orders), "stock": sorted((w, i, q) for (w, i), q in self.stock.items()),
                "grants": sorted(self.grants), "payments": copy.deepcopy(self.payments)}

    def call(self, actor_id, operation, **args):
        with self.lock:
            before = self.state()
            result = self._apply(actor_id, operation, args)
            changed = before != self.state()
            if result["outcome"] != "ok":
                assert not changed, "Rejected operations must leave state unchanged"
            self.events.append({"sequence": len(self.events), "actor": actor_id,
                                "operation": operation, "arguments": args,
                                "result": copy.deepcopy(result), "state_changed": changed,
                                "origin": "designed_in_memory_simulation"})
            return result

    def _apply(self, actor_id, op, args):
        actor = self.actors.get(actor_id)
        if actor is None:
            return {"outcome": "unauthenticated"}
        if op == "new_order":
            if actor["role"] != "sales":
                return {"outcome": "forbidden"}
            customer = self.customers.get(args.get("customer"))
            if customer is None or customer["warehouse"] != actor["warehouse"]:
                return {"outcome": "not_found"}
            lines = args.get("lines", [])
            if not lines or any(item not in self.prices or type(qty) is not int or qty <= 0 for item, qty in lines):
                return {"outcome": "invalid"}
            quantities = {}
            for item, qty in lines:
                quantities[item] = quantities.get(item, 0) + qty
            w = actor["warehouse"]
            if any(self.stock[(w, item)] < qty for item, qty in quantities.items()):
                return {"outcome": "conflict"}
            pk = len(self.orders) + 1
            order = {"id": pk, "warehouse": w, "customer": customer["id"], "status": "new",
                     "total_cents": sum(self.prices[i] * q for i, q in quantities.items()),
                     "lines": sorted(quantities.items())}
            for item, qty in quantities.items():
                self.stock[(w, item)] -= qty
            self.orders[pk] = order
            return {"outcome": "ok", "order": copy.deepcopy(order)}
        if op == "stock_level":
            if actor["role"] not in ("manager", "fulfillment"):
                return {"outcome": "forbidden"}
            return {"outcome": "ok", "low_items": sorted(i for (w, i), q in self.stock.items()
                    if w == actor["warehouse"] and q < args.get("threshold", 20))}
        order = self.orders.get(args.get("order"))
        if order is None or not self.can_read(actor, order):
            return {"outcome": "not_found"}
        if op == "order_status":
            return {"outcome": "ok", "order": copy.deepcopy(order)}
        if op in ("grant_view", "revoke_view"):
            if actor["role"] != "manager":
                return {"outcome": "forbidden"}
            if args.get("grantee") not in self.actors:
                return {"outcome": "invalid"}
            key = (args["grantee"], order["id"])
            if op == "grant_view":
                self.grants.add(key)
            else:
                self.grants.discard(key)
            return {"outcome": "ok"}
        if op == "payment":
            if actor["role"] != "cashier":
                return {"outcome": "forbidden"}
            key = args.get("idempotency_key")
            if not isinstance(key, str) or not key:
                return {"outcome": "invalid"}
            payment = {"order": order["id"], "amount_cents": args.get("amount_cents")}
            if key in self.payments:
                return {"outcome": "ok" if self.payments[key] == payment else "conflict"}
            if order["status"] != "new" or payment["amount_cents"] != order["total_cents"]:
                return {"outcome": "conflict"}
            self.payments[key] = payment
            order["status"] = "paid"
            return {"outcome": "ok"}
        if op == "delivery":
            if actor["role"] != "fulfillment":
                return {"outcome": "forbidden"}
            if order["status"] != "paid":
                return {"outcome": "conflict"}
            order["status"] = "delivered"
            return {"outcome": "ok"}
        return {"outcome": "invalid"}


def simulate(data):
    model = BusinessModel(data)
    checks = []

    def expect(actor, op, outcome="ok", **args):
        result = model.call(actor, op, **args)
        assert result["outcome"] == outcome, (actor, op, result)
        checks.append({"actor": actor, "operation": op, "expected": outcome, "passed": True})
        return result

    created = expect("w1-sales", "new_order", customer="w1-d1-c1", lines=[(1, 2), (2, 3)])["order"]
    pk = created["id"]
    expect("w1-customer", "order_status", order=pk)
    expect("w2-customer", "order_status", "not_found", order=pk)
    expect("w2-manager", "order_status", "not_found", order=pk)
    expect("external-auditor", "order_status", "not_found", order=pk)
    expect("w1-manager", "grant_view", order=pk, grantee="external-auditor")
    expect("external-auditor", "order_status", order=pk)
    expect("external-auditor", "payment", "forbidden", order=pk, amount_cents=created["total_cents"], idempotency_key="denied")
    expect("external-auditor", "grant_view", "forbidden", order=pk, grantee="w2-customer")
    expect("w1-fulfillment", "delivery", "conflict", order=pk)
    expect("w1-cashier", "payment", order=pk, amount_cents=created["total_cents"], idempotency_key="payment-1")
    expect("w1-cashier", "payment", order=pk, amount_cents=created["total_cents"], idempotency_key="payment-1")
    assert len(model.payments) == 1
    expect("w1-fulfillment", "delivery", order=pk)
    assert expect("w1-customer", "order_status", order=pk)["order"]["status"] == "delivered"
    expect("w1-fulfillment", "delivery", "conflict", order=pk)
    expect("w1-manager", "revoke_view", order=pk, grantee="external-auditor")
    expect("external-auditor", "order_status", "not_found", order=pk)
    expect("w1-sales", "new_order", "invalid", customer="w1-d1-c1", lines=[(1, 2), (-1, 1)])
    expect("w1-sales", "new_order", "conflict", customer="w1-d1-c1", lines=[(1, 1000)])
    expect("unknown", "order_status", "unauthenticated", order=pk)
    expect("w1-fulfillment", "stock_level")
    # Two distinct principals contend for the last unit, under the designed lock.
    # This is a consistency check, not a throughput or database concurrency test.
    model.stock[(1, 3)] = 1
    model.actors["w1-sales-second"] = {"id": "w1-sales-second", "role": "sales", "warehouse": 1}
    barrier = threading.Barrier(2)

    def contender(actor):
        barrier.wait(timeout=5)
        return model.call(actor, "new_order", customer="w1-d1-c1", lines=[(3, 1)])

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(contender, ["w1-sales", "w1-sales-second"]))
    assert sorted(r["outcome"] for r in results) == ["conflict", "ok"]
    assert model.stock[(1, 3)] == 0
    assert len(model.orders) == 2
    assert all(q >= 0 for q in model.stock.values())
    checks.append({"case": "two_principals_last_unit", "passed": True,
                   "outcomes": sorted(r["outcome"] for r in results)})
    return {"status": "passed", "origin": "designed_in_memory_simulation",
            "application_observed": False, "rea_reconstruction_validated": False,
            "concurrency_fixture": {"stock_override": {"warehouse": 1, "item": 3, "quantity": 1},
                                    "additional_actor": model.actors["w1-sales-second"], "barrier_parties": 2},
            "checks": checks, "events": model.events, "final_state": model.state()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    data = generate(args.seed)
    encoded = json.dumps(data, sort_keys=True, indent=2) + "\n"
    (args.output / "dataset.json").write_text(encoded)
    result = simulate(data)
    result["dataset_sha256"] = hashlib.sha256(encoded.encode()).hexdigest()
    (args.output / "simulation.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"status": result["status"], "events": len(result["events"]),
                      "checks": len(result["checks"]), "output": str(args.output)}))


if __name__ == "__main__":
    main()
