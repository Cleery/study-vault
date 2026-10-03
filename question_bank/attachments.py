"""Validate enhanced and native attachment ordering submissions."""

import logging
import re
from dataclasses import dataclass

from django.db import transaction

from .models import QuestionAttachment


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExistingAttachment:
    pk: int


@dataclass(frozen=True)
class NewUpload:
    index: int


@dataclass(frozen=True)
class AttachmentPlan:
    ordered_items: tuple[ExistingAttachment | NewUpload, ...]
    removed_ids: frozenset[int]


class AttachmentPlanValidationError(ValueError):
    pass


def _nonnegative_int(value, message):
    if not re.fullmatch(r"[0-9]+", value):
        raise AttachmentPlanValidationError(message)
    try:
        return int(value)
    except ValueError as exc:
        raise AttachmentPlanValidationError(message) from exc


def _removed_ids(post, field, existing_ids):
    removed = []
    for value in post.getlist(field):
        pk = _nonnegative_int(value, "附件删除标记无效。")
        if pk not in existing_ids or pk in removed:
            raise AttachmentPlanValidationError("附件删除标记无效。")
        removed.append(pk)
    return frozenset(removed)


def parse_attachment_plan(post, uploads, question):
    existing_ids = set()
    if question.pk and not question._state.adding:
        existing_ids = set(
            QuestionAttachment.objects.filter(question=question).values_list("pk", flat=True)
        )

    protocol = post.getlist("attachment_protocol") if "attachment_protocol" in post else []
    if "attachment_protocol" in post and protocol != ["enhanced"]:
        raise AttachmentPlanValidationError("附件排序协议无效。")

    if protocol or "attachment_order" in post:
        if "remove_attachment" in post or any(
            key.startswith("attachment_position_") for key in post
        ):
            raise AttachmentPlanValidationError("附件排序协议不能混用。")
        removed_ids = _removed_ids(post, "removed_attachment", existing_ids)
        ordered = []
        seen = set()
        for token in post.getlist("attachment_order"):
            kind, separator, raw_id = token.partition(":")
            if not separator or kind not in {"existing", "new"}:
                raise AttachmentPlanValidationError("附件顺序标记无效。")
            index = _nonnegative_int(raw_id, "附件顺序标记无效。")
            item = ExistingAttachment(index) if kind == "existing" else NewUpload(index)
            if item in seen:
                raise AttachmentPlanValidationError("附件顺序标记重复。")
            if kind == "existing" and (index not in existing_ids or index in removed_ids):
                raise AttachmentPlanValidationError("附件顺序包含无效的已有附件。")
            if kind == "new" and index >= len(uploads):
                raise AttachmentPlanValidationError("附件顺序包含无效的上传文件。")
            seen.add(item)
            ordered.append(item)

        expected = {ExistingAttachment(pk) for pk in existing_ids - removed_ids}
        expected.update(NewUpload(index) for index in range(len(uploads)))
        if seen != expected:
            raise AttachmentPlanValidationError("附件顺序缺少文件。")
        return AttachmentPlan(tuple(ordered), removed_ids)

    if "removed_attachment" in post:
        raise AttachmentPlanValidationError("附件排序协议不能混用。")
    removed_ids = _removed_ids(post, "remove_attachment", existing_ids)
    positions = []
    seen_positions = set()
    for pk in existing_ids - removed_ids:
        field = f"attachment_position_{pk}"
        values = post.getlist(field)
        if len(values) != 1:
            raise AttachmentPlanValidationError("请为每个保留的附件填写位置。")
        position = _nonnegative_int(values[0], "附件位置必须是非负整数。")
        if position in seen_positions:
            raise AttachmentPlanValidationError("附件位置不能重复。")
        seen_positions.add(position)
        positions.append((position, pk))

    ordered = tuple(ExistingAttachment(pk) for _, pk in sorted(positions))
    ordered += tuple(NewUpload(index) for index in range(len(uploads)))
    return AttachmentPlan(ordered, removed_ids)


def apply_attachment_plan(question, uploads, plan, created_names):
    """Persist a validated plan while tracking files written before their rows exist."""
    with transaction.atomic():
        existing = {
            attachment.pk: attachment
            for attachment in QuestionAttachment.objects.select_for_update().filter(question=question)
        }
        retained_ids = {item.pk for item in plan.ordered_items if isinstance(item, ExistingAttachment)}
        upload_indexes = {item.index for item in plan.ordered_items if isinstance(item, NewUpload)}
        if (
            plan.removed_ids - existing.keys()
            or retained_ids != existing.keys() - plan.removed_ids
            or upload_indexes != set(range(len(uploads)))
            or len(plan.ordered_items) != len(existing) - len(plan.removed_ids) + len(uploads)
        ):
            raise AttachmentPlanValidationError("附件计划已失效。")

        highest_order = max((item.sort_order for item in existing.values()), default=-1)
        for offset, pk in enumerate(sorted(retained_ids), start=1):
            QuestionAttachment.objects.filter(pk=pk).update(sort_order=highest_order + offset)

        if plan.removed_ids:
            QuestionAttachment.objects.filter(question=question, pk__in=plan.removed_ids).delete()

        for position, item in enumerate(plan.ordered_items):
            if isinstance(item, ExistingAttachment):
                QuestionAttachment.objects.filter(pk=item.pk).update(sort_order=position)
                continue
            attachment = QuestionAttachment(question=question, file_kind="image", sort_order=position)
            attachment.file.save(uploads[item.index].name, uploads[item.index], save=False)
            created_names.append(attachment.file.name)
            attachment.save()


def cleanup_unreferenced_files(created_names, storage):
    """Remove only files written by this request that have no committed row."""
    for name in dict.fromkeys(created_names):
        if not name:
            continue
        try:
            if not QuestionAttachment.objects.filter(file=name).exists():
                storage.delete(name)
        except Exception:
            logger.exception("Failed to clean up attachment file %s", name)
