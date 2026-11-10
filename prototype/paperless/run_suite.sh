#!/bin/sh
set -eu
mkdir -p /state/data/log /state/media /state/consume /state/scratch /results/environment
cp /opt/reversedb-env/* /results/environment/
python -m pytest -c /app/pyproject.toml -o addopts='' -p no:cacheprovider \
  --randomly-seed=42 --junitxml=/results/upstream.xml \
  /app/src/documents/tests/test_tag_hierarchy.py \
  /app/src/documents/tests/test_permission_filtering_security.py
python -m pytest -c /app/pyproject.toml -o addopts='' -p no:cacheprovider \
  --randomly-seed=42 --junitxml=/results/trace.xml /harness/test_b0_trace.py
