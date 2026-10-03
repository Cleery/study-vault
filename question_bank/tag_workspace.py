"""Read-only state for the tag management workbench."""

from urllib.parse import urlencode
from uuid import UUID

from django.db.models import Count

from .models import Tag


def build_tag_workspace(params, editing_tag=None):
    """Return normalized GET state and matching tags without changing data."""
    kinds = dict(Tag.KIND_CHOICES)
    selected_kind = params.get("kind", "")
    if selected_kind not in kinds:
        selected_kind = ""
    query = (params.get("q") or "").strip()[:100]
    show_archived = params.get("show_archived") == "1"

    def query_string(**changes):
        values = {"kind": selected_kind, "q": query}
        if show_archived:
            values["show_archived"] = "1"
        values.update(changes)
        return urlencode({key: value for key, value in values.items() if value})

    tags = Tag.objects.select_related("parent")
    if not show_archived:
        tags = tags.filter(archived=False)
    if query:
        tags = tags.filter(name__icontains=query)

    counts = dict(tags.values("kind").annotate(total=Count("id")).values_list("kind", "total"))
    total = sum(counts.values())
    categories = [{"kind": "", "label": "全部标签", "count": total, "query": query_string(kind="")}]
    categories.extend(
        {"kind": kind, "label": label, "count": counts.get(kind, 0), "query": query_string(kind=kind)}
        for kind, label in Tag.KIND_CHOICES
    )
    if selected_kind:
        tags = tags.filter(kind=selected_kind)

    selected_tag = editing_tag
    if selected_tag is None:
        try:
            selected_id = UUID(str(params.get("selected", "")))
        except (ValueError, TypeError, AttributeError):
            selected_id = None
        if selected_id is not None:
            selected_tag = Tag.objects.select_related("parent").filter(pk=selected_id).first()

    return {
        "selected_kind": selected_kind,
        "tag_query": query,
        "show_archived": show_archived,
        "selected_tag": selected_tag,
        "tag_categories": categories,
        "tags": tags.order_by("name", "id"),
        "state_query": query_string(),
        "archive_query": query_string(show_archived="" if show_archived else "1"),
        "category_total": total,
    }
