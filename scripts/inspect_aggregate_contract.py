#!/usr/bin/env python3
"""Print the source candidate and outstanding obligations; never import the app."""
import argparse
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "prototype/aggregate_reattach/transform_contract.py"
spec = importlib.util.spec_from_file_location("transform_contract", MODULE)
contract = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = contract
spec.loader.exec_module(contract)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT / "sources/paperless-ngx/src/documents/permissions.py")
    parser.add_argument("--function", default="annotate_document_count_by_ids")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    source = args.source.read_text()
    try:
        region = contract.recover_region(source, args.function)
    except contract.UnsupportedPattern as exc:
        result = {"source": str(args.source), "function": args.function,
                  "decision": "unsupported_pattern", "action": "keep_original", "reason": str(exc)}
    else:
        result = {"source": str(args.source), "scope": "Offline source inspection; no runtime binder attached",
                  **contract.prepare_plan(region, None, {})}
    text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text)
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
