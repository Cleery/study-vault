import json
import zipfile
from pathlib import Path
from pathlib import PurePosixPath

from django.core.files.base import ContentFile
from django.db import transaction

from .models import (
    KnowledgeCard,
    KnowledgeCardPrerequisite,
    Question,
    QuestionAttachment,
    ReviewRecord,
    schedule_tag_picker_cache_invalidation,
    Section,
    Subject,
    Tag,
)
from .validators import validate_image_upload

MANIFEST_VERSION = 1
ENTITY_NAMES = ("subjects", "sections", "questions", "attachments", "knowledge_cards", "tags", "review_records", "knowledge_card_prerequisites")


def _iso(value):
    return value.isoformat() if value is not None else None


def _entity_base(obj):
    return {"id": str(obj.pk), "created_at": _iso(obj.created_at), "updated_at": _iso(obj.updated_at)}


def _entity_id(kind, value):
    return f"{kind}:{value}"


def _build_manifest():
    entities = {name: [] for name in ENTITY_NAMES}
    for obj in Subject.objects.all().order_by("pk"):
        entities["subjects"].append({**_entity_base(obj), "name": obj.name, "slug": obj.slug})
    for obj in Section.objects.all().order_by("pk"):
        entities["sections"].append({**_entity_base(obj), "subject_id": _entity_id("subject", obj.subject_id), "name": obj.name, "slug": obj.slug, "sort_order": obj.sort_order})
    for obj in Question.objects.all().order_by("pk"):
        entities["questions"].append({
            **_entity_base(obj), "subject_id": _entity_id("subject", obj.subject_id) if obj.subject_id else None,
            "section_id": _entity_id("section", obj.section_id) if obj.section_id else None,
            "title": obj.title, "statement": obj.statement, "personal_solution": obj.personal_solution,
            "reference_solution": obj.reference_solution, "error_note": obj.error_note, "mastery": obj.mastery,
            "next_review_at": _iso(obj.next_review_at), "draft": obj.draft, "archived": obj.archived, "deleted_at": _iso(obj.deleted_at),
            "tag_ids": [_entity_id("tag", pk) for pk in obj.tags.values_list("pk", flat=True)],
            "knowledge_card_ids": [_entity_id("knowledge_card", pk) for pk in obj.knowledge_cards.values_list("pk", flat=True)],
        })
    for obj in QuestionAttachment.objects.select_related("question").all().order_by("pk"):
        source = Path(obj.file.name)
        entities["attachments"].append({
            **_entity_base(obj), "question_id": _entity_id("question", obj.question_id), "path": f"attachments/{obj.question_id}/{obj.pk}_{source.name}",
            "file_kind": obj.file_kind, "sort_order": obj.sort_order,
        })
    for obj in KnowledgeCard.objects.all().order_by("pk"):
        entities["knowledge_cards"].append({
            **_entity_base(obj), "subject_id": _entity_id("subject", obj.subject_id), "section_id": _entity_id("section", obj.section_id) if obj.section_id else None,
            "name": obj.name, "type": obj.type, "core_content": obj.core_content, "formal_statement": obj.formal_statement, "conditions": obj.conditions,
            "proof": obj.proof, "usage_signals": obj.usage_signals, "common_mistakes": obj.common_mistakes, "personal_notes": obj.personal_notes,
        })
    for obj in Tag.objects.all().order_by("pk"):
        entities["tags"].append({**_entity_base(obj), "name": obj.name, "parent_id": _entity_id("tag", obj.parent_id) if obj.parent_id else None, "kind": obj.kind, "archived": obj.archived, "redirect_to_id": _entity_id("tag", obj.redirect_to_id) if obj.redirect_to_id else None})
    for obj in ReviewRecord.objects.all().order_by("pk"):
        entities["review_records"].append({**_entity_base(obj), "question_id": _entity_id("question", obj.question_id), "reviewed_at": _iso(obj.reviewed_at), "result": obj.result, "mastery_before": obj.mastery_before, "mastery_after": obj.mastery_after, "duration_seconds": obj.duration_seconds, "note": obj.note, "next_review_at": _iso(obj.next_review_at)})
    for obj in KnowledgeCardPrerequisite.objects.all().order_by("pk"):
        entities["knowledge_card_prerequisites"].append({"id": str(obj.pk), "knowledge_card_id": _entity_id("knowledge_card", obj.knowledge_card_id), "prerequisite_id": _entity_id("knowledge_card", obj.prerequisite_id)})
    return {"version": MANIFEST_VERSION, "entities": entities}


def export_bundle(output):
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    manifest = _build_manifest()
    attachment_files = []
    for item in manifest["entities"]["attachments"]:
        attachment = QuestionAttachment.objects.get(pk=int(item["id"]))
        if not attachment.file or not attachment.file.storage.exists(attachment.file.name):
            raise ValueError(f"附件文件不存在: {attachment.file.name}")
        attachment_files.append((item, attachment))
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2))
        for item, attachment in attachment_files:
            with attachment.file.open("rb") as source:
                archive.writestr(item["path"], source.read())
    return output


def _parse_dt(value):
    if not value:
        return None
    from django.utils.dateparse import parse_datetime
    return parse_datetime(value)


def _validate_manifest(manifest, archive_names):
    if manifest.get("version") != MANIFEST_VERSION:
        raise ValueError("不支持的导出包版本")
    entities = manifest.get("entities")
    if not isinstance(entities, dict) or any(not isinstance(entities.get(name), list) for name in ENTITY_NAMES):
        raise ValueError("导出包缺少实体")
    ids = {}
    for name in ENTITY_NAMES:
        values = [str(item.get("id", "")) for item in entities[name]]
        if any(not value for value in values) or len(values) != len(set(values)):
            raise ValueError(f"{name} 包含空 ID 或重复 ID")
        ids[name] = set(values)
    references = {
        "sections": (("subject_id", "subjects"),),
        "questions": (("subject_id", "subjects"), ("section_id", "sections")),
        "attachments": (("question_id", "questions"),),
        "knowledge_cards": (("subject_id", "subjects"), ("section_id", "sections")),
        "tags": (("parent_id", "tags"), ("redirect_to_id", "tags")),
        "review_records": (("question_id", "questions"),),
        "knowledge_card_prerequisites": (("knowledge_card_id", "knowledge_cards"), ("prerequisite_id", "knowledge_cards")),
    }
    singular = {"subjects": "subject", "sections": "section", "questions": "question", "attachments": "attachment", "knowledge_cards": "knowledge_card", "tags": "tag", "review_records": "review_record", "knowledge_card_prerequisites": "knowledge_card_prerequisite"}
    for entity_name, fields in references.items():
        for item in entities[entity_name]:
            for field, target in fields:
                reference = item.get(field)
                optional = (
                    (entity_name == "questions" and field in {"subject_id", "section_id"})
                    or (entity_name == "knowledge_cards" and field == "section_id")
                    or (entity_name == "tags" and field in {"parent_id", "redirect_to_id"})
                )
                if reference is None and optional:
                    continue
                prefix = singular[target] + ":"
                if not isinstance(reference, str) or not reference.startswith(prefix) or reference[len(prefix):] not in ids[target]:
                    raise ValueError(f"无效关系: {entity_name}.{field}")
    for item in entities["questions"]:
        for field, target, prefix in (("tag_ids", "tags", "tag:"), ("knowledge_card_ids", "knowledge_cards", "knowledge_card:")):
            for reference in item.get(field, []):
                if not isinstance(reference, str) or not reference.startswith(prefix) or reference[len(prefix):] not in ids[target]:
                    raise ValueError(f"无效关系: questions.{field}")
    for item in entities["attachments"]:
        path = item.get("path", "")
        normalized = PurePosixPath(path)
        if normalized.is_absolute() or ".." in normalized.parts or path not in archive_names:
            raise ValueError("附件路径无效或附件缺失")
    return entities


@transaction.atomic
def _import_bundle(bundle, written_files):
    bundle = Path(bundle)
    with zipfile.ZipFile(bundle) as archive:
        try:
            manifest = json.loads(archive.read("manifest.json"))
        except (KeyError, json.JSONDecodeError) as exc:
            raise ValueError("无效导出包") from exc
        entities = _validate_manifest(manifest, set(archive.namelist()))
        maps = {name[:-1]: {} for name in ENTITY_NAMES}
        # Natural keys make integer primary keys portable while preserving package ID mappings.
        for data in entities["subjects"]:
            obj = Subject.objects.filter(name=data["name"]).first() or Subject(name=data["name"])
            obj.slug = data.get("slug", "")
            obj.save()
            maps["subject"][_entity_id("subject", data["id"])] = obj.pk
        for data in entities["sections"]:
            subject_id = maps["subject"][_entity_id("subject", data["subject_id"].split(":", 1)[1])]
            obj = Section.objects.filter(subject_id=subject_id, name=data["name"]).first() or Section(subject_id=subject_id, name=data["name"])
            obj.slug, obj.sort_order = data.get("slug", ""), data.get("sort_order", 0)
            obj.save()
            maps["section"][_entity_id("section", data["id"])] = obj.pk
        pending_tags = {str(data["id"]): data for data in entities["tags"]}
        while pending_tags:
            progressed = False
            for package_id, data in list(pending_tags.items()):
                parent_ref = data.get("parent_id")
                if parent_ref and parent_ref not in maps["tag"]:
                    continue
                parent = maps["tag"].get(parent_ref)
                obj = Tag.objects.filter(pk=data["id"]).first()
                obj = obj or Tag.objects.filter(name=data["name"], parent_id=parent).first()
                obj = obj or Tag(pk=data["id"], name=data["name"])
                obj.name, obj.parent_id = data["name"], parent
                obj.kind, obj.archived = data.get("kind", "custom"), data.get("archived", False)
                obj.redirect_to_id = None
                obj.save()
                maps["tag"][_entity_id("tag", package_id)] = obj.pk
                del pending_tags[package_id]
                progressed = True
            if not progressed:
                raise ValueError("标签父级关系形成环")
        for data in entities["tags"]:
            redirect_id = maps["tag"].get(data.get("redirect_to_id"))
            if redirect_id:
                Tag.objects.filter(pk=maps["tag"][_entity_id("tag", data["id"])]).update(redirect_to_id=redirect_id)
                schedule_tag_picker_cache_invalidation()
        for data in entities["knowledge_cards"]:
            subject_id = maps["subject"][_entity_id("subject", data["subject_id"].split(":", 1)[1])]
            section_id = maps["section"].get(data.get("section_id"))
            obj = KnowledgeCard.objects.filter(pk=data["id"]).first()
            obj = obj or KnowledgeCard.objects.filter(name=data["name"], subject_id=subject_id).first()
            obj = obj or KnowledgeCard(pk=data["id"], name=data["name"], subject_id=subject_id)
            for field in ("type", "core_content", "formal_statement", "conditions", "proof", "usage_signals", "common_mistakes", "personal_notes"):
                setattr(obj, field, data.get(field, ""))
            obj.section_id = section_id
            obj.save()
            maps["knowledge_card"][_entity_id("knowledge_card", data["id"])] = obj.pk
        for data in entities["questions"]:
            subject_id = maps["subject"].get(data.get("subject_id"))
            section_id = maps["section"].get(data.get("section_id"))
            obj = Question.objects.filter(pk=data["id"]).first() or Question(pk=data["id"])
            for field in ("title", "statement", "personal_solution", "reference_solution", "error_note", "mastery", "draft", "archived"):
                setattr(obj, field, data.get(field, getattr(obj, field, "")))
            obj.subject_id, obj.section_id = subject_id, section_id
            obj.next_review_at, obj.deleted_at = _parse_dt(data.get("next_review_at")), _parse_dt(data.get("deleted_at"))
            obj.save()
            maps["question"][_entity_id("question", data["id"])] = obj.pk
            obj.tags.set([maps["tag"][x] for x in data.get("tag_ids", [])])
            obj.knowledge_cards.set([maps["knowledge_card"][x] for x in data.get("knowledge_card_ids", [])])
        for data in entities["attachments"]:
            question_id = maps["question"][data["question_id"]]
            obj = QuestionAttachment.objects.filter(pk=data["id"]).first() or QuestionAttachment(question_id=question_id)
            obj.question_id, obj.file_kind, obj.sort_order = question_id, data.get("file_kind", "image"), data.get("sort_order", 0)
            if data["path"] in archive.namelist():
                attachment_name = Path(data["path"]).name
                attachment_content = ContentFile(archive.read(data["path"]), name=attachment_name)
                validate_image_upload(attachment_content)
                obj.file.save(attachment_name, attachment_content, save=False)
                written_files.append((obj.file.storage, obj.file.name))
            obj.save()
        for data in entities["review_records"]:
            question_id = maps["question"][data["question_id"]]
            obj = ReviewRecord.objects.filter(pk=data["id"]).first() or ReviewRecord(pk=data["id"])
            obj.question_id, obj.reviewed_at, obj.result = question_id, _parse_dt(data["reviewed_at"]), data["result"]
            for field in ("mastery_before", "mastery_after", "duration_seconds", "note"):
                setattr(obj, field, data.get(field))
            obj.next_review_at = _parse_dt(data.get("next_review_at"))
            obj.save()
        for data in entities["knowledge_card_prerequisites"]:
            KnowledgeCardPrerequisite.objects.get_or_create(knowledge_card_id=maps["knowledge_card"][data["knowledge_card_id"]], prerequisite_id=maps["knowledge_card"][data["prerequisite_id"]])
    return maps


def import_bundle(bundle):
    written_files = []
    try:
        return _import_bundle(bundle, written_files)
    except Exception:
        for storage, name in written_files:
            if not QuestionAttachment.objects.filter(file=name).exists():
                storage.delete(name)
        raise
