#!/usr/bin/env python3
import os
import sys


TRUE_VALUES = {"1", "true", "yes", "on"}


def is_true(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in TRUE_VALUES


def main():
    errors = []
    if is_true("DEBUG", default=True):
        errors.append("DEBUG must be false")
    if is_true("DEV_AUTH_BYPASS"):
        errors.append("DEV_AUTH_BYPASS must be false")
    if not os.environ.get("BACKUP_AGE_PUBLIC_KEY", "").strip():
        errors.append("BACKUP_AGE_PUBLIC_KEY is required")
    if not os.environ.get("BACKUP_OFFLINE_PATH", "").strip():
        errors.append("BACKUP_OFFLINE_PATH is required")

    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1

    print("production configuration is valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
