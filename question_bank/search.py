"""Question search and tag relationship services.

The module keeps query parsing out of views so the same behavior is available to
HTML views and tests. Query parameters may be repeated or supplied as comma
separated values.
"""

from __future__ import annotations

from datetime import datetime
from typing import Iterable

from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q, QuerySet
from django.utils import timezone

from .models import Question, Tag


SEARCH_FIELDS = (
    "title",
    "statement",
    "personal_solution",
    "reference_solution",
    "error_note",
)


def parameter_values(params, *names: str) -> list[str]:
    """Return normalized repeated and comma-separated query parameter values."""
    values: list[str] = []
    for name in names:
        if hasattr(params, "getlist"):
            raw = params.getlist(name)
        else:
            raw = params.get(name, [])
            if isinstance(raw, str):
                raw = [raw]
        for value in raw or []:
            values.extend(item.strip() for item in str(value).split(",") if item.strip())
    return list(dict.fromkeys(values))


def _tag_descendant_ids(tag_ids: Iterable) -> set:
    """Resolve redirects and include descendants for parent-tag filtering."""
    requested = {str(value) for value in tag_ids}
    if not requested:
        return set()
    tags = list(Tag.objects.all().only("id", "parent_id", "redirect_to_id"))
    by_id = {str(tag.pk): tag for tag in tags}

    canonical_ids = set()
    for tag_id in requested:
        tag = by_id.get(tag_id)
        if not tag:
            continue
        visited = set()
        while tag.redirect_to_id and str(tag.pk) not in visited:
            visited.add(str(tag.pk))
            target = by_id.get(str(tag.redirect_to_id))
            if target is None:
                break
            tag = target
        canonical_ids.add(str(tag.pk))

    parent_map: dict[str, set[str]] = {}
    for tag in tags:
        if tag.parent_id:
            parent_map.setdefault(str(tag.parent_id), set()).add(str(tag.pk))
    expanded = set(canonical_ids)
    pending = list(canonical_ids)
    while pending:
        parent_id = pending.pop()
        for child_id in parent_map.get(parent_id, set()):
            if child_id not in expanded:
                expanded.add(child_id)
                pending.append(child_id)

    # Historical relationships may still point to a source tag. Include all
    # source tags whose redirect ultimately resolves to one of the targets.
    for tag in tags:
        current = tag
        visited = set()
        while current.redirect_to_id and str(current.pk) not in visited:
            visited.add(str(current.pk))
            target = by_id.get(str(current.redirect_to_id))
            if target is None:
                break
            current = target
        if str(current.pk) in expanded:
            expanded.add(str(tag.pk))
    return expanded


def build_question_queryset(params=None, *, queryset: QuerySet | None = None, now=None) -> QuerySet:
    """Build the canonical question query from GET-like parameters.

    Different filter fields are combined with AND. Values in one field are
    combined with OR by using ``__in`` or a single disjunction.
    """
    params = params or {}
    queryset = queryset or Question.objects.all()
    queryset = queryset.filter(deleted_at__isnull=True)

    include_archived = parameter_values(params, "include_archived", "archived")
    if not include_archived or include_archived[0].lower() not in {"1", "true", "yes", "on"}:
        queryset = queryset.filter(archived=False)

    keyword = (params.get("q") or params.get("keyword") or "").strip()
    if keyword:
        query = Q()
        for field in SEARCH_FIELDS:
            query |= Q(**{f"{field}__icontains": keyword})
        queryset = queryset.filter(query)

    subject_ids = parameter_values(params, "subject", "subjects")
    section_ids = parameter_values(params, "section", "sections")
    mastery_values = parameter_values(params, "mastery", "masteries")
    card_ids = parameter_values(params, "knowledge_card", "knowledge_cards", "card")
    tag_values = parameter_values(params, "tag", "tags")
    if subject_ids:
        queryset = queryset.filter(subject_id__in=subject_ids)
    if section_ids:
        queryset = queryset.filter(section_id__in=section_ids)
    if mastery_values:
        queryset = queryset.filter(mastery__in=mastery_values)
    if card_ids:
        queryset = queryset.filter(knowledge_cards__pk__in=card_ids)
    if tag_values:
        resolved_ids = _tag_descendant_ids(tag_values)
        queryset = queryset.filter(tags__pk__in=resolved_ids or tag_values)

    due_values = parameter_values(params, "due", "overdue", "is_due")
    if due_values and due_values[0].lower() in {"1", "true", "yes", "on", "due", "overdue"}:
        now = now or timezone.now()
        queryset = queryset.filter(next_review_at__isnull=False, next_review_at__lte=now)

    return queryset.select_related("subject", "section").prefetch_related("tags", "knowledge_cards").distinct().order_by(
        "-updated_at", "id"
    )


def search_questions(params=None, *, queryset: QuerySet | None = None, now=None):
    """Alias used by callers that want the search query without pagination."""
    return build_question_queryset(params, queryset=queryset, now=now)


def paginate_questions(params=None, *, per_page=20, queryset=None, now=None):
    """Return a Django paginator page using the canonical stable ordering."""
    params = params or {}
    paginator = Paginator(build_question_queryset(params, queryset=queryset, now=now), per_page)
    page_number = params.get("page", 1)
    return paginator.get_page(page_number)


def resolve_tag(tag_or_id) -> Tag:
    tag = tag_or_id if isinstance(tag_or_id, Tag) else Tag.objects.get(pk=tag_or_id)
    visited = set()
    while tag.redirect_to_id and tag.pk not in visited:
        visited.add(tag.pk)
        target = tag.redirect_to
        if target is None:
            break
        tag = target
    return tag


@transaction.atomic
def rename_tag(tag: Tag, name: str, *, parent: Tag | None = None) -> Tag:
    tag.name = name.strip()
    tag.parent = parent
    tag.save()
    return tag


@transaction.atomic
def merge_tags(source: Tag, target: Tag) -> Tag:
    """Move question relations to target and flatten every redirect in chain."""
    source = source if isinstance(source, Tag) else Tag.objects.select_for_update().get(pk=source)
    target = target if isinstance(target, Tag) else Tag.objects.select_for_update().get(pk=target)
    source = Tag.objects.select_for_update().get(pk=source.pk)
    target = resolve_tag(Tag.objects.select_for_update().get(pk=target.pk))
    if source.pk == target.pk:
        raise ValueError("标签不能合并到自身或自身的重定向目标。")

    # Add the target relationship while retaining the source relationship so
    # historical results can continue to display the original tag.
    source_ids = {source.pk}
    for candidate in Tag.objects.select_for_update().filter(redirect_to__isnull=False):
        if candidate.pk == source.pk:
            continue
        try:
            if resolve_tag(candidate).pk == source.pk:
                source_ids.add(candidate.pk)
        except Tag.DoesNotExist:
            continue
    question_ids = Question.objects.filter(tags__pk__in=source_ids).values_list("pk", flat=True).distinct()
    target.questions.add(*question_ids)
    source.redirect_to = target
    source.archived = True
    source.save(update_fields=["redirect_to", "archived", "updated_at"])

    # Compress both old and indirect redirects to the final target.
    for candidate in Tag.objects.select_for_update().filter(redirect_to__isnull=False):
        if candidate.pk == target.pk:
            continue
        try:
            if resolve_tag(candidate).pk == target.pk and candidate.redirect_to_id != target.pk:
                candidate.redirect_to = target
                candidate.save(update_fields=["redirect_to", "updated_at"])
        except Tag.DoesNotExist:
            continue
    return target


@transaction.atomic
def archive_tag(tag: Tag) -> Tag:
    tag = tag if isinstance(tag, Tag) else Tag.objects.get(pk=tag)
    tag.archived = True
    tag.save(update_fields=["archived", "updated_at"])
    return tag


def active_tag_queryset(*, include_archived=False):
    queryset = Tag.objects.select_related("parent").order_by("name", "id")
    return queryset if include_archived else queryset.filter(archived=False)

