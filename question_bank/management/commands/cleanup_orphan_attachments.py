"""Report or remove old attachment files that have no database reference."""

from datetime import timedelta
from pathlib import Path
from pathlib import PurePosixPath

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from question_bank.models import QuestionAttachment


class Command(BaseCommand):
    help = "列出未引用的旧附件文件；使用 --delete 才删除"

    def add_arguments(self, parser):
        parser.add_argument("--delete", action="store_true")
        parser.add_argument("--older-than-hours", type=int, default=24)

    def handle(self, *args, **options):
        hours = options["older_than_hours"]
        if hours < 0:
            raise CommandError("--older-than-hours 不能为负数")
        storage = QuestionAttachment._meta.get_field("file").storage
        cutoff = timezone.now() - timedelta(hours=hours)
        root = "questions"
        pending = [root]
        visited = set()
        while pending:
            directory = pending.pop()
            if directory in visited:
                continue
            visited.add(directory)
            if not self._within_storage_root(storage, directory, root):
                continue
            try:
                subdirectories, files = storage.listdir(directory)
            except FileNotFoundError:
                continue
            for name in subdirectories:
                child = self._safe_child(directory, name)
                if child and self._within_storage_root(storage, child, root):
                    pending.append(child)
            for name in files:
                candidate = self._safe_child(directory, name)
                if not candidate or not self._within_storage_root(storage, candidate, root):
                    continue
                if storage.get_modified_time(candidate) > cutoff:
                    continue
                if QuestionAttachment.objects.filter(file=candidate).exists():
                    continue
                self.stdout.write(candidate)
                if options["delete"]:
                    storage.delete(candidate)

    @staticmethod
    def _safe_child(directory, name):
        path = PurePosixPath(name)
        if (
            name in {"", ".", ".."}
            or "/" in name
            or "\\" in name
            or path.is_absolute()
            or len(path.parts) != 1
        ):
            return None
        return f"{directory}/{name}"

    @staticmethod
    def _within_storage_root(storage, name, root):
        if name != root and not name.startswith(f"{root}/"):
            return False
        try:
            storage_root = storage.path("")
            candidate = storage.path(name)
        except NotImplementedError:
            return True
        candidate_path = Path(candidate)
        if candidate_path.is_symlink() or getattr(candidate_path, "is_junction", lambda: False)():
            return False
        return candidate_path.resolve().is_relative_to(Path(storage_root).resolve())
