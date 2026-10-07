import json
import zipfile
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path

import pytest
from django.core.exceptions import ValidationError
from django.core import management
from django.core.management.base import CommandError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError
from django.utils import timezone
from PIL import Image

from question_bank.exporting import export_bundle, import_bundle
from question_bank.models import (
    KnowledgeCard,
    Question,
    QuestionAttachment,
    ReviewRecord,
    Section,
    Subject,
    Tag,
    KnowledgeCardPrerequisite,
)
from question_bank.stats import get_statistics
from question_bank.management.commands.backup_bundle import build_checksum_manifest
from question_bank.management.commands import backup_bundle as backup_module


@pytest.fixture
def subject(db):
    return Subject.objects.create(name="数学分析")


@pytest.fixture
def section(subject):
    return Section.objects.create(subject=subject, name="极限")


def make_question(subject, title, **kwargs):
    return Question.objects.create(subject=subject, title=title, draft=False, **kwargs)


def png_bytes():
    stream = BytesIO()
    Image.new("RGB", (3, 3), "white").save(stream, format="PNG")
    return stream.getvalue()


@pytest.mark.django_db
def test_statistics_has_six_fixed_metrics_and_zero_empty_state():
    stats = get_statistics(now=timezone.now())
    assert stats == {
        "question_total": 0,
        "questions_by_subject": {},
        "questions_by_mastery": {key: 0 for key, _ in Question.MASTERY_CHOICES},
        "reviews_last_30_days": 0,
        "errors_last_30_days": 0,
        "due_questions": 0,
    }


@pytest.mark.django_db
def test_statistics_counts_subject_mastery_reviews_errors_and_due(subject, section):
    now = timezone.now()
    mastered = make_question(subject, "已掌握", mastery="mastered", next_review_at=now - timedelta(minutes=1))
    make_question(subject, "未开始")
    ReviewRecord.objects.create(
        question=mastered,
        reviewed_at=now - timedelta(days=2),
        result="not_done",
        mastery_before="mastered",
        mastery_after="unstable",
    )
    ReviewRecord.objects.create(
        question=mastered,
        reviewed_at=now - timedelta(days=40),
        result="hinted",
        mastery_before="mastered",
        mastery_after="unstable",
    )
    stats = get_statistics(now=now)
    assert stats["question_total"] == 2
    assert stats["questions_by_subject"] == {"数学分析": 2}
    assert stats["questions_by_mastery"]["mastered"] == 1
    assert stats["reviews_last_30_days"] == 1
    assert stats["errors_last_30_days"] == 1
    assert stats["due_questions"] == 1


@pytest.mark.django_db
def test_export_import_round_trip_preserves_relationships_and_attachments(tmp_path, subject, section):
    card = KnowledgeCard.objects.create(
        name="极限定义", subject=subject, section=section, type="definition",
        core_content="## 自定义结构\n\n$\\varepsilon$ 定义",
    )
    tag = Tag.objects.create(name="证明")
    question = make_question(subject, "导出题", section=section)
    question.tags.add(tag)
    question.knowledge_cards.add(card)
    KnowledgeCardPrerequisite.objects.create(knowledge_card=card, prerequisite=KnowledgeCard.objects.create(name="实数完备性", subject=subject, type="theorem"))
    attachment = QuestionAttachment.objects.create(
        question=question,
        file=SimpleUploadedFile("figure.png", png_bytes()),
        file_kind="image",
        sort_order=0,
    )
    record = ReviewRecord.objects.create(
        question=question,
        reviewed_at=timezone.now(),
        result="independent",
        mastery_before="unstarted",
        mastery_after="unstable",
    )
    bundle = tmp_path / "bundle.zip"
    export_bundle(bundle)
    with zipfile.ZipFile(bundle) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["version"] == 1
        assert manifest["entities"]["questions"][0]["id"]
        assert manifest["entities"]["attachments"][0]["path"].startswith("attachments/")

    import_bundle(bundle)
    imported = Question.objects.get(title="导出题")
    assert imported.subject.name == "数学分析"
    assert imported.section.name == "极限"
    assert imported.tags.get().name == "证明"
    assert imported.knowledge_cards.get().name == "极限定义"
    assert imported.knowledge_cards.get().core_content == "## 自定义结构\n\n$\\varepsilon$ 定义"
    assert imported.attachments.count() == 1
    assert imported.attachments.get().file.read() == png_bytes()
    assert imported.review_records.count() == 1


@pytest.mark.django_db
def test_export_fails_when_attachment_file_is_missing(tmp_path, settings, subject):
    settings.MEDIA_ROOT = tmp_path / "media"
    question = make_question(subject, "附件丢失")
    attachment = QuestionAttachment.objects.create(
        question=question,
        file=SimpleUploadedFile("missing.png", b"image"),
        sort_order=0,
    )
    Path(attachment.file.path).unlink()

    with pytest.raises(ValueError, match="附件文件不存在"):
        export_bundle(tmp_path / "bundle.zip")


@pytest.mark.django_db(transaction=True)
def test_import_failure_removes_media_written_before_rollback(tmp_path, settings, subject):
    settings.MEDIA_ROOT = tmp_path / "media"
    question = make_question(subject, "回滚附件")
    attachment = QuestionAttachment.objects.create(
        question=question,
        file=SimpleUploadedFile("rollback.png", b"image"),
        sort_order=0,
    )
    first_card = KnowledgeCard.objects.create(name="结论", subject=subject, type="theorem")
    prerequisite = KnowledgeCard.objects.create(name="前置", subject=subject, type="definition")
    KnowledgeCardPrerequisite.objects.create(
        knowledge_card=first_card,
        prerequisite=prerequisite,
    )
    bundle = tmp_path / "bundle.zip"
    export_bundle(bundle)

    with zipfile.ZipFile(bundle) as source:
        manifest = json.loads(source.read("manifest.json"))
    edge = manifest["entities"]["knowledge_card_prerequisites"][0]
    edge["prerequisite_id"] = edge["knowledge_card_id"]
    invalid_bundle = tmp_path / "invalid-after-attachment.zip"
    with zipfile.ZipFile(bundle) as source, zipfile.ZipFile(invalid_bundle, "w") as target:
        for item in source.infolist():
            payload = json.dumps(manifest) if item.filename == "manifest.json" else source.read(item)
            target.writestr(item, payload)

    attachment.delete()
    assert not any(settings.MEDIA_ROOT.rglob("*.png"))

    with pytest.raises(ValidationError):
        import_bundle(invalid_bundle)

    assert QuestionAttachment.objects.count() == 0
    assert not any(settings.MEDIA_ROOT.rglob("*.png"))


@pytest.mark.django_db(transaction=True)
def test_import_rejects_an_attachment_that_is_not_a_valid_image(tmp_path, settings, subject):
    settings.MEDIA_ROOT = tmp_path / "media"
    question = make_question(subject, "伪造附件")
    attachment = QuestionAttachment.objects.create(
        question=question,
        file=SimpleUploadedFile("forged.png", png_bytes()),
        sort_order=0,
    )
    bundle = tmp_path / "bundle.zip"
    export_bundle(bundle)

    invalid_bundle = tmp_path / "invalid-image.zip"
    with zipfile.ZipFile(bundle) as source, zipfile.ZipFile(invalid_bundle, "w") as target:
        for item in source.infolist():
            payload = b"<svg onload='alert(1)'></svg>" if item.filename.endswith(".png") else source.read(item)
            target.writestr(item, payload)

    attachment.delete()
    with pytest.raises(ValidationError, match="有效图片"):
        import_bundle(invalid_bundle)

    assert QuestionAttachment.objects.count() == 0
    assert not any(settings.MEDIA_ROOT.rglob("*.png"))


@pytest.mark.django_db
def test_import_duplicate_id_updates_existing_question(tmp_path, subject):
    question = make_question(subject, "旧标题")
    bundle = tmp_path / "bundle.zip"
    export_bundle(bundle)
    with zipfile.ZipFile(bundle) as source:
        manifest = json.loads(source.read("manifest.json"))
    manifest["entities"]["questions"][0]["title"] = "新标题"
    replacement = tmp_path / "replacement.zip"
    with zipfile.ZipFile(bundle) as source, zipfile.ZipFile(replacement, "w") as target:
        for item in source.infolist():
            target.writestr(item, json.dumps(manifest) if item.filename == "manifest.json" else source.read(item))
    import_bundle(replacement)
    question.refresh_from_db()
    assert question.title == "新标题"
    assert Question.objects.count() == 1


@pytest.mark.django_db
def test_invalid_bundle_rolls_back_all_changes(tmp_path, subject):
    bundle = tmp_path / "invalid.zip"
    with zipfile.ZipFile(bundle, "w") as archive:
        archive.writestr("manifest.json", json.dumps({"version": 999, "entities": {}}))
    with pytest.raises(ValueError):
        import_bundle(bundle)
    assert Subject.objects.count() == 1
    assert Question.objects.count() == 0


@pytest.mark.django_db
def test_management_commands_export_and_import(tmp_path, subject):
    make_question(subject, "命令题")
    bundle = tmp_path / "command.zip"
    management.call_command("export_bundle", output=str(bundle), verbosity=0)
    assert bundle.exists()
    management.call_command("import_bundle", input=str(bundle), verbosity=0)
    assert Question.objects.filter(title="命令题").count() == 1


def test_backup_checksum_manifest_is_relative_and_verifiable(tmp_path):
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    (snapshot / "db.sqlite3").write_bytes(b"database")
    media = snapshot / "media"
    media.mkdir()
    (media / "figure.png").write_bytes(b"image")
    manifest = build_checksum_manifest(snapshot)
    assert set(manifest["files"]) == {"db.sqlite3", "media/figure.png"}
    assert all(len(digest) == 64 for digest in manifest["files"].values())


@pytest.mark.django_db
def test_stats_page_is_accessible_and_due_includes_later_today(client, subject, monkeypatch):
    now = timezone.make_aware(datetime(2026, 10, 8, 12, 0))
    monkeypatch.setattr("question_bank.stats.timezone.now", lambda: now)
    make_question(subject, "今天稍后到期", next_review_at=now + timedelta(minutes=30))
    response = client.get("/stats/")
    assert response.status_code == 200
    assert response.context["stats"]["due_questions"] == 1


@pytest.mark.django_db
def test_stats_workbench_metric_values_match_existing_statistics(client, subject):
    now = timezone.now()
    question = make_question(
        subject, "统计指标题", mastery="unstable", next_review_at=now - timedelta(days=1)
    )
    ReviewRecord.objects.create(
        question=question,
        reviewed_at=now - timedelta(days=1),
        result="not_done",
        mastery_before="unstable",
        mastery_after="struggling",
    )

    response = client.get("/stats/")
    body = response.content.decode()
    stats = response.context["stats"]

    assert stats["question_total"] == 1
    assert stats["due_questions"] == 1
    assert stats["reviews_last_30_days"] == 1
    assert stats["errors_last_30_days"] == 1
    for key in (
        "question_total", "due_questions", "reviews_last_30_days", "errors_last_30_days"
    ):
        assert f'data-metric="{key}">{stats[key]}<' in body


def test_backup_command_fails_when_encryption_key_is_missing(monkeypatch, tmp_path):
    monkeypatch.delenv("BACKUP_AGE_PUBLIC_KEY", raising=False)
    monkeypatch.setenv("BACKUP_OFFLINE_PATH", str(tmp_path / "offline"))
    with pytest.raises(CommandError, match="BACKUP_AGE_PUBLIC_KEY"):
        management.call_command("backup_bundle", output_dir=str(tmp_path / "backups"), verbosity=0)


def test_backup_command_reports_offline_copy_failure(monkeypatch, tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path / "media"
    settings.MEDIA_ROOT.mkdir()
    monkeypatch.setenv("BACKUP_AGE_PUBLIC_KEY", "age1test")
    monkeypatch.setenv("BACKUP_OFFLINE_PATH", str(tmp_path / "offline"))
    monkeypatch.setattr(backup_module, "_sqlite_snapshot", lambda source, destination: Path(destination).write_bytes(b"db"))

    def fake_age(arguments, **kwargs):
        Path(arguments[4]).write_bytes(b"encrypted")

    monkeypatch.setattr(backup_module.subprocess, "run", fake_age)
    monkeypatch.setattr(backup_module.shutil, "copy2", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("offline unavailable")))
    with pytest.raises(CommandError, match="offline unavailable"):
        management.call_command("backup_bundle", output_dir=str(tmp_path / "backups"), verbosity=0)


@pytest.mark.django_db(transaction=True)
def test_attachment_file_is_removed_only_after_last_reference_is_deleted(tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path / "media"
    subject = Subject.objects.create(name="文件清理科目")
    first_question = make_question(subject, "第一题")
    second_question = make_question(subject, "第二题")
    first = QuestionAttachment.objects.create(
        question=first_question,
        file=SimpleUploadedFile("shared.png", b"shared-image"),
        sort_order=0,
    )
    shared_name = first.file.name
    shared_path = Path(settings.MEDIA_ROOT) / shared_name
    second = QuestionAttachment.objects.create(
        question=second_question,
        file=shared_name,
        sort_order=0,
    )
    first.delete()
    assert shared_path.exists()
    second.delete()
    assert not shared_path.exists()
