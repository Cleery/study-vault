from django.contrib import messages
from django.db import transaction
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods, require_POST

from .forms import KnowledgeCardForm, QuestionForm
from .markdown import render_markdown
from .models import KnowledgeCard, Question, QuestionAttachment, Section, Subject


def _markdown_context(obj, fields):
    context = {}
    for field in fields:
        context[f"{field}_html"] = render_markdown(getattr(obj, field, ""))
    return context


def question_list(request):
    return redirect("knowledge-card-list")


def _question_form_context(form, title):
    return {
        "form": form,
        "page_title": title,
        "subjects": Subject.objects.all(),
        "sections": Section.objects.select_related("subject"),
    }


@require_http_methods(["GET", "POST"])
def question_create(request):
    form = QuestionForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            question = form.save()
            for index, upload in enumerate(form.cleaned_data.get("attachments", [])):
                QuestionAttachment.objects.create(
                    question=question, file=upload, file_kind="image", sort_order=index
                )
        return redirect("question-detail", pk=question.pk)
    return render(request, "question_bank/question_form.html", _question_form_context(form, "新建题目"))


@require_http_methods(["GET", "POST"])
def question_edit(request, pk):
    question = get_object_or_404(Question, pk=pk, deleted_at__isnull=True)
    form = QuestionForm(request.POST or None, request.FILES or None, instance=question)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            question = form.save()
            start = question.attachments.count()
            for index, upload in enumerate(form.cleaned_data.get("attachments", []), start=start):
                QuestionAttachment.objects.create(
                    question=question, file=upload, file_kind="image", sort_order=index
                )
        return redirect("question-detail", pk=question.pk)
    return render(request, "question_bank/question_form.html", _question_form_context(form, "编辑题目"))


def question_detail(request, pk):
    question = get_object_or_404(
        Question.objects.prefetch_related("attachments", "knowledge_cards"), pk=pk
    )
    context = {"question": question}
    context.update(
        _markdown_context(
            question,
            ["statement", "personal_solution", "reference_solution", "error_note"],
        )
    )
    return render(request, "question_bank/question_detail.html", context)


@require_POST
def question_archive(request, pk):
    question = get_object_or_404(Question, pk=pk, deleted_at__isnull=True)
    question.soft_delete()
    messages.success(request, "题目已归档。")
    return HttpResponseRedirect(reverse("question-list"))


@require_http_methods(["GET", "POST"])
def knowledge_card_create(request):
    form = KnowledgeCardForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            card = form.save()
            card.prerequisite_cards.set(form.cleaned_data.get("prerequisite_cards", []))
            card.questions.set(form.cleaned_data.get("questions", []))
        return redirect("knowledge-card-detail", pk=card.pk)
    return render(request, "question_bank/knowledge_card_form.html", {"form": form, "page_title": "新建知识卡片"})


@require_http_methods(["GET", "POST"])
def knowledge_card_edit(request, pk):
    card = get_object_or_404(KnowledgeCard, pk=pk)
    form = KnowledgeCardForm(request.POST or None, instance=card)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            card = form.save()
            card.prerequisite_cards.set(form.cleaned_data.get("prerequisite_cards", []))
            card.questions.set(form.cleaned_data.get("questions", []))
        return redirect("knowledge-card-detail", pk=card.pk)
    return render(request, "question_bank/knowledge_card_form.html", {"form": form, "page_title": "编辑知识卡片"})


def knowledge_card_list(request):
    cards = KnowledgeCard.objects.select_related("subject", "section").prefetch_related("questions")
    subject_id = request.GET.get("subject")
    card_type = request.GET.get("type")
    section_id = request.GET.get("section")
    if subject_id:
        cards = cards.filter(subject_id=subject_id)
    if card_type:
        cards = cards.filter(type=card_type)
    if section_id:
        cards = cards.filter(section_id=section_id)
    return render(
        request,
        "question_bank/knowledge_card_list.html",
        {
            "cards": cards,
            "subjects": Subject.objects.all(),
            "sections": Section.objects.select_related("subject"),
            "selected_subject": subject_id or "",
            "selected_type": card_type or "",
            "selected_section": section_id or "",
            "card_types": KnowledgeCard.CARD_TYPE_CHOICES,
        },
    )


def knowledge_card_detail(request, pk):
    card = get_object_or_404(
        KnowledgeCard.objects.select_related("subject", "section").prefetch_related(
            "questions", "prerequisite_cards"
        ),
        pk=pk,
    )
    context = {"card": card}
    context.update(
        _markdown_context(
            card,
            [
                "formal_statement",
                "conditions",
                "proof",
                "usage_signals",
                "common_mistakes",
                "personal_notes",
            ],
        )
    )
    return render(request, "question_bank/knowledge_card_detail.html", context)
