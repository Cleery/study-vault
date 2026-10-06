from datetime import timedelta, timezone
from pathlib import Path

from django.core import signing


QUESTION_VERSION_MAX_AGE = timedelta(hours=24)
QUESTION_VERSION_SALT = "question_bank.question_version"


def _utc_iso(value):
    return value.astimezone(timezone.utc).isoformat()


def _question_payload(question):
    attachments = [
        {
            "id": attachment.pk,
            "updated_at": _utc_iso(attachment.updated_at),
            "type": attachment.file_kind,
            "role": attachment.attachment_role,
            "filename": Path(attachment.file.name).name,
            "order": attachment.sort_order,
        }
        for attachment in question.attachments.all()
    ]
    attachments.sort(key=lambda item: (item["order"], item["id"]))
    return {
        "question_id": str(question.pk),
        "updated_at": _utc_iso(question.updated_at),
        "attachments": attachments,
        "tag_ids": sorted(str(pk) for pk in question.tags.values_list("pk", flat=True)),
        "knowledge_card_ids": sorted(
            str(pk) for pk in question.knowledge_cards.values_list("pk", flat=True)
        ),
    }


def build_question_version(question) -> str:
    return signing.dumps(_question_payload(question), salt=QUESTION_VERSION_SALT)


def verify_question_version(question, token: str) -> bool:
    if not isinstance(token, str) or not token:
        return False
    try:
        payload = signing.loads(
            token, salt=QUESTION_VERSION_SALT, max_age=QUESTION_VERSION_MAX_AGE
        )
    except (signing.BadSignature, TypeError, ValueError):
        return False
    return payload == _question_payload(question)
