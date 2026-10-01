from django.contrib import messages
from django.db import transaction
from django.http import HttpResponseRedirect
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods, require_POST

from .forms import KnowledgeCardForm, QuestionForm, TagForm
from .markdown import render_markdown
from .models import KnowledgeCard, Question, QuestionAttachment, Section, Subject, Tag
from .search import (
    active_tag_queryset,
    archive_tag,
    build_question_queryset,
    merge_tags,
    parameter_values,
    rename_tag,
)


def _markdown_context(obj, fields):
    context = {}
    for field in fields:
        context[f"{field}_html"] = render_markdown(getattr(obj, field, ""))
    return context


def question_list(request):
    questions = build_question_queryset(request.GET)
    page = Paginator(questions, 20).get_page(request.GET.get("page", 1))
    active_filters = []
    labels = {
        "q": "关键词",
        "subject": "科目",
        "section": "章节",
        "tag": "标签",
        "knowledge_card": "知识卡片",
        "mastery": "掌握程度",
        "due": "到期",
    }
    for key, label in labels.items():
        values = parameter_values(request.GET, key)
        for value in values:
            active_filters.append({"key": key, "value": value, "label": f"{label}: {value}"})
    context = {
        "questions": page,
        "page_obj": page,
        "paginator": page.paginator,
        "subjects": Subject.objects.all(),
        "sections": Section.objects.select_related("subject"),
        "tags": active_tag_queryset(),
        "all_tags": Tag.objects.select_related("parent").order_by("name", "id"),
        "knowledge_cards": KnowledgeCard.objects.order_by("name", "id"),
        "mastery_choices": Question.MASTERY_CHOICES,
        "active_filters": active_filters,
        "selected_subjects": parameter_values(request.GET, "subject", "subjects"),
        "selected_sections": parameter_values(request.GET, "section", "sections"),
        "selected_tags": parameter_values(request.GET, "tag", "tags"),
        "selected_cards": parameter_values(request.GET, "knowledge_card", "knowledge_cards", "card"),
        "selected_mastery": parameter_values(request.GET, "mastery", "masteries"),
        "query": request.GET.get("q", ""),
    }
    return render(request, "question_bank/question_list.html", context)


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
def tag_manage(request, pk=None):
    tag = get_object_or_404(Tag, pk=pk) if pk else None
    form = TagForm(request.POST or None, instance=tag)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "标签已保存。")
        return redirect("tag-manage")
    return render(
        request,
        "question_bank/tag_manage.html",
        {"form": form, "tags": Tag.objects.select_related("parent").order_by("name", "id"), "editing_tag": tag},
    )


@require_POST
def tag_rename(request, pk):
    tag = get_object_or_404(Tag, pk=pk)
    form = TagForm(request.POST, instance=tag)
    if form.is_valid():
        form.save()
    return redirect("tag-manage")


@require_POST
def tag_merge(request, pk):
    source = get_object_or_404(Tag, pk=pk)
    target_id = request.POST.get("target") or request.POST.get("target_tag")
    target = get_object_or_404(Tag, pk=target_id)
    try:
        merge_tags(source, target)
        messages.success(request, "标签已合并。")
    except ValueError as exc:
        messages.error(request, str(exc))
    return redirect("tag-manage")


@require_POST
def tag_archive(request, pk):
    archive_tag(get_object_or_404(Tag, pk=pk))
    messages.success(request, "标签已归档。")
    return redirect("tag-manage")


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
