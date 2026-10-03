import logging
import uuid
from pathlib import Path

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Q
from django.db.models.signals import m2m_changed, post_delete, post_save, pre_save
from django.dispatch import receiver
from django.utils import timezone
from django.utils.text import slugify


logger = logging.getLogger(__name__)


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Subject(TimeStampedModel):
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=120, blank=True)

    class Meta:
        ordering = ["name", "id"]
        indexes = [models.Index(fields=["name"])]

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name) or uuid.uuid4().hex[:12]
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class Section(TimeStampedModel):
    subject = models.ForeignKey(Subject, on_delete=models.PROTECT, related_name="sections")
    name = models.CharField(max_length=150)
    slug = models.SlugField(max_length=180, blank=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["subject_id", "sort_order", "name", "id"]
        constraints = [
            models.UniqueConstraint(fields=["subject", "name"], name="unique_section_name_per_subject")
        ]
        indexes = [
            models.Index(fields=["subject", "sort_order"]),
            models.Index(fields=["name"]),
        ]

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name) or uuid.uuid4().hex[:12]
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.subject}: {self.name}"


class Question(TimeStampedModel):
    MASTERY_UNSTARTED = "unstarted"
    MASTERY_STRUGGLING = "struggling"
    MASTERY_UNSTABLE = "unstable"
    MASTERY_MASTERED = "mastered"
    MASTERY_CHOICES = (
        (MASTERY_UNSTARTED, "未开始"),
        (MASTERY_STRUGGLING, "有思路但无法完成"),
        (MASTERY_UNSTABLE, "可以完成但不稳定"),
        (MASTERY_MASTERED, "熟练掌握"),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    subject = models.ForeignKey(
        Subject,
        on_delete=models.PROTECT,
        related_name="questions",
        null=True,
        blank=True,
    )
    section = models.ForeignKey(
        Section,
        on_delete=models.SET_NULL,
        related_name="questions",
        null=True,
        blank=True,
    )
    title = models.CharField(max_length=255, blank=True)
    statement = models.TextField(blank=True)
    personal_solution = models.TextField(blank=True)
    reference_solution = models.TextField(blank=True)
    error_note = models.TextField(blank=True)
    mastery = models.CharField(max_length=20, choices=MASTERY_CHOICES, default=MASTERY_UNSTARTED)
    next_review_at = models.DateTimeField(null=True, blank=True)
    draft = models.BooleanField(default=True)
    archived = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)
    tags = models.ManyToManyField("Tag", related_name="questions", blank=True)
    knowledge_cards = models.ManyToManyField(
        "KnowledgeCard", related_name="questions", blank=True
    )

    class Meta:
        ordering = ["-updated_at", "id"]
        indexes = [
            models.Index(fields=["subject", "section"]),
            models.Index(fields=["mastery", "next_review_at"]),
            models.Index(fields=["archived", "draft"]),
            models.Index(fields=["deleted_at"]),
            models.Index(fields=["-updated_at"]),
        ]

    def clean(self):
        errors = {}
        if not self.draft and not (self.subject_id or self.title.strip() or self.statement.strip()):
            errors["title"] = "正式题目至少需要科目、标题或题干中的一项。"
        if self.subject_id and self.section_id:
            section_subject_id = getattr(self.section, "subject_id", None)
            if section_subject_id and section_subject_id != self.subject_id:
                errors["section"] = "章节必须属于题目的科目。"
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def soft_delete(self):
        self.deleted_at = timezone.now()
        self.save(update_fields=["deleted_at", "updated_at"])

    @property
    def is_deleted(self):
        return self.deleted_at is not None

    def __str__(self):
        return self.title or f"题目 {self.pk}"


def question_attachment_upload_to(instance, filename):
    suffix = Path(filename).suffix.lower()
    question_id = getattr(instance, "question_id", None)
    return f"questions/{question_id}/{uuid.uuid4().hex}{suffix}"


class QuestionAttachment(TimeStampedModel):
    FILE_KIND_CHOICES = (
        ("image", "图片"),
        ("document", "文档"),
        ("other", "其他"),
    )

    question = models.ForeignKey(
        Question, on_delete=models.CASCADE, related_name="attachments"
    )
    file = models.FileField(upload_to=question_attachment_upload_to)
    file_kind = models.CharField(max_length=20, choices=FILE_KIND_CHOICES, default="image")
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["question", "sort_order"], name="unique_attachment_order_per_question"
            )
        ]
        indexes = [models.Index(fields=["question", "sort_order"])]

    def __str__(self):
        return self.file.name


def _delete_attachment_file_when_unreferenced(storage, name):
    if not name:
        return

    def cleanup():
        try:
            references = QuestionAttachment.objects.filter(file=name)
            if not references.exists():
                storage.delete(name)
        except Exception:
            logger.exception("Failed to delete attachment file %s", name)

    transaction.on_commit(cleanup)


@receiver(post_delete, sender=QuestionAttachment)
def cleanup_deleted_attachment_file(sender, instance, **kwargs):
    if instance.file:
        _delete_attachment_file_when_unreferenced(instance.file.storage, instance.file.name)


@receiver(pre_save, sender=QuestionAttachment)
def remember_replaced_attachment_file(sender, instance, update_fields=None, **kwargs):
    instance._previous_attachment_file = None
    if not instance.pk or (update_fields is not None and "file" not in update_fields):
        return
    previous = sender.objects.filter(pk=instance.pk).only("file").first()
    if previous and previous.file and previous.file.name != instance.file.name:
        instance._previous_attachment_file = (previous.file.storage, previous.file.name)


@receiver(post_save, sender=QuestionAttachment)
def cleanup_replaced_attachment_file(sender, instance, **kwargs):
    previous = getattr(instance, "_previous_attachment_file", None)
    if previous:
        _delete_attachment_file_when_unreferenced(*previous)
        instance._previous_attachment_file = None


class KnowledgeCard(TimeStampedModel):
    CARD_TYPE_CHOICES = (
        ("theorem", "定理"),
        ("definition", "定义"),
        ("lemma", "引理"),
        ("criterion", "判别法"),
        ("formula", "公式"),
        ("other", "其他"),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    subject = models.ForeignKey(Subject, on_delete=models.PROTECT, related_name="knowledge_cards")
    section = models.ForeignKey(
        Section,
        on_delete=models.SET_NULL,
        related_name="knowledge_cards",
        null=True,
        blank=True,
    )
    type = models.CharField(max_length=50)
    core_content = models.TextField(blank=True)
    formal_statement = models.TextField(blank=True)
    conditions = models.TextField(blank=True)
    proof = models.TextField(blank=True)
    usage_signals = models.TextField(blank=True)
    common_mistakes = models.TextField(blank=True)
    personal_notes = models.TextField(blank=True)
    prerequisite_cards = models.ManyToManyField(
        "self",
        through="KnowledgeCardPrerequisite",
        symmetrical=False,
        related_name="dependent_cards",
        blank=True,
    )

    class Meta:
        ordering = ["name", "id"]
        indexes = [
            models.Index(fields=["subject", "section"]),
            models.Index(fields=["type"]),
            models.Index(fields=["name"]),
        ]

    def __init__(self, *args, **kwargs):
        if "card_type" in kwargs and "type" not in kwargs:
            kwargs["type"] = kwargs.pop("card_type")
        super().__init__(*args, **kwargs)

    @property
    def card_type(self):
        return self.type

    @card_type.setter
    def card_type(self, value):
        self.type = value

    def get_type_display(self):
        labels = dict(self.CARD_TYPE_CHOICES)
        return labels.get(self.type, self.type)

    def clean(self):
        errors = {}
        if not self.name.strip():
            errors["name"] = "知识卡片名称不能为空。"
        if not self.subject_id:
            errors["subject"] = "知识卡片必须关联科目。"
        if not self.type:
            errors["type"] = "知识卡片类型不能为空。"
        if self.subject_id and self.section_id:
            section_subject_id = getattr(self.section, "subject_id", None)
            if section_subject_id and section_subject_id != self.subject_id:
                errors["section"] = "章节必须属于知识卡片的科目。"
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class KnowledgeCardPrerequisite(models.Model):
    knowledge_card = models.ForeignKey(
        KnowledgeCard, on_delete=models.CASCADE, related_name="prerequisite_edges"
    )
    prerequisite = models.ForeignKey(
        KnowledgeCard, on_delete=models.CASCADE, related_name="dependent_edges"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["knowledge_card", "prerequisite"],
                name="unique_knowledge_card_prerequisite",
            ),
            models.CheckConstraint(
                condition=~Q(knowledge_card=models.F("prerequisite")),
                name="knowledge_card_prerequisite_not_self",
            ),
        ]
        indexes = [
            models.Index(fields=["knowledge_card"]),
            models.Index(fields=["prerequisite"]),
        ]

    def clean(self):
        if self.knowledge_card_id == self.prerequisite_id:
            raise ValidationError("知识卡片不能以前置关系指向自身。")
        if self.knowledge_card_id and self.prerequisite_id and _has_prerequisite_path(
            self.prerequisite_id, self.knowledge_card_id, exclude=self.pk
        ):
            raise ValidationError("知识卡片前置关系不能形成环。")

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


def _has_prerequisite_path(start_id, target_id, exclude=None, seen=None):
    if start_id == target_id:
        return True
    seen = set() if seen is None else seen
    if start_id in seen:
        return False
    seen.add(start_id)
    edge_query = KnowledgeCardPrerequisite.objects.filter(knowledge_card_id=start_id)
    if exclude:
        edge_query = edge_query.exclude(pk=exclude)
    return any(
        _has_prerequisite_path(edge.prerequisite_id, target_id, exclude=exclude, seen=seen)
        for edge in edge_query.only("prerequisite_id")
    )


@receiver(m2m_changed, sender=KnowledgeCardPrerequisite)
def validate_knowledge_card_prerequisites(sender, instance, action, reverse, model, pk_set, **kwargs):
    if action != "pre_add" or not pk_set:
        return
    if reverse:
        source_id = next(iter(pk_set))
        target_ids = {instance.pk}
    else:
        source_id = instance.pk
        target_ids = set(pk_set)
    if source_id in target_ids:
        raise ValidationError("知识卡片不能以前置关系指向自身。")
    if any(_has_prerequisite_path(target_id, source_id) for target_id in target_ids):
        raise ValidationError("知识卡片前置关系不能形成环。")


class Tag(TimeStampedModel):
    KIND_CHOICES = (
        ("custom", "自定义"),
        ("method", "方法"),
        ("problem_type", "题型"),
        ("source", "来源"),
        ("error_type", "错误类型"),
        ("system", "系统"),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100)
    parent = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        related_name="children",
        null=True,
        blank=True,
    )
    kind = models.CharField(max_length=30, choices=KIND_CHOICES, default="custom")
    archived = models.BooleanField(default=False)
    redirect_to = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        related_name="redirect_sources",
        null=True,
        blank=True,
    )

    class Meta:
        ordering = ["name", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["name", "parent"], name="unique_tag_name_per_parent"
            ),
            models.UniqueConstraint(
                fields=["name"],
                condition=Q(parent__isnull=True),
                name="unique_root_tag_name",
            ),
        ]
        indexes = [
            models.Index(fields=["parent", "name"]),
            models.Index(fields=["kind", "archived"]),
            models.Index(fields=["redirect_to"]),
            models.Index(
                fields=["archived", "redirect_to", "name"],
                name="tag_picker_prefix_idx",
            ),
        ]

    def clean(self):
        errors = {}
        if not self.name.strip():
            errors["name"] = "标签名称不能为空。"
        if self.redirect_to_id == self.pk:
            errors["redirect_to"] = "标签不能重定向到自身。"
        if self.parent_id == self.pk:
            errors["parent"] = "标签不能将自身设为父级。"
        if (
            self.parent_id
            and self.pk
            and self.parent_id != self.pk
            and _tag_parent_reaches(self.parent_id, self.pk)
        ):
            errors["parent"] = "父级不能形成环。"
        if self.redirect_to_id and self.pk and _tag_redirect_reaches(self.redirect_to_id, self.pk):
            errors["redirect_to"] = "标签重定向不能形成环。"
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def resolve_redirect(self):
        current = self
        visited = set()
        while current.redirect_to_id and current.redirect_to_id not in visited:
            visited.add(current.pk)
            current = current.redirect_to
        return current

    def __str__(self):
        return self.name


def _tag_redirect_reaches(start_id, target_id, seen=None):
    seen = set() if seen is None else seen
    if start_id == target_id:
        return True
    if start_id in seen:
        return False
    seen.add(start_id)
    next_id = Tag.objects.filter(pk=start_id).values_list("redirect_to_id", flat=True).first()
    return bool(next_id and _tag_redirect_reaches(next_id, target_id, seen))


def _tag_parent_reaches(start_id, target_id):
    """Return whether a prospective parent chain reaches the edited tag."""
    visited = set()
    current_id = start_id
    while current_id and current_id not in visited:
        if current_id == target_id:
            return True
        visited.add(current_id)
        current_id = Tag.objects.filter(pk=current_id).values_list("parent_id", flat=True).first()
    return False


def schedule_tag_picker_cache_invalidation():
    from .search import invalidate_tag_picker_cache

    transaction.on_commit(invalidate_tag_picker_cache)


@receiver(post_save, sender=Tag)
def invalidate_tag_picker_cache_after_tag_save(sender, instance, **kwargs):
    schedule_tag_picker_cache_invalidation()


@receiver(post_delete, sender=Tag)
def invalidate_tag_picker_cache_after_tag_delete(sender, instance, **kwargs):
    schedule_tag_picker_cache_invalidation()


class ReviewRecord(TimeStampedModel):
    RESULT_CHOICES = (
        ("not_done", "未完成"),
        ("hinted", "看提示完成"),
        ("independent", "独立完成"),
        ("mastered", "熟练完成"),
        ("skipped", "跳过"),
    )

    question = models.ForeignKey(
        Question, on_delete=models.CASCADE, related_name="review_records"
    )
    reviewed_at = models.DateTimeField(default=timezone.now)
    result = models.CharField(max_length=20, choices=RESULT_CHOICES)
    mastery_before = models.CharField(max_length=20, choices=Question.MASTERY_CHOICES)
    mastery_after = models.CharField(max_length=20, choices=Question.MASTERY_CHOICES)
    duration_seconds = models.PositiveIntegerField(null=True, blank=True)
    note = models.TextField(blank=True)
    next_review_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-reviewed_at", "-id"]
        indexes = [
            models.Index(fields=["question", "-reviewed_at"]),
            models.Index(fields=["result", "-reviewed_at"]),
            models.Index(fields=["next_review_at"]),
        ]

    def __str__(self):
        return f"{self.question} @ {self.reviewed_at:%Y-%m-%d %H:%M}"
