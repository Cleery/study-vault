import logging
import os
import subprocess
from datetime import timedelta

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.http import QueryDict
from django.utils import timezone

from question_bank.attachments import (
    AttachmentPlan,
    AttachmentPlanValidationError,
    ExistingAttachment,
    NewUpload,
    apply_attachment_plan,
    cleanup_unreferenced_files,
    parse_attachment_plan,
)
from question_bank.models import Question, QuestionAttachment


@pytest.fixture
def question(db):
    return Question.objects.create()


@pytest.fixture
def attachments(question):
    return (
        QuestionAttachment.objects.create(question=question, file="first.png", sort_order=0),
        QuestionAttachment.objects.create(question=question, file="second.png", sort_order=1),
    )


def post_data(**fields):
    post = QueryDict(mutable=True)
    for key, values in fields.items():
        post.setlist(key, values if isinstance(values, list) else [values])
    return post


@pytest.mark.django_db
def test_enhanced_order_interleaves_existing_and_uploaded_files(question, attachments):
    first, second = attachments
    post = post_data(attachment_order=[f"existing:{second.pk}", "new:1", f"existing:{first.pk}", "new:0"])

    plan = parse_attachment_plan(post, [object(), object()], question)

    assert plan == AttachmentPlan(
        (ExistingAttachment(second.pk), NewUpload(1), ExistingAttachment(first.pk), NewUpload(0)),
        frozenset(),
    )


@pytest.mark.django_db
def test_enhanced_order_can_remove_existing_attachment(question, attachments):
    first, second = attachments
    post = post_data(attachment_order=[f"existing:{second.pk}", "new:0"], removed_attachment=[str(first.pk)])

    plan = parse_attachment_plan(post, [object()], question)

    assert plan == AttachmentPlan((ExistingAttachment(second.pk), NewUpload(0)), frozenset({first.pk}))


@pytest.mark.django_db
@pytest.mark.parametrize("tokens", [["new:0", "new:0"], ["existing:1", "existing:1"]])
def test_enhanced_order_rejects_duplicate_tokens(question, tokens):
    if tokens[0].startswith("existing:"):
        item = QuestionAttachment.objects.create(question=question, file="a.png")
        tokens = [f"existing:{item.pk}", f"existing:{item.pk}"]
        uploads = []
    else:
        uploads = [object()]
    with pytest.raises(AttachmentPlanValidationError):
        parse_attachment_plan(post_data(attachment_order=tokens), uploads, question)


@pytest.mark.django_db
def test_enhanced_order_rejects_unknown_existing_id(question):
    with pytest.raises(AttachmentPlanValidationError):
        parse_attachment_plan(post_data(attachment_order=["existing:999999"]), [], question)


@pytest.mark.django_db
def test_enhanced_order_rejects_foreign_attachment(question):
    foreign = QuestionAttachment.objects.create(question=Question.objects.create(), file="foreign.png")
    with pytest.raises(AttachmentPlanValidationError):
        parse_attachment_plan(post_data(attachment_order=[f"existing:{foreign.pk}"]), [], question)


@pytest.mark.django_db
def test_enhanced_order_rejects_omitted_survivor(question, attachments):
    first, _ = attachments
    with pytest.raises(AttachmentPlanValidationError):
        parse_attachment_plan(post_data(attachment_order=[f"existing:{first.pk}"]), [], question)


@pytest.mark.django_db
def test_enhanced_order_rejects_omitted_upload(question):
    with pytest.raises(AttachmentPlanValidationError):
        parse_attachment_plan(post_data(attachment_order=["new:0"]), [object(), object()], question)


@pytest.mark.django_db
def test_enhanced_order_rejects_removed_item_in_order(question, attachments):
    first, second = attachments
    with pytest.raises(AttachmentPlanValidationError):
        parse_attachment_plan(
            post_data(attachment_order=[f"existing:{first.pk}", f"existing:{second.pk}"], removed_attachment=[str(first.pk)]),
            [], question,
        )


@pytest.mark.django_db
def test_enhanced_order_rejects_out_of_range_upload_index(question):
    with pytest.raises(AttachmentPlanValidationError):
        parse_attachment_plan(post_data(attachment_order=["new:1"]), [object()], question)


@pytest.mark.django_db
@pytest.mark.parametrize("token", ["new0", "other:0", "new:-1", "new:abc"])
def test_enhanced_order_rejects_malformed_token(question, token):
    with pytest.raises(AttachmentPlanValidationError, match="附件顺序标记无效"):
        parse_attachment_plan(post_data(attachment_order=[token]), [object()], question)


@pytest.mark.django_db
def test_enhanced_order_reports_oversized_numeric_token_as_validation_error(question):
    with pytest.raises(AttachmentPlanValidationError):
        parse_attachment_plan(post_data(attachment_order=["new:" + "9" * 5000]), [], question)


@pytest.mark.django_db
@pytest.mark.parametrize("mixed", [{"remove_attachment": ["1"]}, {"attachment_position_1": ["0"]}])
def test_enhanced_order_rejects_fallback_fields(question, mixed):
    post = post_data(attachment_order=["new:0"], **mixed)
    with pytest.raises(AttachmentPlanValidationError):
        parse_attachment_plan(post, [object()], question)


@pytest.mark.django_db
def test_enhanced_order_rejects_foreign_deletion(question):
    foreign = QuestionAttachment.objects.create(question=Question.objects.create(), file="foreign.png")
    with pytest.raises(AttachmentPlanValidationError):
        parse_attachment_plan(
            QueryDict(f"attachment_protocol=enhanced&removed_attachment={foreign.pk}"),
            [], question,
        )


@pytest.mark.django_db
def test_enhanced_protocol_can_remove_every_existing_attachment(question, attachments):
    first, second = attachments
    post = QueryDict(
        f"attachment_protocol=enhanced&removed_attachment={first.pk}"
        f"&removed_attachment={second.pk}"
    )

    plan = parse_attachment_plan(post, [], question)

    assert plan == AttachmentPlan((), frozenset({first.pk, second.pk}))


@pytest.mark.django_db
def test_enhanced_protocol_accepts_empty_new_question():
    plan = parse_attachment_plan(QueryDict("attachment_protocol=enhanced"), [], Question())
    assert plan == AttachmentPlan((), frozenset())


@pytest.mark.django_db
@pytest.mark.parametrize("has_order", [False, True])
def test_unknown_attachment_protocol_is_rejected(question, has_order):
    query = "attachment_protocol=unknown"
    if has_order:
        query += "&attachment_order=new%3A0"
    with pytest.raises(AttachmentPlanValidationError, match="附件排序协议无效"):
        parse_attachment_plan(QueryDict(query), [object()] if has_order else [], question)


@pytest.mark.django_db
@pytest.mark.parametrize("fallback_field", ["remove", "position"])
def test_enhanced_marker_rejects_fallback_fields_without_order(question, attachments, fallback_field):
    first, second = attachments
    if fallback_field == "remove":
        query = (
            f"attachment_protocol=enhanced&remove_attachment={first.pk}"
            f"&attachment_position_{second.pk}=0"
        )
    else:
        query = (
            f"attachment_protocol=enhanced&attachment_position_{first.pk}=0"
            f"&attachment_position_{second.pk}=1"
        )
    with pytest.raises(AttachmentPlanValidationError, match="附件排序协议不能混用"):
        parse_attachment_plan(QueryDict(query), [], question)


@pytest.mark.django_db
@pytest.mark.parametrize("removed_value", ["duplicate", "abc", "-1", "", "999999"])
def test_enhanced_order_rejects_duplicate_or_invalid_deletion(question, attachments, removed_value):
    first, second = attachments
    removed = [str(first.pk), str(first.pk)] if removed_value == "duplicate" else [removed_value]
    post = post_data(
        attachment_order=[f"existing:{second.pk}"], removed_attachment=removed
    )

    with pytest.raises(AttachmentPlanValidationError, match="附件删除标记无效"):
        parse_attachment_plan(post, [], question)


@pytest.mark.django_db
def test_new_question_accepts_enhanced_upload_order():
    plan = parse_attachment_plan(
        post_data(attachment_order=["new:1", "new:0"]), [object(), object()], Question()
    )
    assert plan == AttachmentPlan((NewUpload(1), NewUpload(0)), frozenset())


@pytest.mark.django_db
def test_fallback_removes_existing_and_appends_uploads(question, attachments):
    first, second = attachments
    post = post_data(remove_attachment=[str(first.pk)], **{f"attachment_position_{second.pk}": "7"})

    plan = parse_attachment_plan(post, [object(), object()], question)

    assert plan == AttachmentPlan((ExistingAttachment(second.pk), NewUpload(0), NewUpload(1)), frozenset({first.pk}))


@pytest.mark.django_db
def test_fallback_normalizes_position_gaps(question, attachments):
    first, second = attachments
    post = post_data(**{f"attachment_position_{first.pk}": "20", f"attachment_position_{second.pk}": "4"})
    plan = parse_attachment_plan(post, [], question)
    assert plan.ordered_items == (ExistingAttachment(second.pk), ExistingAttachment(first.pk))


@pytest.mark.django_db
def test_fallback_rejects_missing_position(question, attachments):
    first, _ = attachments
    with pytest.raises(AttachmentPlanValidationError):
        parse_attachment_plan(post_data(**{f"attachment_position_{first.pk}": "0"}), [], question)


@pytest.mark.django_db
def test_fallback_rejects_repeated_position_field(question, attachments):
    first, second = attachments
    post = post_data(**{
        f"attachment_position_{first.pk}": ["0", "1"],
        f"attachment_position_{second.pk}": "2",
    })

    with pytest.raises(AttachmentPlanValidationError, match="请为每个保留的附件填写位置"):
        parse_attachment_plan(post, [], question)


@pytest.mark.django_db
@pytest.mark.parametrize("positions", [("0", "0"), ("-1", "0"), ("abc", "0"), ("", "0")])
def test_fallback_rejects_duplicate_or_invalid_positions(question, attachments, positions):
    first, second = attachments
    post = post_data(**{f"attachment_position_{first.pk}": positions[0], f"attachment_position_{second.pk}": positions[1]})
    with pytest.raises(AttachmentPlanValidationError):
        parse_attachment_plan(post, [], question)


@pytest.mark.django_db
def test_fallback_rejects_enhanced_deletion_field(question):
    with pytest.raises(AttachmentPlanValidationError):
        parse_attachment_plan(post_data(removed_attachment=["1"]), [], question)


@pytest.mark.django_db
def test_fallback_rejects_foreign_deletion(question):
    foreign = QuestionAttachment.objects.create(question=Question.objects.create(), file="foreign.png")
    with pytest.raises(AttachmentPlanValidationError):
        parse_attachment_plan(post_data(remove_attachment=[str(foreign.pk)]), [], question)


@pytest.fixture
def media_root(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path / "media"
    return settings.MEDIA_ROOT


def upload(name, content=b"image bytes"):
    return SimpleUploadedFile(name, content, content_type="image/png")


@pytest.mark.django_db(transaction=True)
def test_apply_plan_interleaves_uploads_and_reorders_mixed_existing_kinds(question, media_root):
    first = QuestionAttachment.objects.create(
        question=question, file=upload("first.png"), file_kind="image", sort_order=0
    )
    second = QuestionAttachment.objects.create(
        question=question, file=upload("second.pdf"), file_kind="document", sort_order=1
    )
    third = QuestionAttachment.objects.create(
        question=question, file=upload("third.txt"), file_kind="other", sort_order=2
    )
    uploads = [upload("new-0.png"), upload("new-1.png")]
    plan = AttachmentPlan(
        (ExistingAttachment(third.pk), NewUpload(1), ExistingAttachment(first.pk),
         NewUpload(0), ExistingAttachment(second.pk)),
        frozenset(),
    )
    created_names = []

    apply_attachment_plan(question, uploads, plan, created_names)

    rows = list(question.attachments.order_by("sort_order"))
    assert [row.sort_order for row in rows] == list(range(5))
    assert [row.pk for row in rows[::2]] == [third.pk, first.pk, second.pk]
    assert [row.file_kind for row in rows] == ["other", "image", "image", "image", "document"]
    assert created_names == [rows[1].file.name, rows[3].file.name]
    assert all((media_root / name).exists() for name in created_names)


@pytest.mark.django_db(transaction=True)
def test_apply_plan_deletes_first_row_and_file_after_commit(question, media_root):
    first = QuestionAttachment.objects.create(question=question, file=upload("first.png"), sort_order=0)
    second = QuestionAttachment.objects.create(question=question, file=upload("second.png"), sort_order=1)
    first_path = media_root / first.file.name
    plan = AttachmentPlan((ExistingAttachment(second.pk),), frozenset({first.pk}))

    with transaction.atomic():
        apply_attachment_plan(question, [], plan, [])
        assert not QuestionAttachment.objects.filter(pk=first.pk).exists()
        assert first_path.exists()
        assert QuestionAttachment.objects.get(pk=second.pk).sort_order == 0

    assert not first_path.exists()
    assert QuestionAttachment.objects.get(pk=second.pk).sort_order == 0


@pytest.mark.django_db(transaction=True)
def test_apply_plan_normalizes_survivors_after_multiple_deletions(question, media_root):
    rows = [
        QuestionAttachment.objects.create(question=question, file=upload(f"{index}.png"), sort_order=index)
        for index in range(4)
    ]
    plan = AttachmentPlan(
        (ExistingAttachment(rows[3].pk), ExistingAttachment(rows[1].pk)),
        frozenset({rows[0].pk, rows[2].pk}),
    )

    apply_attachment_plan(question, [], plan, [])

    assert list(question.attachments.values_list("pk", "sort_order")) == [
        (rows[3].pk, 0), (rows[1].pk, 1)
    ]
    assert not (media_root / rows[0].file.name).exists()
    assert not (media_root / rows[2].file.name).exists()


@pytest.mark.django_db(transaction=True)
def test_failed_attachment_save_can_clean_only_new_unreferenced_file(
    question, media_root, monkeypatch
):
    existing = QuestionAttachment.objects.create(
        question=question, file=upload("existing.png"), sort_order=0
    )
    original_save = QuestionAttachment.save

    def fail_new_save(instance, *args, **kwargs):
        if instance.pk is None:
            raise RuntimeError("database save failed")
        return original_save(instance, *args, **kwargs)

    monkeypatch.setattr(QuestionAttachment, "save", fail_new_save)
    names = []
    plan = AttachmentPlan((ExistingAttachment(existing.pk), NewUpload(0)), frozenset())

    with pytest.raises(RuntimeError, match="database save failed"):
        apply_attachment_plan(question, [upload("new.png")], plan, names)

    assert len(names) == 1
    assert (media_root / names[0]).exists()
    cleanup_unreferenced_files(names, existing.file.storage)
    assert not (media_root / names[0]).exists()
    assert (media_root / existing.file.name).exists()
    assert list(question.attachments.values_list("pk", "sort_order")) == [(existing.pk, 0)]


@pytest.mark.django_db(transaction=True)
def test_cleanup_preserves_new_file_if_another_row_references_it(question, media_root):
    attachment = QuestionAttachment.objects.create(
        question=question, file=upload("referenced.png"), sort_order=0
    )

    cleanup_unreferenced_files([attachment.file.name], attachment.file.storage)

    assert (media_root / attachment.file.name).exists()


@pytest.mark.django_db(transaction=True)
def test_delete_failure_is_logged_without_escaping_commit(question, media_root, monkeypatch, caplog):
    attachment = QuestionAttachment.objects.create(
        question=question, file=upload("doomed.png"), sort_order=0
    )
    storage = attachment.file.storage

    def fail_delete(name):
        raise OSError("storage unavailable")

    monkeypatch.setattr(storage, "delete", fail_delete)
    with caplog.at_level(logging.ERROR):
        attachment.delete()

    assert "storage unavailable" in caplog.text
    assert attachment.file.name in caplog.text
    assert (media_root / attachment.file.name).exists()


@pytest.mark.django_db(transaction=True)
def test_failed_replacement_preserves_old_file_and_database_reference(question, media_root):
    attachment = QuestionAttachment.objects.create(
        question=question, file=upload("original.png"), sort_order=0
    )
    QuestionAttachment.objects.create(question=question, file=upload("other.png"), sort_order=1)
    old_name = attachment.file.name
    attachment.file.save("replacement.png", upload("replacement.png"), save=False)
    attachment.sort_order = 1

    with pytest.raises(IntegrityError):
        attachment.save()

    assert (media_root / old_name).exists()
    assert QuestionAttachment.objects.get(pk=attachment.pk).file.name == old_name


@pytest.mark.django_db(transaction=True)
def test_replacement_deletes_old_file_only_after_successful_commit(question, media_root):
    attachment = QuestionAttachment.objects.create(
        question=question, file=upload("original.png"), sort_order=0
    )
    old_name = attachment.file.name
    attachment.file.save("replacement.png", upload("replacement.png"), save=False)
    new_name = attachment.file.name

    with transaction.atomic():
        attachment.save(update_fields=["file", "updated_at"])
        assert (media_root / old_name).exists()
        assert (media_root / new_name).exists()
        assert QuestionAttachment.objects.get(pk=attachment.pk).file.name == new_name

    assert not (media_root / old_name).exists()
    assert (media_root / new_name).exists()


@pytest.mark.django_db(transaction=True)
def test_replacement_delete_failure_is_logged_after_commit(question, media_root, monkeypatch, caplog):
    attachment = QuestionAttachment.objects.create(
        question=question, file=upload("original.png"), sort_order=0
    )
    old_name = attachment.file.name
    attachment.file.save("replacement.png", upload("replacement.png"), save=False)
    storage = attachment.file.storage

    def fail_delete(name):
        raise OSError("replacement storage unavailable")

    monkeypatch.setattr(storage, "delete", fail_delete)
    with caplog.at_level(logging.ERROR):
        with transaction.atomic():
            attachment.save(update_fields=["file", "updated_at"])
            assert old_name not in caplog.text

    assert old_name in caplog.text
    assert "replacement storage unavailable" in caplog.text
    assert (media_root / old_name).exists()


@pytest.mark.django_db(transaction=True)
def test_cleanup_failure_is_logged_without_masking_original_error(question, media_root, monkeypatch, caplog):
    storage = QuestionAttachment._meta.get_field("file").storage

    def fail_delete(name):
        raise OSError("cleanup unavailable")

    monkeypatch.setattr(storage, "delete", fail_delete)
    with caplog.at_level(logging.ERROR):
        with pytest.raises(RuntimeError, match="original failure"):
            try:
                raise RuntimeError("original failure")
            finally:
                cleanup_unreferenced_files(["questions/orphan.png"], storage)

    assert "cleanup unavailable" in caplog.text
    assert "questions/orphan.png" in caplog.text


@pytest.mark.django_db(transaction=True)
def test_orphan_cleanup_command_dry_run_then_deletes_only_old_unreferenced_files(
    question, media_root, capsys
):
    attachment = QuestionAttachment.objects.create(
        question=question, file=upload("kept.png"), sort_order=0
    )
    old_orphan = media_root / "questions" / "orphan-old.png"
    recent_orphan = media_root / "questions" / "orphan-new.png"
    old_orphan.parent.mkdir(parents=True, exist_ok=True)
    old_orphan.write_bytes(b"old")
    recent_orphan.write_bytes(b"new")
    old_timestamp = (timezone.now() - timedelta(hours=48)).timestamp()
    os.utime(old_orphan, (old_timestamp, old_timestamp))

    call_command("cleanup_orphan_attachments", verbosity=0)
    assert old_orphan.exists()
    assert recent_orphan.exists()
    assert (media_root / attachment.file.name).exists()

    call_command("cleanup_orphan_attachments", "--delete", verbosity=0)
    assert not old_orphan.exists()
    assert recent_orphan.exists()
    assert (media_root / attachment.file.name).exists()


@pytest.mark.django_db(transaction=True)
def test_orphan_cleanup_does_not_follow_question_directory_link(media_root, capsys):
    questions_dir = media_root / "questions"
    outside_dir = media_root / "other"
    questions_dir.mkdir(parents=True)
    outside_dir.mkdir()
    orphan = questions_dir / "old-orphan.png"
    outside = outside_dir / "outside.png"
    orphan.write_bytes(b"orphan")
    outside.write_bytes(b"unrelated")
    old_timestamp = (timezone.now() - timedelta(hours=48)).timestamp()
    os.utime(orphan, (old_timestamp, old_timestamp))
    os.utime(outside, (old_timestamp, old_timestamp))
    link = questions_dir / "linked"
    try:
        link.symlink_to(outside_dir, target_is_directory=True)
    except OSError as exc:
        if os.name != "nt":
            pytest.skip(f"directory symlinks are unavailable: {exc}")
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(outside_dir)],
            capture_output=True,
            text=True,
        )
        if result.returncode:
            pytest.skip(f"directory links are unavailable: {result.stderr}")

    call_command("cleanup_orphan_attachments", "--delete", verbosity=0)

    assert not orphan.exists()
    assert outside.exists()
    assert "questions/linked/outside.png" not in capsys.readouterr().out
