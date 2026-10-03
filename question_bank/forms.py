import re

from django import forms
from django.core.exceptions import ValidationError
from django.db.models import Q

from .models import KnowledgeCard, Question, Section, Subject, Tag
from .validators import validate_image_upload


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    widget = MultipleFileInput

    def clean(self, data, initial=None):
        if not data:
            return []
        if isinstance(data, (list, tuple)):
            return [super().clean(item, initial) for item in data]
        return [super().clean(data, initial)]


class QuestionForm(forms.ModelForm):
    attachments = MultipleFileField(
        required=False,
        label="图片附件",
        widget=MultipleFileInput(attrs={"data-image-input": "true", "data-preview": "image-preview"}),
    )
    mastery = forms.ChoiceField(
        choices=Question.MASTERY_CHOICES,
        required=False,
        initial=Question.MASTERY_UNSTARTED,
    )

    class Meta:
        model = Question
        fields = [
            "subject",
            "section",
            "title",
            "statement",
            "personal_solution",
            "reference_solution",
            "error_note",
            "mastery",
            "draft",
            "tags",
            "knowledge_cards",
        ]
        widgets = {
            "draft": forms.HiddenInput(),
            "statement": forms.Textarea(attrs={"rows": 8}),
            "personal_solution": forms.Textarea(attrs={"rows": 6}),
            "reference_solution": forms.Textarea(attrs={"rows": 6}),
            "error_note": forms.Textarea(attrs={"rows": 4}),
            "knowledge_cards": forms.SelectMultiple(attrs={"size": 5}),
            "tags": forms.SelectMultiple(attrs={"size": 5}),
        }
        labels = {
            "subject": "科目",
            "section": "章节",
            "title": "标题",
            "statement": "题干",
            "personal_solution": "个人解法",
            "reference_solution": "参考解法",
            "error_note": "错误笔记",
            "mastery": "掌握程度",
            "draft": "草稿",
            "tags": "标签",
            "knowledge_cards": "知识卡片",
        }

    def __init__(self, *args, save_intent=None, **kwargs):
        if args and args[0] is not None:
            data = args[0].copy()
            data["draft"] = "on" if save_intent == "draft" else ""
            args = (data, *args[1:])
        self.save_intent = save_intent
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.fields["tags"].queryset = Tag.objects.filter(
                Q(archived=False) | Q(questions=self.instance)
            ).distinct().order_by("name", "id")
        else:
            self.fields["tags"].queryset = Tag.objects.filter(archived=False).order_by(
                "name", "id"
            )

    def clean_attachments(self):
        uploads = self.cleaned_data.get("attachments", [])
        for upload in uploads:
            try:
                validate_image_upload(upload)
            except ValidationError as exc:
                reason = "; ".join(str(message) for message in exc.messages)
                raise ValidationError(f"{upload.name}：{reason}") from exc
        return uploads

    def clean_tags(self):
        tags = self.cleaned_data.get("tags")
        if self.instance and self.instance.pk:
            return tags
        return tags.exclude(archived=True) if tags is not None else tags

    def clean(self):
        cleaned = super().clean()
        if self.is_bound and self.save_intent not in {"draft", "publish"}:
            self.add_error(None, "保存意图无效，必须选择保存为草稿或正式题目。")
        if not cleaned.get("mastery"):
            cleaned["mastery"] = Question.MASTERY_UNSTARTED
        subject = cleaned.get("subject")
        section = cleaned.get("section")
        if subject and section and section.subject_id != subject.pk:
            self.add_error("section", "章节必须属于题目的科目。")
        return cleaned


class KnowledgeCardForm(forms.ModelForm):
    subject = forms.CharField(label="科目", max_length=100)
    section = forms.CharField(label="章节", max_length=150, required=False)
    type = forms.CharField(label="类型", max_length=50)
    core_content = forms.CharField(
        required=False,
        strip=False,
        label="核心内容",
        widget=forms.Textarea(
            attrs={
                "rows": 18,
                "data-kc-editor": "true",
                "placeholder": "## 定理内容\n\n写定理、定义或公式。\n\n## 使用条件\n\n写成立条件。\n\n## 使用信号\n\n写什么时候使用。",
            }
        ),
    )
    questions = forms.ModelMultipleChoiceField(
        queryset=Question.objects.all(), required=False, label="关联题目"
    )

    class Meta:
        model = KnowledgeCard
        fields = [
            "name",
            "subject",
            "section",
            "type",
            "core_content",
            "formal_statement",
            "conditions",
            "proof",
            "usage_signals",
            "common_mistakes",
            "personal_notes",
            "prerequisite_cards",
            "questions",
        ]
        widgets = {
            "personal_notes": forms.Textarea(attrs={"rows": 4}),
            "prerequisite_cards": forms.SelectMultiple(attrs={"size": 5}),
            "questions": forms.SelectMultiple(attrs={"size": 5}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["subject"].widget.attrs.update({"list": "subject-options", "autocomplete": "off"})
        self.fields["section"].widget.attrs.update({"list": "section-options", "autocomplete": "off"})
        self.fields["type"].widget.attrs.update({"list": "type-options", "autocomplete": "off"})
        self.subject_options = Subject.objects.order_by("name", "id")
        self.section_options = Section.objects.select_related("subject").order_by("subject_id", "sort_order", "name", "id")
        self.type_options = [label for _, label in KnowledgeCard.CARD_TYPE_CHOICES]
        for legacy_field in (
            "formal_statement", "conditions", "proof", "usage_signals", "common_mistakes"
        ):
            self.fields[legacy_field].widget = forms.HiddenInput()
        if self.instance and self.instance.subject_id and not self.is_bound:
            self.initial["subject"] = self.instance.subject.name
            self.initial["section"] = self.instance.section.name if self.instance.section else ""
            self.initial["type"] = self.instance.type
            self.initial["core_content"] = self.instance.core_content or build_core_content(self.instance)
        if self.instance and self.instance.pk:
            self.fields["prerequisite_cards"].queryset = KnowledgeCard.objects.exclude(
                pk=self.instance.pk
            )

    def clean(self):
        cleaned = super().clean()
        subject_name = (cleaned.get("subject") or "").strip()
        section_name = (cleaned.get("section") or "").strip()
        type_name = (cleaned.get("type") or "").strip()
        predefined_types = {label: value for value, label in KnowledgeCard.CARD_TYPE_CHOICES}
        if subject_name.isdigit():
            legacy_subject = Subject.objects.filter(pk=subject_name).first()
            if legacy_subject:
                subject_name = legacy_subject.name
        if section_name.isdigit():
            legacy_section = Section.objects.filter(pk=section_name).first()
            if legacy_section:
                section_name = legacy_section.name
        if subject_name and not self.errors:
            subject = Subject.objects.filter(name=subject_name).first()
            if subject is None:
                subject, _ = Subject.objects.get_or_create(name=subject_name)
            cleaned["subject"] = subject
            if section_name:
                section = Section.objects.filter(subject=subject, name=section_name).first()
                if section is None:
                    section, _ = Section.objects.get_or_create(subject=subject, name=section_name)
                cleaned["section"] = section
            else:
                cleaned["section"] = None
        else:
            cleaned.pop("subject", None)
            cleaned.pop("section", None)
        cleaned["type"] = predefined_types.get(type_name, type_name)
        return cleaned

    def save(self, commit=True):
        instance = super().save(commit=False)
        core_content = self.cleaned_data.get("core_content", "")
        if "core_content" not in self.data and not core_content.strip():
            legacy_parts = (
                ("定理内容", self.cleaned_data.get("formal_statement", "")),
                ("使用条件", self.cleaned_data.get("conditions", "")),
                ("证明", self.cleaned_data.get("proof", "")),
                ("使用信号", self.cleaned_data.get("usage_signals", "")),
                ("常见错误", self.cleaned_data.get("common_mistakes", "")),
            )
            core_content = "\n\n".join(
                f"## {heading}\n\n{value}" for heading, value in legacy_parts if value.strip()
            )
        instance.core_content = core_content
        sections = split_core_content(core_content)
        for field_name, value in sections.items():
            setattr(instance, field_name, value)
        if commit:
            instance.save()
            self.save_m2m()
        return instance


_CORE_HEADING_FIELDS = {
    "定理内容": "formal_statement",
    "正式表述": "formal_statement",
    "定义内容": "formal_statement",
    "核心内容": "formal_statement",
    "使用条件": "conditions",
    "适用条件": "conditions",
    "成立条件": "conditions",
    "证明": "proof",
    "证明过程": "proof",
    "使用信号": "usage_signals",
    "使用场景": "usage_signals",
    "常见错误": "common_mistakes",
    "易错点": "common_mistakes",
}


def split_core_content(source):
    """Map the compact editor headings to the legacy storage fields."""
    result = {
        "formal_statement": "",
        "conditions": "",
        "proof": "",
        "usage_signals": "",
        "common_mistakes": "",
    }
    current = None
    unknown = []
    fence_marker = None
    for line in (source or "").replace("\r\n", "\n").split("\n"):
        fence = re.match(r"^\s*(`{3,}|~{3,})", line)
        if fence:
            marker = fence.group(1)
            if fence_marker is None:
                fence_marker = marker
            elif marker[0] == fence_marker[0] and len(marker) >= len(fence_marker):
                fence_marker = None
        match = None if fence_marker or fence else re.match(r"^##\s+(.+?)\s*$", line)
        if match:
            current = _CORE_HEADING_FIELDS.get(match.group(1).strip())
            if current is None:
                unknown.append([line])
            continue
        if current:
            result[current] += ("\n" if result[current] else "") + line
        elif unknown:
            unknown[-1].append(line)
        elif line.strip():
            result["formal_statement"] += ("\n" if result["formal_statement"] else "") + line
    if unknown:
        extra = "\n\n".join("\n".join(lines).strip() for lines in unknown if "\n".join(lines).strip())
        if extra:
            result["formal_statement"] = "\n\n".join(
                value for value in (result["formal_statement"].strip(), extra) if value
            )
    return {key: value.strip() for key, value in result.items()}


def build_core_content(card):
    sections = (
        ("定理内容", card.formal_statement),
        ("使用条件", card.conditions),
        ("证明", card.proof),
        ("使用信号", card.usage_signals),
        ("常见错误", card.common_mistakes),
    )
    return "\n\n".join(f"## {heading}\n\n{value}" for heading, value in sections if value.strip())


class TagForm(forms.ModelForm):
    class Meta:
        model = Tag
        fields = ["name", "parent", "kind"]
        widgets = {"parent": forms.Select(attrs={"class": "tag-parent"})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        queryset = Tag.objects.filter(archived=False).order_by("name", "id")
        if self.instance and self.instance.pk:
            excluded_ids = {self.instance.pk}
            pending_ids = {self.instance.pk}
            while pending_ids:
                child_ids = set(
                    Tag.objects.filter(parent_id__in=pending_ids).values_list("pk", flat=True)
                )
                child_ids -= excluded_ids
                if not child_ids:
                    break
                excluded_ids.update(child_ids)
                pending_ids = child_ids
            queryset = queryset.exclude(pk__in=excluded_ids)
        self.fields["parent"].queryset = queryset

