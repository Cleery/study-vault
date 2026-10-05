import hashlib

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from .attachments import cleanup_unreferenced_files
from .models import Question, QuestionAttachment
from .validators import validate_image_upload


class UploadConflict(Exception):
    pass


def create_batch_draft(*, image, batch_id, client_upload_id):
    validate_image_upload(image)
    digest = hashlib.sha256()
    for chunk in image.chunks():
        digest.update(chunk)
    image.seek(0)
    sha256 = digest.hexdigest()

    def existing_result():
        attachment = QuestionAttachment.objects.select_related("question").filter(
            client_upload_id=client_upload_id
        ).first()
        if attachment is None:
            return None
        if attachment.content_sha256 != sha256 or attachment.question.batch_id != batch_id:
            raise UploadConflict("上传标识已对应另一张图片或批次。")
        return attachment.question, False

    existing = existing_result()
    if existing:
        return existing

    created_names = []
    storage = QuestionAttachment._meta.get_field("file").storage
    try:
        with transaction.atomic():
            question = Question.objects.create(draft=True, batch_id=batch_id)
            attachment = QuestionAttachment(
                question=question, file_kind="image", sort_order=0,
                client_upload_id=client_upload_id, content_sha256=sha256,
            )
            attachment.file.save(image.name, image, save=False)
            created_names.append(attachment.file.name)
            attachment.save()
        return question, True
    except IntegrityError:
        cleanup_unreferenced_files(created_names, storage)
        existing = existing_result()
        if existing:
            return existing
        raise
    except Exception:
        cleanup_unreferenced_files(created_names, storage)
        raise
