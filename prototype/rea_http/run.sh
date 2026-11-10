#!/bin/sh
set -eu
mkdir -p /state/data/log /state/media /state/consume /state/scratch
export PYTHONPATH=/http-harness:/app/src:/harness
python -m pytest -c /app/pyproject.toml -o addopts='' -p no:cacheprovider \
  --randomly-seed=42 --junitxml=/results/http.xml \
  /http-harness/test_http_trace.py /http-harness/test_b1_permissions.py
python -m pytest -c /app/pyproject.toml -o addopts='' -p no:cacheprovider \
  -p b1_plugin --b1-tags --randomly-seed=42 --junitxml=/results/b1-upstream.xml \
  /app/src/documents/tests/test_tag_hierarchy.py \
  /app/src/documents/tests/test_permission_filtering_security.py
