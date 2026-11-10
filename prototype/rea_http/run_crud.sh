#!/bin/sh
set -eu
mkdir -p /state/data/log /state/media /state/consume /state/scratch
export PYTHONPATH=/http-harness:/app/src:/harness
python -m pytest -c /app/pyproject.toml -o addopts='' -p no:cacheprovider \
  --randomly-seed=42 --junitxml=/results/crud.xml /http-harness/test_tag_crud_capture.py
