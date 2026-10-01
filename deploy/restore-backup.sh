#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 2 ]; then
  echo "usage: $0 BACKUP.tar.gz.age RESTORE_DIRECTORY" >&2
  exit 64
fi
if [ -z "${BACKUP_AGE_IDENTITY_FILE:-}" ]; then
  echo "BACKUP_AGE_IDENTITY_FILE is required" >&2
  exit 65
fi
if [ ! -r "$BACKUP_AGE_IDENTITY_FILE" ]; then
  echo "age identity file is not readable" >&2
  exit 66
fi

backup=$1
restore_dir=$2
work_dir=$(mktemp -d)
trap 'rm -rf "$work_dir"' EXIT

age --decrypt --identity "$BACKUP_AGE_IDENTITY_FILE" --output "$work_dir/backup.tar.gz" "$backup"
mkdir -p "$restore_dir"
tar -xzf "$work_dir/backup.tar.gz" -C "$restore_dir" --strip-components=1

python3 - "$restore_dir" <<'PY'
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
for relative, expected in manifest["files"].items():
    path = (root / relative).resolve()
    if root not in path.parents or not path.is_file():
        raise SystemExit(f"missing or unsafe backup path: {relative}")
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != expected:
        raise SystemExit(f"checksum mismatch: {relative}")
with sqlite3.connect(root / "db.sqlite3") as database:
    result = database.execute("PRAGMA integrity_check").fetchone()[0]
if result != "ok":
    raise SystemExit(f"sqlite integrity check failed: {result}")
PY

echo "restore verified: $restore_dir"
