import os
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
CHECK_SCRIPT = ROOT / "deploy" / "check-production-config.py"


def production_env():
    env = os.environ.copy()
    env.update(
        {
            "SECRET_KEY": "9qz0SPXxw48G7hD2XvYrK8fUQm3Lp6Nc1Tj5AaEwZs4Bd7Hi2Ko9RuVx0Fg3CePn",
            "DEBUG": "false",
            "DEV_AUTH_BYPASS": "false",
            "BACKUP_AGE_PUBLIC_KEY": "age1example",
            "BACKUP_OFFLINE_PATH": "/mnt/offline/math-question-bank",
        }
    )
    return env


def run_production_check(env):
    return subprocess.run(
        [sys.executable, str(CHECK_SCRIPT)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("DEBUG", "true", "DEBUG must be false"),
        ("DEV_AUTH_BYPASS", "true", "DEV_AUTH_BYPASS must be false"),
        ("SECRET_KEY", "", "SECRET_KEY must contain at least 50 characters"),
        ("BACKUP_AGE_PUBLIC_KEY", "", "BACKUP_AGE_PUBLIC_KEY is required"),
        ("BACKUP_OFFLINE_PATH", "", "BACKUP_OFFLINE_PATH is required"),
    ],
)
def test_production_check_rejects_unsafe_or_missing_values(name, value, message):
    env = production_env()
    env[name] = value

    result = run_production_check(env)

    assert result.returncode != 0
    assert message in result.stderr


def test_production_check_accepts_required_values():
    result = run_production_check(production_env())

    assert result.returncode == 0
    assert "production configuration is valid" in result.stdout


def test_gunicorn_service_runs_preflight_and_one_worker():
    service = (ROOT / "deploy" / "gunicorn.service").read_text(encoding="utf-8")

    assert "User=mathvault" in service
    assert "EnvironmentFile=/etc/math-question-bank.env" in service
    assert "ExecStartPre=" in service and "check-production-config.py" in service
    assert "--workers 1" in service
    assert "config.wsgi:application" in service


def test_nginx_protects_all_locations_and_limits_uploads():
    config = (ROOT / "deploy" / "nginx.conf").read_text(encoding="utf-8")

    server_block = config.split("server {", 1)[1]
    assert "auth_basic " in server_block
    assert "auth_basic_user_file /etc/nginx/.htpasswd-math-question-bank;" in server_block
    assert "client_max_body_size 10M;" in server_block
    assert "location /static/" in server_block
    assert "location /media/" in server_block
    assert "location /" in server_block
    assert "proxy_set_header X-Forwarded-Proto $scheme;" in server_block


def test_backup_units_pause_writes_and_run_daily():
    service = (ROOT / "deploy" / "backup.service").read_text(encoding="utf-8")
    timer = (ROOT / "deploy" / "backup.timer").read_text(encoding="utf-8")

    assert "systemctl stop math-question-bank.service" in service
    assert "manage.py backup_bundle" in service
    assert "systemctl start math-question-bank.service" in service
    assert "OnCalendar=daily" in timer
    assert "Persistent=true" in timer


def test_example_environment_documents_production_security_and_backups():
    example = (ROOT / ".env.example").read_text(encoding="utf-8")

    for name in (
        "DEV_AUTH_BYPASS",
        "BACKUP_AGE_PUBLIC_KEY",
        "BACKUP_OFFLINE_PATH",
        "CSRF_COOKIE_SECURE",
        "SESSION_COOKIE_SECURE",
        "SECURE_SSL_REDIRECT",
    ):
        assert f"{name}=" in example
