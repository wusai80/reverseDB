"""Sixteen new standalone challenge sources; no expected labels or variants.

Common wrapping supplies the frozen extractor's execute/try interface only.
All sources are newly authored, not independently collected external software.
"""
import textwrap


# id, whether the region's scalar result is discarded, independent program body
BODIES = [
    ("overwritten_receipt", False, """
        receipt = 0
        if not removed:
            receipt = x + 1
        receipt = x * 2
        return {"receipt": receipt}
    """),
    ("checksum_chain", False, """
        subtotal = 0
        if not removed:
            subtotal = x * 3
        checksum = subtotal + 7
        return {"checksum": checksum}
    """),
    ("conditional_ledger", True, """
        ledger = {"balance": 20}
        if not removed:
            if flag:
                ledger["balance"] -= x
        return {"balance": ledger["balance"]}
    """),
    ("opposite_guard", True, """
        if not removed:
            if x < 0:
                raise Rejected("DENIED")
        if x > 0:
            raise Rejected("DENIED")
        return {"accepted": True}
    """),
    ("redundant_guard", True, """
        if not removed:
            if x < 0:
                raise Rejected("DENIED")
        if x < 0:
            raise Rejected("DENIED")
        return {"accepted": True}
    """),
    ("preexisting_snapshot", True, """
        record = {"version": 4}
        snapshot = dict(record)
        if not removed:
            record["version"] += x
        return {"snapshot": snapshot["version"]}
    """),
    ("two_consumers", True, """
        inventory = {"count": 10}
        receipt = inventory
        if not removed:
            inventory["count"] += x
        return {"stock": inventory["count"], "receipt": receipt["count"]}
    """),
    ("integer_identity", True, """
        account = {"balance": x}
        if not removed:
            account["balance"] += 0
        return {"balance": account["balance"]}
    """),
    ("reset_after_assignment", False, """
        stamp = 0
        stamp = x
        if not removed:
            stamp = 0
        return {"stamp": stamp}
    """),
    ("early_return", True, """
        if not removed:
            if x < 0:
                return {"accepted": False}
        return {"accepted": True}
    """),
    ("control_dependency", True, """
        reject = False
        if not removed:
            reject = x < 0
        if reject:
            raise Rejected("REJECTED")
        return {"accepted": True}
    """),
    ("implicit_division_error", True, """
        scratch = 0
        if not removed:
            scratch = 12 // (x + 2)
        return {"accepted": True}
    """),
    ("loop_accumulator", False, """
        total = 0
        if not removed:
            for item in (x, x + 1):
                total += item
        return {"total": total}
    """),
    ("method_side_effect", True, """
        audit = []
        if not removed:
            audit.append(x)
        return {"audit": audit}
    """),
    ("finally_cleanup", True, """
        resource = {"open": True}
        try:
            if not removed:
                resource["open"] = False
        finally:
            resource["open"] = False
        return {"open": resource["open"]}
    """),
    ("postmutation_copy", True, """
        stock = {"quantity": 10}
        if not removed:
            stock["quantity"] += x
        snapshot = dict(stock)
        return {"quantity": snapshot["quantity"]}
    """),
]


def programs():
    result = []
    for name, discarded, body in BODIES:
        source = (
            "class Rejected(Exception):\n    pass\n\n"
            "def execute(x, flag, removed):\n    try:\n"
            + textwrap.indent(textwrap.dedent(body).strip(), "        ")
            + '\n    except Rejected as error:\n        return {"error": str(error)}\n'
        )
        result.append(dict(id=name, source=source, discarded_result=discarded,
                           provenance="newly-authored-challenge",
                           scalar_result_note="False for receipt/subtotal/total/stamp value-producing region; True for guards, heap effects, control-only region or unused scratch. This is not an effect-freedom claim."))
    return result
