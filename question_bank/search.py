"""Question search and tag relationship services.

The module keeps query parsing out of views so the same behavior is available to
HTML views and tests. Query parameters may be repeated or supplied as comma
separated values.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Iterable

from django.core.cache import cache
from django.core.paginator import Paginator
from django.db import connection, transaction
from django.db.models import Q, QuerySet
from django.db.models.expressions import RawSQL
from django.utils import timezone

from .models import Question, Tag


SEARCH_FIELDS = (
    "title",
    "statement",
    "personal_solution",
    "reference_solution",
    "error_note",
)
DUE_FILTER_VALUES = {"1", "true", "yes", "on", "due", "overdue"}
TAG_PICKER_CACHE_KEY = "question_bank:tag-picker:hierarchy:v1"
TAG_PICKER_CACHE_TIMEOUT = 300
TAG_RELATION_BATCH_SIZE = 500
logger = logging.getLogger(__name__)


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


def due_filter_is_active(params) -> bool:
    values = parameter_values(params, "due", "overdue", "is_due")
    return bool(values and values[0].lower() in DUE_FILTER_VALUES)


def _tag_value(value) -> str:
    return str(getattr(value, "pk", value))


def _iter_values(values):
    if values is None:
        return ()
    if isinstance(values, str):
        return (item.strip() for item in values.split(",") if item.strip())
    return values


def _build_tag_hierarchy_snapshot() -> dict:
    """Return a primitive, cacheable snapshot of the tag hierarchy.

    Redirect resolution is kept in this snapshot so a picker request never
    performs one query per tag. Parent cycles can exist in legacy rows, so the
    traversal records the affected chain and treats those tags as ``Other``.
    """
    rows = list(Tag.objects.all().only("id", "name", "parent_id", "archived", "redirect_to_id"))
    by_id = {
        str(row.pk): {
            "id": str(row.pk),
            "name": row.name,
            "parent_id": str(row.parent_id) if row.parent_id else None,
            "archived": bool(row.archived),
            "redirect_to_id": str(row.redirect_to_id) if row.redirect_to_id else None,
        }
        for row in rows
    }

    def resolve_redirect(tag_id):
        current = str(tag_id)
        visited = set()
        while True:
            if current in visited:
                return None
            visited.add(current)
            row = by_id.get(current)
            if row is None:
                return None
            target = row["redirect_to_id"]
            if not target:
                return current if not row["archived"] else None
            current = target

    canonical_ids = {
        tag_id
        for tag_id, row in by_id.items()
        if not row["archived"] and not row["redirect_to_id"]
    }
    relation_ids = {tag_id: {tag_id} for tag_id in canonical_ids}
    relation_to_canonical = {}
    for tag_id in by_id:
        canonical_id = resolve_redirect(tag_id)
        if canonical_id in canonical_ids:
            relation_ids.setdefault(canonical_id, set()).add(tag_id)
            relation_to_canonical[tag_id] = canonical_id

    active_parent = {
        tag_id: row["parent_id"]
        for tag_id, row in by_id.items()
        if tag_id in canonical_ids and row["parent_id"] in canonical_ids
    }
    cycle_affected = set()
    root_by_id = {}
    for tag_id in canonical_ids:
        chain = []
        positions = {}
        current = tag_id
        while current in canonical_ids:
            if current in positions:
                cycle = chain[positions[current] :]
                cycle_affected.update(chain)
                logger.warning("Legacy tag parent cycle detected: %s", ",".join(sorted(cycle)))
                break
            positions[current] = len(chain)
            chain.append(current)
            parent_id = active_parent.get(current)
            if not parent_id:
                root_by_id[tag_id] = current
                break
            current = parent_id

    # A tag below a legacy cycle is also kept out of a category. This avoids
    # presenting an unstable root while retaining the tag as an independent
    # selectable item.
    for tag_id in canonical_ids:
        if tag_id in root_by_id or tag_id in cycle_affected:
            continue
        current = tag_id
        visited = set()
        while current in active_parent and current not in visited:
            visited.add(current)
            current = active_parent[current]
            if current in cycle_affected:
                cycle_affected.update(visited)
                break
        if current not in cycle_affected:
            root_by_id[tag_id] = tag_id if current not in canonical_ids else current

    # Recompute roots for active chains. An archived or redirected parent ends
    # the chain at the current active tag.
    for tag_id in canonical_ids - cycle_affected:
        if tag_id in root_by_id:
            continue
        current = tag_id
        seen = set()
        while True:
            if current in seen or current in cycle_affected:
                cycle_affected.update(seen)
                break
            seen.add(current)
            parent_id = by_id[current]["parent_id"]
            if parent_id not in canonical_ids:
                root_by_id[tag_id] = current
                break
            current = parent_id

    children = {tag_id: set() for tag_id in canonical_ids - cycle_affected}
    for tag_id, parent_id in active_parent.items():
        if tag_id in cycle_affected or parent_id in cycle_affected:
            continue
        children.setdefault(parent_id, set()).add(tag_id)

    descendants = {tag_id: set() for tag_id in canonical_ids}
    for root_id in canonical_ids - cycle_affected:
        pending = list(children.get(root_id, ()))
        while pending:
            child_id = pending.pop()
            if child_id in descendants[root_id]:
                continue
            descendants[root_id].add(child_id)
            pending.extend(children.get(child_id, ()))

    category_members = {}
    for tag_id, root_id in root_by_id.items():
        if tag_id in cycle_affected:
            continue
        category_members.setdefault(root_id, set()).add(tag_id)
    categories = {
        root_id: set(members)
        for root_id, members in category_members.items()
        if len(members) > 1
    }
    other_ids = {
        tag_id
        for tag_id in canonical_ids
        if tag_id in cycle_affected or tag_id not in {member for members in categories.values() for member in members}
    }

    depth = {}
    for tag_id, root_id in root_by_id.items():
        if tag_id in cycle_affected:
            continue
        current = tag_id
        value = 0
        seen = set()
        while current != root_id and current not in seen:
            seen.add(current)
            current = active_parent.get(current)
            value += 1
        depth[tag_id] = value if current == root_id else 0

    return {
        "tags": by_id,
        "canonical_ids": tuple(sorted(canonical_ids)),
        "relation_ids": {key: tuple(sorted(value)) for key, value in relation_ids.items()},
        "relation_to_canonical": relation_to_canonical,
        "parent": active_parent,
        "children": {key: tuple(sorted(value)) for key, value in children.items()},
        "descendants": {key: tuple(sorted(value)) for key, value in descendants.items()},
        "root_by_id": root_by_id,
        "categories": {key: tuple(sorted(value)) for key, value in categories.items()},
        "other_ids": tuple(sorted(other_ids)),
        "depth": depth,
    }


def _tag_hierarchy_snapshot() -> dict:
    if connection.in_atomic_block:
        shared_snapshot = cache.get(TAG_PICKER_CACHE_KEY)
        if shared_snapshot is not None:
            return shared_snapshot
        atomic_token = id(connection.atomic_blocks[-1])
        local_entry = getattr(connection, "_tag_picker_local_snapshot", None)
        if local_entry is not None and local_entry[0] == atomic_token:
            return local_entry[1]
        local_snapshot = _build_tag_hierarchy_snapshot()
        connection._tag_picker_local_snapshot = (atomic_token, local_snapshot)
        return local_snapshot
    if hasattr(connection, "_tag_picker_local_snapshot"):
        connection._tag_picker_local_snapshot = None
    snapshot = cache.get(TAG_PICKER_CACHE_KEY)
    if snapshot is None:
        snapshot = _build_tag_hierarchy_snapshot()
        cache.set(TAG_PICKER_CACHE_KEY, snapshot, TAG_PICKER_CACHE_TIMEOUT)
    return snapshot


def invalidate_tag_picker_cache() -> None:
    cache.delete(TAG_PICKER_CACHE_KEY)


def canonical_tag_ids(tag_ids: Iterable) -> tuple[str, ...]:
    """Resolve requested IDs to active canonical tags in input order."""
    snapshot = _tag_hierarchy_snapshot()
    result = []
    seen = set()
    relation_to_canonical = snapshot["relation_to_canonical"]
    for value in _iter_values(tag_ids):
        canonical_id = relation_to_canonical.get(_tag_value(value))
        if canonical_id and canonical_id not in seen:
            result.append(canonical_id)
            seen.add(canonical_id)
    return tuple(result)


def tag_relation_ids(canonical_ids: Iterable) -> set[str]:
    """Return canonical and historical source IDs for active canonical IDs."""
    snapshot = _tag_hierarchy_snapshot()
    result = set()
    for value in _iter_values(canonical_ids):
        canonical_id = snapshot["relation_to_canonical"].get(_tag_value(value), _tag_value(value))
        result.update(snapshot["relation_ids"].get(canonical_id, ()))
    return result


def _canonical_descendant_ids(canonical_ids: Iterable) -> set[str]:
    snapshot = _tag_hierarchy_snapshot()
    result = set()
    for value in _iter_values(canonical_ids):
        canonical_id = snapshot["relation_to_canonical"].get(_tag_value(value), _tag_value(value))
        if canonical_id in snapshot["canonical_ids"]:
            result.add(canonical_id)
            result.update(snapshot["descendants"].get(canonical_id, ()))
    return result


def _tag_filter_q(relation_ids: Iterable[str]) -> Q | None:
    values = sorted({_tag_value(value) for value in relation_ids})
    if not values:
        return None
    if len(values) > TAG_RELATION_BATCH_SIZE:
        literals = []
        for value in values:
            try:
                normalized = uuid.UUID(value).hex
            except (ValueError, AttributeError):
                continue
            literals.append(f"('{normalized}')")
        if not literals:
            return None
        # A VALUES relation has no bound parameters, so the statement remains
        # valid when SQLite is configured with its 999-variable limit.
        return Q(tags__pk__in=RawSQL("VALUES " + ",".join(literals), []))
    query = Q(tags__pk__in=values[:TAG_RELATION_BATCH_SIZE])
    for start in range(TAG_RELATION_BATCH_SIZE, len(values), TAG_RELATION_BATCH_SIZE):
        query |= Q(tags__pk__in=values[start : start + TAG_RELATION_BATCH_SIZE])
    return query


def _tag_descendant_ids(tag_ids: Iterable) -> set:
    """Return relation IDs for canonical tags and their active descendants."""
    return tag_relation_ids(_canonical_descendant_ids(tag_ids))


def build_question_queryset(params=None, *, queryset: QuerySet | None = None, now=None) -> QuerySet:
    """Build the canonical question query from GET-like parameters.

    Different filter fields are combined with AND. Values in one field are
    combined with OR by using ``__in`` or a single disjunction.
    """
    params = params or {}
    queryset = queryset or Question.objects.all()
    queryset = queryset.filter(deleted_at__isnull=True)

    draft_values = parameter_values(params, "draft")
    draft_only = bool(draft_values and draft_values[0].lower() in {"1", "true", "yes", "on"})
    include_archived = parameter_values(params, "include_archived", "archived")
    if draft_only or not include_archived or include_archived[0].lower() not in {"1", "true", "yes", "on"}:
        queryset = queryset.filter(archived=False)
    if draft_only:
        queryset = queryset.filter(draft=True)

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
        tag_filter = _tag_filter_q(resolved_ids)
        if tag_filter is not None:
            queryset = queryset.filter(tag_filter)

    if due_filter_is_active(params):
        now = now or timezone.now()
        queryset = queryset.filter(next_review_at__isnull=False, next_review_at__lte=now)

    return queryset.select_related("subject", "section").prefetch_related(
        "tags", "knowledge_cards", "attachments"
    ).distinct().order_by("-updated_at", "id")


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


def _subject_values(subject_ids: Iterable) -> tuple[str, ...]:
    values = []
    seen = set()
    for value in _iter_values(subject_ids):
        normalized = _tag_value(value)
        if normalized and normalized not in seen:
            values.append(normalized)
            seen.add(normalized)
    return tuple(values)


def _question_ids_by_canonical_tag(
    snapshot: dict, subject_ids: Iterable, tag_ids: Iterable[str] | None = None
) -> dict[str, set]:
    """Aggregate relation rows once and retain distinct question IDs in Python."""
    through = Question.tags.through
    question_filter = {
        "question__deleted_at__isnull": True,
        "question__archived": False,
    }
    subjects = _subject_values(subject_ids)
    if subjects:
        question_filter["question__subject_id__in"] = subjects
    if tag_ids is None:
        target_ids = set(snapshot["canonical_ids"])
    else:
        target_ids = set()
        for tag_id in tag_ids:
            if tag_id in snapshot["canonical_ids"]:
                target_ids.add(tag_id)
                target_ids.update(snapshot["descendants"].get(tag_id, ()))
    rows = through.objects.filter(**question_filter).values_list("tag_id", "question_id")
    by_tag = {tag_id: set() for tag_id in target_ids}
    relation_to_canonical = snapshot["relation_to_canonical"]
    for relation_id, question_id in rows.iterator():
        canonical_id = relation_to_canonical.get(str(relation_id))
        if canonical_id:
            by_tag.setdefault(canonical_id, set()).add(question_id)

    # Aggregate descendants bottom-up, avoiding one query per picker item.
    children = snapshot["children"]
    ordered = sorted(target_ids, key=lambda value: snapshot["depth"].get(value, 0), reverse=True)
    for tag_id in ordered:
        for child_id in children.get(tag_id, ()):
            by_tag.setdefault(tag_id, set()).update(by_tag.get(child_id, set()))
    return by_tag


def _tag_item(snapshot: dict, tag_id: str, count: int, *, category_id=None) -> dict:
    row = snapshot["tags"][tag_id]
    root_id = category_id or snapshot["root_by_id"].get(tag_id)
    if category_id is None and root_id not in snapshot["categories"]:
        root_id = "other"
    return {
        "id": tag_id,
        "value": tag_id,
        "canonical_id": tag_id,
        "name": row["name"],
        "label": row["name"],
        "count": int(count),
        "question_count": int(count),
        "category": root_id,
        "category_id": root_id,
        "category_name": snapshot["tags"].get(root_id, {}).get("name") if root_id in snapshot["tags"] else "Other",
        "depth": snapshot["depth"].get(tag_id, 0),
    }


def _item_sort_key(item: dict):
    return (-int(item.get("count", 0)), str(item.get("name", "")).casefold(), str(item.get("id", "")))


def _picker_category(snapshot, category_id, member_ids, counts, selected, *, group, page):
    visible = [
        _tag_item(snapshot, tag_id, len(counts.get(tag_id, set())), category_id=category_id)
        for tag_id in member_ids
        if tag_id != category_id
        and tag_id not in selected
        and counts.get(tag_id)
        and len(counts.get(tag_id, set())) > 0
    ]
    visible.sort(key=_item_sort_key)
    offset = 0
    page_value = 1
    if str(group or "") == str(category_id):
        try:
            page_value = max(1, int(page))
        except (TypeError, ValueError):
            page_value = 1
        offset = (page_value - 1) * 20
        page_items = visible[offset : offset + 20]
    else:
        page_items = visible[:6]
    total_count = len(set().union(*(counts.get(tag_id, set()) for tag_id in member_ids))) if member_ids else 0
    all_item = {
        "id": category_id,
        "canonical_id": category_id,
        "name": f"All in {snapshot['tags'][category_id]['name']}",
        "count": total_count,
        "category": category_id,
        "category_name": snapshot["tags"][category_id]["name"],
        "depth": 0,
        "is_category": True,
    }
    return {
        "id": category_id,
        "name": snapshot["tags"][category_id]["name"],
        "count": total_count,
        "all": all_item,
        "all_item": all_item,
        "items": page_items,
        "page_items": page_items,
        "members": page_items,
        "tags": page_items,
        "page": page_value,
        "has_next": offset + len(page_items) < len(visible),
        "total_items": len(visible),
    }


def build_tag_picker(
    subject_ids: Iterable,
    selected_tag_ids: Iterable,
    *,
    query: str = "",
    group=None,
    page: int = 1,
) -> dict:
    """Build the bounded, subject-scoped native tag-picker context."""
    selected_values = tuple(_iter_values(selected_tag_ids))
    if selected_values and connection.in_atomic_block:
        snapshot = _build_tag_hierarchy_snapshot()
        connection._tag_picker_local_snapshot = (id(connection.atomic_blocks[-1]), snapshot)
    elif connection.in_atomic_block:
        connection._tag_picker_local_snapshot = None
        snapshot = _tag_hierarchy_snapshot()
    else:
        snapshot = _tag_hierarchy_snapshot()
    selected_canonical = tuple(
        dict.fromkeys(
            snapshot["relation_to_canonical"].get(_tag_value(value))
            for value in selected_values
            if snapshot["relation_to_canonical"].get(_tag_value(value))
        )
    )
    selected = set(selected_canonical)
    counts = _question_ids_by_canonical_tag(snapshot, subject_ids)
    selected_items = [
        _tag_item(snapshot, tag_id, len(counts.get(tag_id, set())))
        for tag_id in selected_canonical
        if tag_id in snapshot["tags"]
    ]

    common = [
        _tag_item(snapshot, tag_id, len(counts.get(tag_id, set())))
        for tag_id in snapshot["canonical_ids"]
        if tag_id not in selected and counts.get(tag_id) and len(counts[tag_id]) > 0
    ]
    common.sort(key=_item_sort_key)
    common = common[:8]

    categories = []
    for category_id, member_ids in snapshot["categories"].items():
        category = _picker_category(
            snapshot,
            category_id,
            member_ids,
            counts,
            selected,
            group=group,
            page=page,
        )
        if category["count"] > 0 and (category["items"] or category_id in selected):
            categories.append(category)

    other_members = [tag_id for tag_id in snapshot["other_ids"] if tag_id in snapshot["tags"]]
    other_items = [
        _tag_item(snapshot, tag_id, len(counts.get(tag_id, set())), category_id="other")
        for tag_id in other_members
        if tag_id not in selected and counts.get(tag_id) and len(counts[tag_id]) > 0
    ]
    other_items.sort(key=_item_sort_key)
    other_count = len(set().union(*(counts.get(tag_id, set()) for tag_id in other_members))) if other_members else 0
    if other_count > 0 and (other_items or "other" == str(group or "")):
        try:
            other_page = max(1, int(page)) if str(group or "") == "other" else 1
        except (TypeError, ValueError):
            other_page = 1
        offset = (other_page - 1) * 20 if str(group or "") == "other" else 0
        other_items_page = other_items[offset : offset + (20 if str(group or "") == "other" else 6)]
        other_all = {
            "id": "other",
            "canonical_id": "other",
            "name": "Other",
            "count": other_count,
            "category": "other",
            "category_name": "Other",
            "depth": 0,
            "is_category": True,
        }
        categories.append(
            {
                "id": "other",
                "name": "Other",
                "count": other_count,
                "all": other_all,
                "all_item": other_all,
                "items": other_items_page,
                "page_items": other_items_page,
                "members": other_items_page,
                "tags": other_items_page,
                "page": other_page,
                "has_next": offset + len(other_items_page) < len(other_items),
                "total_items": len(other_items),
            }
        )

    categories.sort(key=_item_sort_key)
    categories = categories[:12]

    prefix = (query or "").strip()
    search_items = []
    if prefix:
        matches = [
            tag_id
            for tag_id in snapshot["canonical_ids"]
            if tag_id not in selected
            and counts.get(tag_id)
            and len(counts[tag_id]) > 0
            and snapshot["tags"][tag_id]["name"].lower().startswith(prefix.lower())
        ]
        search_items = [_tag_item(snapshot, tag_id, len(counts[tag_id])) for tag_id in matches]
        search_items.sort(key=_item_sort_key)
        search_items = search_items[:20]

    result = {
        "selected": selected_items,
        "selected_tags": selected_items,
        "common": common,
        "common_tags": common,
        "categories": categories,
        "category_groups": categories,
        "search": search_items,
        "search_items": search_items,
        "query": prefix,
    }
    return result


def search_tag_suggestions(prefix: str, subject_ids: Iterable, selected_tag_ids: Iterable = ()) -> list[dict]:
    """Return at most twenty active canonical prefix matches."""
    prefix = (prefix or "").strip()
    if not prefix:
        return []
    snapshot = _tag_hierarchy_snapshot()
    selected = set(canonical_tag_ids(selected_tag_ids))
    candidate_ids = list(
        Tag.objects.filter(archived=False, redirect_to__isnull=True, name__istartswith=prefix)
        .order_by("name", "id")
        .values_list("pk", flat=True)[:100]
    )
    candidate_ids = [str(tag_id) for tag_id in candidate_ids if str(tag_id) not in selected]
    counts = _question_ids_by_canonical_tag(snapshot, subject_ids, candidate_ids)
    items = [
        _tag_item(snapshot, tag_id, len(counts.get(tag_id, set())))
        for tag_id in candidate_ids
        if counts.get(tag_id) and len(counts[tag_id]) > 0
    ]
    items.sort(key=_item_sort_key)
    return items[:20]

