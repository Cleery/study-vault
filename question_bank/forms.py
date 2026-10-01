from django import forms

from .models import KnowledgeCard, Question, Section
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
    attachments = MultipleFileField(required=False, label="图片附件")
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
            "knowledge_cards",
        ]
        widgets = {
            "statement": forms.Textarea(attrs={"rows": 8}),
            "personal_solution": forms.Textarea(attrs={"rows": 6}),
            "reference_solution": forms.Textarea(attrs={"rows": 6}),
            "error_note": forms.Textarea(attrs={"rows": 4}),
            "knowledge_cards": forms.SelectMultiple(attrs={"size": 5}),
        }

    def clean_attachments(self):
        uploads = self.cleaned_data.get("attachments", [])
        for upload in uploads:
            validate_image_upload(upload)
        return uploads

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("mastery"):
            cleaned["mastery"] = Question.MASTERY_UNSTARTED
        subject = cleaned.get("subject")
        section = cleaned.get("section")
        if subject and section and section.subject_id != subject.pk:
            self.add_error("section", "章节必须属于题目的科目。")
        return cleaned


class KnowledgeCardForm(forms.ModelForm):
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
            "formal_statement": forms.Textarea(attrs={"rows": 5}),
            "conditions": forms.Textarea(attrs={"rows": 4}),
            "proof": forms.Textarea(attrs={"rows": 6}),
            "usage_signals": forms.Textarea(attrs={"rows": 4}),
            "common_mistakes": forms.Textarea(attrs={"rows": 4}),
            "personal_notes": forms.Textarea(attrs={"rows": 4}),
            "prerequisite_cards": forms.SelectMultiple(attrs={"size": 5}),
            "questions": forms.SelectMultiple(attrs={"size": 5}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.fields["prerequisite_cards"].queryset = KnowledgeCard.objects.exclude(
                pk=self.instance.pk
            )

    def clean(self):
        cleaned = super().clean()
        subject = cleaned.get("subject")
        section = cleaned.get("section")
        if subject and section and section.subject_id != subject.pk:
            self.add_error("section", "章节必须属于知识卡片的科目。")
        return cleaned

