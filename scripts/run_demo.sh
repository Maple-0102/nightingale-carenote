#!/bin/sh
set -eu
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_DIR=$(dirname "$SCRIPT_DIR")
cd "$PROJECT_DIR"
export HOST="${HOST:-127.0.0.1}"
export PORT="${PORT:-8000}"
export CARENOTE_DB="${CARENOTE_DB:-$PROJECT_DIR/data/carenote.db}"
exec python3 server.py
