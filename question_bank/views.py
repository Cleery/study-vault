from django.contrib import messages
from django.db import transaction
from django.http import HttpResponseBadRequest, HttpResponseRedirect, JsonResponse
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods, require_POST
from uuid import UUID

from .attachments import (
    AttachmentPlanValidationError,
    apply_attachment_plan,
    cleanup_unreferenced_files,
    parse_attachment_plan,
)
from .forms import KnowledgeCardForm, QuestionForm, TagForm, build_core_content
from .markdown import render_markdown
from .models import KnowledgeCard, Question, QuestionAttachment, Section, Subject, Tag
from .review import due_today_questions, get_overdue_questions, get_recent_mistakes, apply_review
from .search import (
    active_tag_queryset,
    archive_tag,
    build_question_queryset,
    build_tag_picker,
    canonical_tag_ids,
    due_filter_is_active,
    merge_tags,
    parameter_values,
    rename_tag,
    search_tag_suggestions,
)
from .stats import get_statistics
from .tag_workspace import build_tag_workspace
from .versioning import build_question_version, verify_question_version


def _markdown_context(obj, fields):
    context = {}
    for field in fields:
        context[f"{field}_html"] = render_markdown(getattr(obj, field, ""))
    return context


@require_POST
def markdown_preview(request):
    source = request.POST.get("source", "")
    if len(source) > 100000:
        return HttpResponseBadRequest("Markdown source exceeds 100000 characters.")
    return JsonResponse({"html": render_markdown(source)})


def question_list(request):
    raw_tag_values = parameter_values(request.GET, "tag", "tags")
    selected_tag_ids = list(canonical_tag_ids(raw_tag_values))
    question_params = request.GET.copy()
    question_params.pop("tags", None)
    question_params.setlist("tag", selected_tag_ids)
    questions = build_question_queryset(question_params)
    page = Paginator(questions, 20).get_page(request.GET.get("page", 1))
    subjects = list(Subject.objects.all())
    sections = list(Section.objects.select_related("subject"))
    tags = list(active_tag_queryset())
    all_tags = list(Tag.objects.select_related("parent").order_by("name", "id"))
    knowledge_cards = list(KnowledgeCard.objects.order_by("name", "id"))
    mastery_choices = list(Question.MASTERY_CHOICES)
    value_labels = {
        "subject": {str(item.pk): item.name for item in subjects},
        "section": {str(item.pk): item.name for item in sections},
        "tag": {str(item.pk): item.name for item in all_tags},
        "knowledge_card": {str(item.pk): item.name for item in knowledge_cards},
        "mastery": dict(mastery_choices),
        "due": {
            value: "仅显示到期题目"
            for value in ("1", "true", "yes", "on", "due", "overdue")
        },
    }
    query = (request.GET.get("q") or request.GET.get("keyword") or "").strip()
    due_values = parameter_values(request.GET, "due", "overdue", "is_due")
    selected_due = due_filter_is_active(request.GET)
    active_filters = []
    filter_definitions = (
        ("q", "关键词", ("q", "keyword")),
        ("subject", "科目", ("subject", "subjects")),
        ("section", "章节", ("section", "sections")),
        ("tag", "标签", ("tag", "tags")),
        (
            "knowledge_card",
            "知识卡片",
            ("knowledge_card", "knowledge_cards", "card"),
        ),
        ("mastery", "掌握程度", ("mastery", "masteries")),
        ("due", "到期", ("due", "overdue", "is_due")),
    )
    for key, label, aliases in filter_definitions:
        if key == "q" and query:
            values = [query]
        elif key == "tag":
            values = selected_tag_ids
        else:
            values = parameter_values(request.GET, *aliases)
        if key == "due":
            values = [due_values[0]] if selected_due else []
        for value in values:
            lookup_value = value.lower() if key == "due" else value
            readable_value = value_labels.get(key, {}).get(lookup_value, value)
            active_filters.append(
                {
                    "key": key,
                    "aliases": ",".join(aliases),
                    "value": value,
                    "label": f"{label}: {readable_value}",
                }
            )
    for question in page.object_list:
        question.statement_html = render_markdown(question.statement)
        question.image_attachments = [
            attachment
            for attachment in question.attachments.all()
            if attachment.file_kind == "image"
        ]
    pagination_params = request.GET.copy()
    pagination_params.pop("page", None)
    pagination_params.pop("tags", None)
    pagination_params.setlist("tag", selected_tag_ids)
    subject_ids = parameter_values(request.GET, "subject", "subjects")
    tag_picker = build_tag_picker(
        subject_ids,
        selected_tag_ids,
        query=request.GET.get("tag_picker_q", ""),
        group=request.GET.get("tag_picker_group"),
        page=request.GET.get("tag_picker_page", 1),
    )
    context = {
        "questions": page,
        "page_obj": page,
        "paginator": page.paginator,
        "subjects": subjects,
        "sections": sections,
        "tags": tags,
        "all_tags": all_tags,
        "knowledge_cards": knowledge_cards,
        "mastery_choices": mastery_choices,
        "active_filters": active_filters,
        "pagination_query": pagination_params.urlencode(),
        "selected_subjects": parameter_values(request.GET, "subject", "subjects"),
        "selected_sections": parameter_values(request.GET, "section", "sections"),
        "selected_tags": selected_tag_ids,
        "selected_cards": parameter_values(request.GET, "knowledge_card", "knowledge_cards", "card"),
        "selected_mastery": parameter_values(request.GET, "mastery", "masteries"),
        "selected_due": selected_due,
        "query": query,
        "tag_picker": tag_picker,
    }
    return render(request, "question_bank/question_list.html", context)


@require_GET
def tag_suggestions(request):
    query = request.GET.get("q", "")
    if len(query) > 100:
        return HttpResponseBadRequest("Tag suggestion query exceeds 100 characters.")
    subject_ids = parameter_values(request.GET, "subject", "subjects")
    selected_tag_ids = canonical_tag_ids(parameter_values(request.GET, "selected_tag"))
    items = search_tag_suggestions(query, subject_ids, selected_tag_ids)
    return JsonResponse(
        {
            "items": [
                {
                    "id": item["canonical_id"],
                    "canonical_id": item["canonical_id"],
                    "name": item["name"],
                    "category": item.get("category_name") or "Other",
                    "category_name": item.get("category_name") or "Other",
                    "category_id": item.get("category_id"),
                    "depth": item.get("depth", 0),
                    "count": item.get("count", 0),
                }
                for item in items
            ]
        }
    )


def _question_form_context(form, title, submitted_intent="", question=None, conflict=False, request=None):
    existing_attachments = list(question.attachments.all()) if question and not question._state.adding else []
    post = request.POST if request and request.method == "POST" else None
    removed_ids = set(post.getlist("remove_attachment")) if post is not None else set()
    attachment_controls = [
        {
            "attachment": attachment,
            "position": post.get(f"attachment_position_{attachment.pk}", str(attachment.sort_order))
            if post is not None else str(attachment.sort_order),
            "removed": str(attachment.pk) in removed_ids,
        }
        for attachment in existing_attachments
    ]
    return {
        "form": form,
        "page_title": title,
        "submitted_intent": submitted_intent,
        "question": question,
        "question_version": build_question_version(question) if question and not question._state.adding else "",
        "existing_attachments": existing_attachments,
        "attachment_controls": attachment_controls,
        "needs_upload_reselection": bool(
            form.is_bound and request and request.FILES.getlist("attachments")
        ),
        "conflict": conflict,
        "subjects": Subject.objects.all(),
        "sections": Section.objects.select_related("subject"),
    }


def _save_question(request, submitted_intent, pk=None):
    created_names = []
    try:
        with transaction.atomic():
            question = (
                get_object_or_404(
                    Question.objects.select_for_update(), pk=pk, deleted_at__isnull=True
                )
                if pk else Question()
            )
            if pk and not verify_question_version(question, request.POST.get("question_version", "")):
                form = QuestionForm(
                    request.POST, request.FILES or None, instance=question,
                    save_intent=submitted_intent,
                )
                form.add_error(None, "题目已被修改，请检查最新内容后重新保存。")
                return form, question, True

            form = QuestionForm(
                request.POST, request.FILES or None, instance=question,
                save_intent=submitted_intent,
            )
            if not form.is_valid():
                return form, question, False
            uploads = form.cleaned_data.get("attachments", [])
            try:
                plan = parse_attachment_plan(request.POST, uploads, question)
            except AttachmentPlanValidationError as exc:
                form.add_error(None, str(exc))
                return form, question, False

            try:
                with transaction.atomic():
                    question = form.save(commit=False)
                    question.save()
                    form.save_m2m()
                    apply_attachment_plan(question, uploads, plan, created_names)
            except AttachmentPlanValidationError as exc:
                form.add_error(None, str(exc))
                return form, question if pk else None, False
            return None, question, False
    except Exception:
        cleanup_unreferenced_files(
            created_names, QuestionAttachment._meta.get_field("file").storage
        )
        raise


@require_http_methods(["GET", "POST"])
def question_create(request):
    submitted_intent = request.POST.get("save_intent", "") if request.method == "POST" else ""
    if request.method == "POST":
        form, question, _ = _save_question(request, submitted_intent)
        if form is None:
            return redirect(f"{reverse('question-edit', kwargs={'pk': question.pk})}?saved=1")
    else:
        form, question = QuestionForm(), None
    return render(
        request,
        "question_bank/question_form.html",
        _question_form_context(form, "新建题目", submitted_intent, question, request=request),
    )


@require_http_methods(["GET", "POST"])
def question_edit(request, pk):
    question = get_object_or_404(Question, pk=pk, deleted_at__isnull=True)
    submitted_intent = request.POST.get("save_intent", "") if request.method == "POST" else ""
    if request.method == "POST":
        form, _, conflict = _save_question(request, submitted_intent, pk=pk)
        if form is None:
            return redirect(f"{reverse('question-edit', kwargs={'pk': pk})}?saved=1")
        question = get_object_or_404(Question, pk=pk, deleted_at__isnull=True)
    else:
        form = QuestionForm(instance=question)
        conflict = False
    return render(
        request,
        "question_bank/question_form.html",
        _question_form_context(form, "编辑题目", submitted_intent, question, conflict, request),
        status=409 if conflict else 200,
    )


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
    workspace = build_tag_workspace(request.GET, tag)
    form_target_tag = tag or (workspace["selected_tag"] if request.method == "GET" else None)
    form = TagForm(request.POST or None, instance=tag if request.method == "POST" else workspace["selected_tag"])
    if request.method == "POST" and form.is_valid():
        saved_tag = form.save()
        messages.success(request, "标签已保存。")
        return _tag_workspace_redirect(workspace, selected=saved_tag.pk)
    return render(
        request,
        "question_bank/tag_manage.html",
        {"form": form, "editing_tag": tag, "form_target_tag": form_target_tag, **workspace},
    )


@require_POST
def tag_rename(request, pk):
    tag = get_object_or_404(Tag, pk=pk)
    form = TagForm(request.POST, instance=tag)
    if form.is_valid():
        form.save()
    return redirect("tag-manage")


def _tag_workspace_redirect(workspace, *, selected=None):
    query = workspace["state_query"]
    if selected is not None:
        query = f"{query}&selected={selected}" if query else f"selected={selected}"
    url = reverse("tag-manage")
    return HttpResponseRedirect(f"{url}?{query}" if query else url)


def _tag_confirmation_context(request, source, operation, *, error="", selected_target=""):
    workspace = build_tag_workspace(request.GET)
    query = workspace["state_query"]
    selected_query = f"{query}&selected={source.pk}" if query else f"selected={source.pk}"
    selected_target_tag = None
    selected_target_invalid = False
    if selected_target:
        try:
            selected_target_pk = UUID(str(selected_target))
        except (ValueError, TypeError, AttributeError):
            selected_target_pk = None
        if selected_target_pk:
            selected_target_tag = Tag.objects.filter(pk=selected_target_pk).first()
        selected_target_invalid = (
            selected_target_tag is None
            or selected_target_tag.pk == source.pk
            or selected_target_tag.archived
            or bool(selected_target_tag.redirect_to_id)
        )
    return {
        "source": source,
        "operation": operation,
        "error": error,
        "selected_target": selected_target,
        "selected_target_tag": selected_target_tag,
        "selected_target_invalid": selected_target_invalid,
        "merge_targets": Tag.objects.filter(archived=False, redirect_to__isnull=True)
        .exclude(pk=source.pk).order_by("name", "id"),
        "state_query": query,
        "cancel_query": selected_query,
        "selected_kind": workspace["selected_kind"],
        "tag_query": workspace["tag_query"],
        "show_archived": workspace["show_archived"],
    }


@require_GET
def tag_merge_confirm(request, pk):
    source = get_object_or_404(Tag, pk=pk)
    return render(request, "question_bank/tag_confirm.html", _tag_confirmation_context(request, source, "merge"))


@require_GET
def tag_archive_confirm(request, pk):
    source = get_object_or_404(Tag, pk=pk)
    return render(request, "question_bank/tag_confirm.html", _tag_confirmation_context(request, source, "archive"))


@require_POST
def tag_merge(request, pk):
    source = get_object_or_404(Tag, pk=pk)
    target_id = request.POST.get("target") or request.POST.get("target_tag") or ""
    if not target_id:
        return render(request, "question_bank/tag_confirm.html", _tag_confirmation_context(
            request, source, "merge", error="请选择合并目标。"
        ))
    try:
        target_pk = UUID(str(target_id))
    except (ValueError, TypeError, AttributeError):
        target_pk = None
    target = Tag.objects.filter(pk=target_pk).first() if target_pk else None
    if target is None or target.archived or target.redirect_to_id or target.pk == source.pk:
        label = f"“{target.name}”" if target else ""
        return render(request, "question_bank/tag_confirm.html", _tag_confirmation_context(
            request, source, "merge", error=f"目标标签{label}已归档或不可用，请重新选择。", selected_target=target_id
        ))
    try:
        merge_tags(source, target)
        messages.success(request, "标签已合并。")
    except ValueError as exc:
        return render(request, "question_bank/tag_confirm.html", _tag_confirmation_context(
            request, source, "merge", error=str(exc), selected_target=target_id
        ))
    return _tag_workspace_redirect(build_tag_workspace(request.GET))


@require_POST
def tag_archive(request, pk):
    source = get_object_or_404(Tag, pk=pk)
    archive_tag(source)
    messages.success(request, "标签已归档。")
    return _tag_workspace_redirect(build_tag_workspace(request.GET))


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
            "card_types": list(KnowledgeCard.CARD_TYPE_CHOICES) + [
                (value, value)
                for value in KnowledgeCard.objects.exclude(
                    type__in=[key for key, _ in KnowledgeCard.CARD_TYPE_CHOICES]
                ).order_by("type").values_list("type", flat=True).distinct()
            ],
        },
    )


def knowledge_card_detail(request, pk):
    card = get_object_or_404(
        KnowledgeCard.objects.select_related("subject", "section").prefetch_related(
            "questions", "prerequisite_cards"
        ),
        pk=pk,
    )
    context = {
        "card": card,
        "core_content_html": render_markdown(card.core_content or build_core_content(card)),
    }
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


def review_list(request):
    """Review queues: due today, recent mistakes, and overdue questions."""
    queue = request.GET.get("queue", "due")
    if queue not in ("due", "recent", "overdue"):
        queue = "due"
    knowledge_card = request.GET.get("knowledge_card") or request.GET.get("card") or ""
    due_questions = due_today_questions(knowledge_card=knowledge_card or None)
    recent_mistakes = get_recent_mistakes(knowledge_card=knowledge_card or None)
    overdue = get_overdue_questions(knowledge_card=knowledge_card or None)
    queues = {
        "due": due_questions,
        "recent": recent_mistakes,
        "overdue": overdue,
    }
    queue_tabs = [
        {
            "key": key,
            "title": title,
            "count": queues[key].count(),
            "is_current": key == queue,
        }
        for key, title in (
            ("due", "今日到期"),
            ("recent", "最近错误"),
            ("overdue", "逾期"),
        )
    ]
    return render(
        request,
        "question_bank/review_list.html",
        {
            "queue": queue,
            "questions": queues[queue],
            "queue_tabs": queue_tabs,
            "due_questions": due_questions,
            "recent_mistakes": recent_mistakes,
            "overdue_questions": overdue,
            "knowledge_cards": KnowledgeCard.objects.order_by("name", "id"),
            "selected_card": str(knowledge_card),
        },
    )


@require_http_methods(["GET", "POST"])
def review_detail(request, pk):
    question = get_object_or_404(
        Question.objects.select_related("subject", "section").prefetch_related("knowledge_cards"),
        pk=pk,
        deleted_at__isnull=True,
        archived=False,
    )
    error = ""
    if request.method == "POST":
        result = request.POST.get("result", "")
        try:
            apply_review(
                question,
                result,
                duration_seconds=request.POST.get("duration_seconds"),
                note=request.POST.get("note", ""),
            )
        except (TypeError, ValueError) as exc:
            error = str(exc)
        else:
            return redirect(f"{reverse('review-detail', kwargs={'pk': question.pk})}?submitted=1")
    context = {"question": question, "error": error, "show_reference": bool(request.GET.get("show_reference") or request.GET.get("submitted"))}
    context.update(_markdown_context(question, ["statement", "personal_solution", "error_note"]))
    if context["show_reference"]:
        context.update(_markdown_context(question, ["reference_solution"]))
    return render(request, "question_bank/review_detail.html", context)


def stats(request):
    statistics = get_statistics()
    mastery_rows = [
        {
            "key": key,
            "label": label,
            "count": statistics["questions_by_mastery"].get(key, 0),
        }
        for key, label in Question.MASTERY_CHOICES
    ]
    return render(
        request,
        "question_bank/stats.html",
        {"stats": statistics, "mastery_rows": mastery_rows},
    )
