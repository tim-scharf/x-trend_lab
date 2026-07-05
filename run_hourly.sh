#!/bin/bash

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python}"
LOG_FILE="$PROJECT_DIR/logs/run_loop.log"

mkdir -p "$PROJECT_DIR/logs"
mkdir -p "$PROJECT_DIR/data/runtime"
mkdir -p "$PROJECT_DIR/data/generated"
mkdir -p "$PROJECT_DIR/data/snapshots"

cd "$PROJECT_DIR"

echo "==================================================" >> "$LOG_FILE"
echo "Run started at $(date)" >> "$LOG_FILE"
"$PYTHON_BIN" scripts/run_loop.py >> "$LOG_FILE" 2>&1
echo "Run finished at $(date)" >> "$LOG_FILE"
echo "" >> "$LOG_FILE"
