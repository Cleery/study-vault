import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection


def _sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_checksum_manifest(root):
    root = Path(root)
    files = {
        path.relative_to(root).as_posix(): _sha256(path)
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.name != "manifest.json"
    }
    return {
        "version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "files": files,
    }


def _sqlite_snapshot(source, destination):
    connection.close()
    with sqlite3.connect(source) as source_db, sqlite3.connect(destination) as target_db:
        source_db.execute("PRAGMA busy_timeout = 30000")
        source_db.backup(target_db)
        result = target_db.execute("PRAGMA integrity_check").fetchone()[0]
        if result != "ok":
            raise CommandError(f"SQLite 完整性检查失败: {result}")


class Command(BaseCommand):
    help = "创建 SQLite、媒体文件和校验清单组成的 age 加密备份"

    def add_arguments(self, parser):
        parser.add_argument("--output-dir", default=str(Path(settings.BASE_DIR) / "backups"))
        parser.add_argument("--keep", type=int, default=14)

    def handle(self, *args, **options):
        public_key = os.environ.get("BACKUP_AGE_PUBLIC_KEY", "").strip()
        offline_path = os.environ.get("BACKUP_OFFLINE_PATH", "").strip()
        age_cli = os.environ.get("BACKUP_AGE_CLI", "age").strip() or "age"
        if not public_key:
            raise CommandError("缺少 BACKUP_AGE_PUBLIC_KEY")
        if not offline_path:
            raise CommandError("缺少 BACKUP_OFFLINE_PATH")
        if options["keep"] < 1:
            raise CommandError("--keep 必须至少为 1")
        database = Path(settings.DATABASES["default"]["NAME"])
        if settings.DATABASES["default"]["ENGINE"] != "django.db.backends.sqlite3":
            raise CommandError("backup_bundle 仅支持 SQLite")
        output_dir = Path(options["output_dir"])
        offline_dir = Path(offline_path)
        output_dir.mkdir(parents=True, exist_ok=True)
        offline_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
        name = f"question-bank-{stamp}"
        encrypted = output_dir / f"{name}.tar.gz.age"
        recovery_manifest = output_dir / f"{name}.recovery.json"
        try:
            with tempfile.TemporaryDirectory(prefix="question-bank-backup-") as temporary:
                temporary = Path(temporary)
                snapshot = temporary / name
                snapshot.mkdir()
                _sqlite_snapshot(database, snapshot / "db.sqlite3")
                media = Path(settings.MEDIA_ROOT)
                if media.exists():
                    shutil.copytree(media, snapshot / "media")
                else:
                    (snapshot / "media").mkdir()
                checksum_manifest = build_checksum_manifest(snapshot)
                (snapshot / "manifest.json").write_text(json.dumps(checksum_manifest, indent=2), encoding="utf-8")
                archive = temporary / f"{name}.tar.gz"
                with tarfile.open(archive, "w:gz") as target:
                    target.add(snapshot, arcname=name)
                subprocess.run([age_cli, "--recipient", public_key, "--output", str(encrypted), str(archive)], check=True, capture_output=True, text=True)
            recovery = {
                "version": 1,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "archive": encrypted.name,
                "archive_sha256": _sha256(encrypted),
                "identity_env": "BACKUP_AGE_IDENTITY_FILE",
            }
            recovery_manifest.write_text(json.dumps(recovery, indent=2), encoding="utf-8")
            shutil.copy2(encrypted, offline_dir / encrypted.name)
            shutil.copy2(recovery_manifest, offline_dir / recovery_manifest.name)
        except FileNotFoundError as exc:
            raise CommandError(f"age 命令不可用: {age_cli}") from exc
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or exc.stdout or str(exc)).strip()
            raise CommandError(f"age 加密失败: {detail}") from exc
        except OSError as exc:
            raise CommandError(f"备份写入失败: {exc}") from exc
        for directory in (output_dir, offline_dir):
            archives = sorted(directory.glob("question-bank-*.tar.gz.age"), key=lambda path: path.stat().st_mtime, reverse=True)
            for old in archives[options["keep"]:]:
                old.unlink()
                old.with_name(old.name.removesuffix(".tar.gz.age") + ".recovery.json").unlink(missing_ok=True)
        self.stdout.write(self.style.SUCCESS(str(encrypted)))
