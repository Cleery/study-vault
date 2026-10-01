from pathlib import Path

from django.core.exceptions import ValidationError
from PIL import Image

MAX_IMAGE_SIZE = 10 * 1024 * 1024
ALLOWED_IMAGE_FORMATS = {
    "PNG": {".png"},
    "JPEG": {".jpg", ".jpeg"},
    "WEBP": {".webp"},
}


def validate_image_upload(upload):
    if upload.size > MAX_IMAGE_SIZE:
        raise ValidationError("图片大小不能超过 10 MB。")
    suffix = Path(upload.name).suffix.lower()
    try:
        image = Image.open(upload)
        image_format = image.format
        image.verify()
    except Exception as exc:
        raise ValidationError("上传文件不是有效图片。") from exc
    finally:
        upload.seek(0)
    if image_format not in ALLOWED_IMAGE_FORMATS or suffix not in ALLOWED_IMAGE_FORMATS[image_format]:
        raise ValidationError("仅支持 PNG、JPEG 和 WebP 图片。")
