#!/usr/bin/env python3
"""Extract count-mixin candidates and compare with the audited registry offline."""
import argparse
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("callsite_extraction", ROOT / "prototype/aggregate_reattach/callsite_extraction.py")
extractor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(extractor)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--audit", type=Path,
                        default=ROOT / "evidence/code-audit-fixes/20261104-13435f1b4859",
                        help="version-matched archived runtime metadata")
    args = parser.parse_args()
    source_path = ROOT / "sources/paperless-ngx/src/documents/views.py"
    registry_path = ROOT / "prototype/aggregate_reattach/tag_query_binding.py"
    source, registry = source_path.read_text(), registry_path.read_text()
    audit = args.audit
    attnames, issues = extractor.load_audited_attnames(
        audit, registry,
        (ROOT / "sources/paperless-ngx/src/documents/models.py").read_text(),
        (ROOT / "sources/paperless-ngx/src/documents/permissions.py").read_text(),
    )
    result = extractor.extract_callsites(source)
    registered = extractor.extract_registry(registry)
    result.update(source_path=str(source_path), registry_path=str(registry_path),
                  registry_source_sha256=extractor.source_hash(registry),
                  registry=registered, metadata_issues=issues,
                  comparisons=extractor.compare_to_registry(result, registered, attnames))
    text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text)
    else:
        print(text, end="")


if __name__ == "__main__":
    main()
