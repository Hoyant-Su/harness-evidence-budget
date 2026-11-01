#!/usr/bin/env bash
set -eu
harness_repo="$1"
endpoint="$2"
export PYTHONPATH="$harness_repo/scripts"
python collect.py --harness-repo "$harness_repo" --output sources --pins manifest.json
python prepare.py --harness-repo "$harness_repo" --sources sources --output bundles
python run.py --harness-repo "$harness_repo" --bundles bundles --output rerun-results --endpoint "$endpoint" --model contextecho-qwen3.5-2b
python evaluate.py --harness-repo "$harness_repo" --bundles bundles --results rerun-results
