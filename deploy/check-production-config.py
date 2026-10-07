#!/usr/bin/env python3
import os
import sys
import ipaddress
import socket
from urllib.parse import urlsplit


TRUE_VALUES = {"1", "true", "yes", "on"}


def is_true(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in TRUE_VALUES


def private_ai_target(url):
    parts = urlsplit(url)
    hostname = (parts.hostname or "").lower()
    if hostname == "localhost" or hostname.endswith(".local"):
        return True
    try:
        return not ipaddress.ip_address(hostname.split("%", 1)[0]).is_global
    except ValueError:
        try:
            addresses = socket.getaddrinfo(hostname, parts.port or 443)
        except OSError:
            return True
        return not addresses or any(
            not ipaddress.ip_address(str(entry[4][0]).split("%", 1)[0]).is_global
            for entry in addresses
        )


def positive_int(name, default):
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError:
        return None
    return value if value > 0 else None


def main():
    errors = []
    if is_true("DEBUG", default=True):
        errors.append("DEBUG must be false")
    if is_true("DEV_AUTH_BYPASS"):
        errors.append("DEV_AUTH_BYPASS must be false")
    secret_key = os.environ.get("SECRET_KEY", "").strip()
    if len(secret_key) < 50 or secret_key.startswith("django-insecure-"):
        errors.append("SECRET_KEY must contain at least 50 characters and must not use Django's insecure prefix")
    if not os.environ.get("BACKUP_AGE_PUBLIC_KEY", "").strip():
        errors.append("BACKUP_AGE_PUBLIC_KEY is required")
    if not os.environ.get("BACKUP_OFFLINE_PATH", "").strip():
        errors.append("BACKUP_OFFLINE_PATH is required")
    if is_true("AI_ENABLED") and os.environ.get("AI_PROVIDER", "relay").strip() == "relay":
        ai_base_url = os.environ.get("AI_BASE_URL", "").strip()
        parts = urlsplit(ai_base_url)
        if parts.scheme != "https" or not parts.hostname:
            errors.append("AI_BASE_URL must use HTTPS")
        elif (
            not is_true("AI_ALLOW_PRIVATE_BASE_URL")
            and private_ai_target(ai_base_url)
        ):
            errors.append("AI_BASE_URL must not target a private network")
        if not os.environ.get("AI_API_KEY", ""):
            errors.append("AI_API_KEY is required when relay AI is enabled")
        total_timeout = positive_int("AI_TOTAL_TIMEOUT_SECONDS", 90)
        if total_timeout is None or total_timeout >= 120:
            errors.append("AI_TOTAL_TIMEOUT_SECONDS must be less than 120")

    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1

    print("production configuration is valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
