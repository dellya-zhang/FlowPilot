#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
npm --prefix client ci --no-audit --no-fund
npm --prefix client run build
mkdir -p artifacts
