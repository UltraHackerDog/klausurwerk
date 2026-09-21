#!/bin/sh
# Start Klausurwerk on loopback only.
cd "$(dirname "$0")" || exit 1
exec uv run uvicorn app.main:app --host 127.0.0.1 --port 8765
