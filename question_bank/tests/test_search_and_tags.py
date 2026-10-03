from datetime import timedelta
from pathlib import Path
import sqlite3

import pytest
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from question_bank.forms import QuestionForm, TagForm
from question_bank import search
from question_bank.exporting import import_bundle
from question_bank.models import KnowledgeCard, Question, Section, Subject, Tag
from question_bank.search import (
    build_question_queryset,
    build_tag_picker,
    canonical_tag_ids,
    merge_tags,
    search_tag_suggestions,
    tag_relation_ids,
)


@pytest.fixture
def subject(db):
    return Subject.objects.create(name="数学分析")


@pytest.fixture
def section(subject):
    return Section.objects.create(subject=subject, name="极限")


def make_question(subject, title, **kwargs):
    return Question.objects.create(subject=subject, title=title, draft=False, **kwargs)


@pytest.mark.django_db
def test_tag_workspace_defaults_to_active_tags(client):
    active = Tag.objects.create(name="活动标签")
    archived = Tag.objects.create(name="归档标签", archived=True)

    response = client.get(reverse("tag-manage"))

    assert response.status_code == 200
    assert [tag.pk for tag in response.context["tags"]] == [active.pk]
    assert archived.pk not in [tag.pk for tag in response.context["tags"]]
    assert response.context["show_archived"] is False


@pytest.mark.django_db
def test_tag_workspace_filters_by_model_kind_choices(client):
    kinds = [Tag.objects.create(name=f"标签{i}", kind=kind) for i, (kind, _) in enumerate(Tag.KIND_CHOICES)]

    response = client.get(reverse("tag-manage"), {"kind": "method"})
    invalid = client.get(reverse("tag-manage"), {"kind": "unknown"})

    assert response.context["selected_kind"] == "method"
    assert [tag.pk for tag in response.context["tags"]] == [kinds[1].pk]
    assert [category["kind"] for category in response.context["tag_categories"]] == [
        "", *(kind for kind, _ in Tag.KIND_CHOICES)
    ]
    assert invalid.context["selected_kind"] == ""
    assert len(invalid.context["tags"]) == len(kinds)


@pytest.mark.django_db
def test_tag_workspace_search_matches_name_and_limits_query(client):
    matching = Tag.objects.create(name="极限方法")
    Tag.objects.create(name="积分方法")

    response = client.get(reverse("tag-manage"), {"q": " 极限 "})
    long_query = client.get(reverse("tag-manage"), {"q": "x" * 101})

    assert response.context["tag_query"] == "极限"
    assert [tag.pk for tag in response.context["tags"]] == [matching.pk]
    assert len(long_query.context["tag_query"]) == 100


@pytest.mark.django_db
def test_tag_workspace_archived_toggle_has_clickable_link(client):
    active = Tag.objects.create(name="活动标签")
    archived = Tag.objects.create(name="归档标签", archived=True)

    response = client.get(reverse("tag-manage"))
    body = response.content.decode()
    shown = client.get(reverse("tag-manage"), {"show_archived": "1"})

    assert 'href="?show_archived=1"' in body
    assert "包含已归档标签" in body
    assert shown.context["show_archived"] is True
    assert {tag.pk for tag in shown.context["tags"]} == {active.pk, archived.pk}
    assert "隐藏已归档标签" in shown.content.decode()


@pytest.mark.django_db
def test_tag_workspace_selects_valid_tag_and_ignores_invalid_uuid(client):
    tag = Tag.objects.create(name="可选标签")

    selected = client.get(reverse("tag-manage"), {"selected": str(tag.pk)})
    invalid = client.get(reverse("tag-manage"), {"selected": "not-a-uuid"})

    assert selected.context["selected_tag"].pk == tag.pk
    assert invalid.status_code == 200
    assert invalid.context["selected_tag"] is None


@pytest.mark.django_db
def test_tag_workspace_kind_and_search_must_both_match(client):
    matching = Tag.objects.create(name="极限方法", kind="method")
    Tag.objects.create(name="极限题型", kind="problem_type")
    Tag.objects.create(name="积分方法", kind="method")

    response = client.get(reverse("tag-manage"), {"kind": "method", "q": "极限"})

    assert [tag.pk for tag in response.context["tags"]] == [matching.pk]
    assert response.context["tag_categories"][0]["count"] == 2


@pytest.mark.django_db
def test_tag_workspace_edit_url_selects_tag_and_keeps_form(client):
    tag = Tag.objects.create(name="编辑标签", kind="method")

    response = client.get(reverse("tag-edit", kwargs={"pk": tag.pk}))

    assert response.context["selected_tag"].pk == tag.pk
    assert response.context["form"].instance.pk == tag.pk
    assert 'name="name"' in response.content.decode()


@pytest.mark.django_db
def test_tag_workspace_selected_get_submits_existing_tag_edit(client):
    tag = Tag.objects.create(name="原标签", kind="method")
    response = client.get(
        reverse("tag-manage"),
        {"selected": str(tag.pk), "kind": "method", "q": "原"},
    )

    body = response.content.decode()
    assert response.context["form"].instance.pk == tag.pk
    assert response.context["form_target_tag"].pk == tag.pk
    assert (
        f'action="{reverse("tag-edit", kwargs={"pk": tag.pk})}'
        '?kind=method&amp;q=%E5%8E%9F"'
    ) in body


@pytest.mark.django_db
def test_tag_workspace_selected_get_does_not_turn_create_post_into_edit(client):
    selected = Tag.objects.create(name="原标签")

    response = client.post(
        reverse("tag-manage") + f"?selected={selected.pk}",
        {"name": "新标签", "kind": "custom", "parent": ""},
    )

    selected.refresh_from_db()
    assert response.status_code == 302
    assert selected.name == "原标签"
    assert Tag.objects.filter(name="新标签").exists()


@pytest.mark.django_db
def test_tag_workspace_invalid_create_with_selected_keeps_create_action_and_input(client):
    selected = Tag.objects.create(name="原标签")
    url = reverse("tag-manage") + f"?kind=method&q=极限&selected={selected.pk}"

    response = client.post(url, {"name": "", "kind": "custom", "parent": ""})
    body = response.content.decode()

    assert response.status_code == 200
    assert response.context["form"].is_bound
    assert response.context["form"].errors
    assert response.context["selected_kind"] == "method"
    assert response.context["tag_query"] == "极限"
    assert response.context["selected_tag"].pk == selected.pk
    assert f'action="{reverse("tag-manage")}?kind=method&amp;q=%E6%9E%81%E9%99%90"' in body
    assert f'action="{reverse("tag-edit", kwargs={"pk": selected.pk})}' not in body


def test_tag_workspace_css_has_responsive_three_panel_layout():
    css = (Path(__file__).parents[2] / "static/question_bank/css/tag-workbench.css").read_text(
        encoding="utf-8"
    ).replace(" ", "")
    assert ".tag-workbench" in css
    assert ".tag-workbench__categories" in css
    assert ".tag-workbench__list" in css
    assert ".tag-workbench__details" in css
    assert "@media(max-width:760px)" in css
    assert "grid-template-columns:1fr" in css


def test_tag_workspace_css_switches_to_two_columns_before_midwidth_overflow():
    css = (Path(__file__).parents[2] / "static/question_bank/css/tag-workbench.css").read_text(
        encoding="utf-8"
    ).replace(" ", "")
    assert "@media(max-width:1280px)" in css
    assert ".tag-workbench__layout{grid-template-columns:minmax(160px,200px)minmax(0,1fr)}" in css


@pytest.mark.django_db
@pytest.mark.parametrize("operation", ["archive", "merge"])
def test_tag_confirmation_get_shows_source_and_preserves_cancel_state(client, operation):
    source = Tag.objects.create(name="待处理标签", kind="method")
    url = reverse(f"tag-{operation}-confirm", kwargs={"pk": source.pk})
    response = client.get(url, {"kind": "method", "q": "待处理", "show_archived": "1", "selected": str(source.pk)})

    assert response.status_code == 200
    body = response.content.decode()
    assert "待处理标签" in body
    assert 'class="tag-workbench__details tag-workbench__confirm"' in body
    assert 'class="tag-workbench__form"' in body
    assert f'action="{reverse(f"tag-{operation}", kwargs={"pk": source.pk})}?' in body
    assert f'href="{reverse("tag-manage")}?kind=method&amp;q=%E5%BE%85%E5%A4%84%E7%90%86&amp;show_archived=1&amp;selected={source.pk}"' in body
    assert source.archived is False


@pytest.mark.django_db
def test_tag_confirmation_merge_requires_explicit_eligible_target(client):
    source = Tag.objects.create(name="来源")
    target = Tag.objects.create(name="目标")
    archived = Tag.objects.create(name="归档目标", archived=True)
    redirected = Tag.objects.create(name="重定向目标", redirect_to=target)
    url = reverse("tag-merge-confirm", kwargs={"pk": source.pk})

    response = client.get(url)
    choices = list(response.context["merge_targets"])
    body = response.content.decode()
    assert target in choices
    assert source not in choices and archived not in choices and redirected not in choices
    assert '<option value="" selected>' in body
    assert f'value="{target.pk}"' in body

    empty = client.post(reverse("tag-merge", kwargs={"pk": source.pk}), {"target": ""})
    assert empty.status_code == 200
    assert "请选择合并目标" in empty.content.decode()
    source.refresh_from_db()
    assert source.archived is False and source.redirect_to_id is None


@pytest.mark.django_db
def test_tag_confirmation_merge_error_preserves_invalid_target_and_state(client):
    source = Tag.objects.create(name="来源")
    target = Tag.objects.create(name="归档目标", archived=True)
    url = reverse("tag-merge", kwargs={"pk": source.pk})

    response = client.post(url + "?kind=method&q=来源&show_archived=1", {"target": str(target.pk)})

    assert response.status_code == 200
    assert response.context["selected_target"] == str(target.pk)
    assert f'<option value="{target.pk}" selected disabled>归档目标' in response.content.decode()
    assert "已归档" in response.content.decode()
    assert response.context["selected_kind"] == "method"
    assert response.context["tag_query"] == "来源"
    source.refresh_from_db()
    assert source.redirect_to_id is None


@pytest.mark.django_db
@pytest.mark.parametrize("target_value", ["malformed", "", "self", "redirected"])
def test_tag_confirmation_direct_merge_post_rejects_unusable_target(client, target_value):
    source = Tag.objects.create(name="来源")
    final = Tag.objects.create(name="最终目标")
    redirected = Tag.objects.create(name="旧目标", redirect_to=final)
    values = {"self": str(source.pk), "redirected": str(redirected.pk)}
    response = client.post(
        reverse("tag-merge", kwargs={"pk": source.pk}),
        {"target": values.get(target_value, target_value)},
    )

    assert response.status_code == 200
    assert response.context["error"]
    source.refresh_from_db()
    assert source.archived is False and source.redirect_to_id is None


@pytest.mark.django_db
@pytest.mark.parametrize("submitted", ["self", "<script>alert(1)</script>"])
def test_tag_confirmation_invalid_target_remains_visible_and_allows_reselection(client, submitted):
    source = Tag.objects.create(name="来源")
    eligible = Tag.objects.create(name="可用目标")
    target_value = str(source.pk) if submitted == "self" else submitted

    response = client.post(reverse("tag-merge", kwargs={"pk": source.pk}), {"target": target_value})
    body = response.content.decode()

    assert response.status_code == 200
    assert response.context["selected_target"] == target_value
    assert 'selected disabled' in body
    assert "来源（已归档或不可用）" in body if submitted == "self" else "&lt;script&gt;alert(1)&lt;/script&gt;" in body
    assert "<script>alert(1)</script>" not in body
    assert f'<option value="{eligible.pk}"' in body


@pytest.mark.django_db
def test_tag_confirmation_post_uses_only_normalized_filter_params(client):
    source = Tag.objects.create(name="来源")
    response = client.post(
        reverse("tag-archive", kwargs={"pk": source.pk}) + "?kind=unknown&q=  来源  &next=https://invalid.example/",
        {},
    )

    assert response.status_code == 302
    assert response["Location"] == reverse("tag-manage") + "?q=%E6%9D%A5%E6%BA%90"


@pytest.mark.django_db
def test_tag_confirmation_post_actions_return_filtered_workspace_and_clear_selection(client):
    source = Tag.objects.create(name="来源", kind="method")
    target = Tag.objects.create(name="目标", kind="method")
    query = f"?kind=method&q=%E6%9D%A5%E6%BA%90&show_archived=1&selected={source.pk}"

    merged = client.post(reverse("tag-merge", kwargs={"pk": source.pk}) + query, {"target": str(target.pk)})
    source.refresh_from_db()
    assert merged.status_code == 302
    assert merged["Location"] == reverse("tag-manage") + "?kind=method&q=%E6%9D%A5%E6%BA%90&show_archived=1"
    assert source.archived is True and source.redirect_to_id == target.pk
    assert "标签已合并" in client.get(merged["Location"]).content.decode()

    another = Tag.objects.create(name="另一个", kind="method")
    archived = client.post(reverse("tag-archive", kwargs={"pk": another.pk}) + query, {})
    another.refresh_from_db()
    assert archived.status_code == 302
    assert "selected=" not in archived["Location"]
    assert another.archived is True


@pytest.mark.django_db
def test_tag_workspace_write_keeps_invalid_edit_input_and_all_filters(client):
    tag = Tag.objects.create(name="原名", kind="method")
    url = reverse("tag-edit", kwargs={"pk": tag.pk}) + f"?kind=method&q=原&show_archived=1&selected={tag.pk}"

    response = client.post(url, {"name": "", "kind": "method", "parent": ""})

    assert response.status_code == 200
    assert response.context["form"].is_bound
    assert response.context["form"].errors
    assert response.context["selected_tag"].pk == tag.pk
    assert response.context["selected_kind"] == "method"
    assert response.context["tag_query"] == "原"
    assert response.context["show_archived"] is True
    assert f'action="{reverse("tag-edit", kwargs={"pk": tag.pk})}?kind=method&amp;q=%E5%8E%9F&amp;show_archived=1"' in response.content.decode()


@pytest.mark.django_db
def test_tag_workspace_write_success_preserves_filters_and_selects_saved_tag(client):
    url = reverse("tag-manage") + "?kind=method&q=新&show_archived=1"
    response = client.post(url, {"name": "新方法", "kind": "method", "parent": ""})

    saved = Tag.objects.get(name="新方法")
    assert response.status_code == 302
    assert response["Location"] == reverse("tag-manage") + f"?kind=method&q=%E6%96%B0&show_archived=1&selected={saved.pk}"


@pytest.mark.django_db
def test_tag_workspace_write_edit_success_preserves_filters_and_selects_edited_tag(client):
    tag = Tag.objects.create(name="原方法", kind="method")
    url = reverse("tag-edit", kwargs={"pk": tag.pk}) + "?kind=method&q=新&show_archived=1"
    response = client.post(url, {"name": "新方法", "kind": "method", "parent": ""})

    tag.refresh_from_db()
    assert tag.name == "新方法"
    assert response["Location"] == reverse("tag-manage") + f"?kind=method&q=%E6%96%B0&show_archived=1&selected={tag.pk}"


@pytest.mark.django_db
def test_tag_workspace_more_actions_link_to_confirmation_pages(client):
    tag = Tag.objects.create(name="可操作")
    response = client.get(reverse("tag-manage"), {"selected": str(tag.pk), "q": "可"})
    body = response.content.decode()

    assert reverse("tag-archive-confirm", kwargs={"pk": tag.pk}) in body
    assert reverse("tag-merge-confirm", kwargs={"pk": tag.pk}) in body


@pytest.mark.django_db
@pytest.mark.parametrize("field", ["title", "statement", "personal_solution", "reference_solution", "error_note"])
def test_keyword_search_covers_all_text_fields(client, subject, field):
    question = make_question(subject, "其他")
    setattr(question, field, "needle")
    question.save()
    response = client.get(reverse("question-list"), {"q": "NEEDLE"})
    assert response.status_code == 200
    assert str(question.pk) in response.content.decode()


@pytest.mark.django_db
def test_filters_use_and_between_fields_and_or_within_field(client, subject, section):
    other = Subject.objects.create(name="高等代数")
    q1 = make_question(subject, "q1", section=section, mastery=Question.MASTERY_MASTERED)
    q2 = make_question(subject, "q2", section=section, mastery=Question.MASTERY_STRUGGLING)
    q3 = make_question(other, "q3", mastery=Question.MASTERY_MASTERED)
    response = client.get(
        reverse("question-list"),
        [("subject", str(subject.pk)), ("mastery", Question.MASTERY_MASTERED), ("mastery", Question.MASTERY_STRUGGLING)],
    )
    body = response.content.decode()
    assert str(q1.pk) in body and str(q2.pk) in body
    assert str(q3.pk) not in body


@pytest.mark.django_db
def test_search_paginates_twenty_and_orders_by_updated_then_uuid(subject):
    questions = [make_question(subject, f"q{i}") for i in range(21)]
    page = build_question_queryset({})
    assert page.count() == 21
    from django.core.paginator import Paginator

    assert Paginator(page, 20).num_pages == 2
    assert list(page.values_list("updated_at", flat=True)) == sorted(
        page.values_list("updated_at", flat=True), reverse=True
    )


@pytest.mark.django_db
def test_due_filter_and_knowledge_card_filter(subject):
    card = KnowledgeCard.objects.create(name="极限定义", subject=subject, type="definition")
    due = make_question(subject, "due", next_review_at=timezone.now() - timedelta(minutes=1))
    future = make_question(subject, "future", next_review_at=timezone.now() + timedelta(days=1))
    due.knowledge_cards.add(card)
    result = build_question_queryset({"due": "1", "knowledge_card": str(card.pk)})
    assert list(result) == [due]
    assert future not in result


@pytest.mark.django_db
def test_merge_migrates_relations_and_compresses_redirects(subject):
    source = Tag.objects.create(name="旧")
    middle = Tag.objects.create(name="中")
    target = Tag.objects.create(name="新")
    question = make_question(subject, "带标签")
    question.tags.add(source)
    source.redirect_to = middle
    source.save()
    merge_tags(middle, target)
    source.refresh_from_db()
    middle.refresh_from_db()
    assert source.redirect_to_id == target.pk
    assert middle.redirect_to_id == target.pk
    assert set(question.tags.all()) == {source, target}


@pytest.mark.django_db
def test_archived_tag_is_hidden_for_new_questions_but_kept_in_history(subject):
    archived = Tag.objects.create(name="历史", archived=True)
    question = make_question(subject, "历史题")
    question.tags.add(archived)
    assert archived not in QuestionForm().fields["tags"].queryset
    assert archived in question.tags.all()


@pytest.mark.django_db
def test_root_route_is_question_search_home(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "题目检索" in response.content.decode()


@pytest.mark.django_db
def test_tag_rejects_direct_self_parent():
    tag = Tag.objects.create(name="方法")
    tag.parent = tag
    with pytest.raises(ValidationError, match="标签不能将自身设为父级"):
        tag.save()


@pytest.mark.django_db
def test_tag_rejects_parent_that_is_a_descendant():
    root = Tag.objects.create(name="方法")
    child = Tag.objects.create(name="夹逼", parent=root)
    root.parent = child
    with pytest.raises(ValidationError, match="父级不能形成环"):
        root.save()


@pytest.mark.django_db
def test_tag_parent_cycle_traversal_stops_on_legacy_cycle():
    root = Tag(id="00000000-0000-0000-0000-000000000001", name="根")
    child = Tag(id="00000000-0000-0000-0000-000000000002", name="子")
    Tag.objects.bulk_create([root, child])
    Tag.objects.filter(pk=root.pk).update(parent_id=child.pk)
    Tag.objects.filter(pk=child.pk).update(parent_id=root.pk)

    candidate = Tag.objects.create(name="候选")
    candidate.parent_id = root.pk
    candidate.full_clean()


@pytest.mark.django_db
def test_tag_form_excludes_edited_tag_and_all_descendants_from_parent_choices():
    root = Tag.objects.create(name="方法")
    child = Tag.objects.create(name="夹逼", parent=root)
    grandchild = Tag.objects.create(name="数列", parent=child)
    sibling = Tag.objects.create(name="积分")

    form = TagForm(instance=root, data={"name": root.name, "parent": "", "kind": root.kind})

    choices = set(form.fields["parent"].queryset.values_list("pk", flat=True))
    assert root.pk not in choices
    assert child.pk not in choices
    assert grandchild.pk not in choices
    assert sibling.pk in choices


@pytest.mark.django_db
def test_tag_picker_prefix_index_is_declared():
    assert any(index.name == "tag_picker_prefix_idx" for index in Tag._meta.indexes)


def _tag_cache_spy(monkeypatch):
    cache.clear()
    calls = []
    monkeypatch.setattr(
        search,
        "invalidate_tag_picker_cache",
        lambda: calls.append("invalidated"),
        raising=False,
    )
    return calls


@pytest.mark.django_db
def test_tag_create_schedules_cache_invalidation_after_commit(monkeypatch):
    calls = _tag_cache_spy(monkeypatch)
    with TestCase.captureOnCommitCallbacks(execute=True):
        with transaction.atomic():
            Tag.objects.create(name="新标签")
    assert calls == ["invalidated"]


@pytest.mark.django_db
@pytest.mark.parametrize(
    "mutation",
    [
        "rename",
        "reparent",
        "archive",
        "restore",
    ],
)
def test_tag_mutations_schedule_cache_invalidation_after_commit(monkeypatch, mutation):
    parent = Tag.objects.create(name="父级")
    tag = Tag.objects.create(name="标签", parent=parent, archived=mutation == "restore")
    other_parent = Tag.objects.create(name="另一个父级")
    calls = _tag_cache_spy(monkeypatch)

    with TestCase.captureOnCommitCallbacks(execute=True):
        with transaction.atomic():
            if mutation == "rename":
                tag.name = "新名称"
            elif mutation == "reparent":
                tag.parent = other_parent
            elif mutation == "archive":
                tag.archived = True
            else:
                tag.archived = False
            tag.save()
    assert calls == ["invalidated"]


@pytest.mark.django_db
def test_tag_delete_schedules_cache_invalidation_after_commit(monkeypatch):
    tag = Tag.objects.create(name="待删除")
    calls = _tag_cache_spy(monkeypatch)
    with TestCase.captureOnCommitCallbacks(execute=True):
        with transaction.atomic():
            tag.delete()
    assert calls == ["invalidated"]


@pytest.mark.django_db
def test_tag_merge_schedules_cache_invalidation_after_commit(monkeypatch, subject):
    source = Tag.objects.create(name="旧标签")
    target = Tag.objects.create(name="新标签")
    question = make_question(subject, "合并题")
    question.tags.add(source)
    calls = _tag_cache_spy(monkeypatch)

    with TestCase.captureOnCommitCallbacks(execute=True):
        with transaction.atomic():
            merge_tags(source, target)
    assert calls


@pytest.mark.django_db
def test_import_style_direct_tag_save_schedules_cache_invalidation_after_commit(monkeypatch):
    tag = Tag.objects.create(name="导入标签")
    calls = _tag_cache_spy(monkeypatch)
    with TestCase.captureOnCommitCallbacks(execute=True):
        with transaction.atomic():
            imported = Tag.objects.get(pk=tag.pk)
            imported.name = "导入后标签"
            imported.save()
    assert calls == ["invalidated"]


@pytest.mark.django_db
def test_tag_cache_invalidation_callback_does_not_run_after_rollback(monkeypatch):
    calls = _tag_cache_spy(monkeypatch)
    with TestCase.captureOnCommitCallbacks(execute=True):
        with pytest.raises(RuntimeError):
            with transaction.atomic():
                Tag.objects.create(name="回滚标签")
                raise RuntimeError("rollback")
    assert calls == []


@pytest.mark.django_db
def test_import_query_set_update_schedules_tag_cache_invalidation(tmp_path, monkeypatch):
    import json
    import uuid
    import zipfile

    target_id = str(uuid.uuid4())
    source_id = str(uuid.uuid4())
    entities = {
        "subjects": [],
        "sections": [],
        "questions": [],
        "attachments": [],
        "knowledge_cards": [],
        "tags": [
            {"id": target_id, "created_at": None, "updated_at": None, "name": "目标", "parent_id": None, "kind": "custom", "archived": False, "redirect_to_id": None},
            {"id": source_id, "created_at": None, "updated_at": None, "name": "来源", "parent_id": None, "kind": "custom", "archived": False, "redirect_to_id": f"tag:{target_id}"},
        ],
        "review_records": [],
        "knowledge_card_prerequisites": [],
    }
    bundle = tmp_path / "tags.zip"
    with zipfile.ZipFile(bundle, "w") as archive:
        archive.writestr("manifest.json", json.dumps({"version": 1, "entities": entities}))

    cache.clear()
    calls = []
    monkeypatch.setattr(search, "invalidate_tag_picker_cache", lambda: None, raising=False)
    monkeypatch.setattr(
        "question_bank.exporting.schedule_tag_picker_cache_invalidation",
        lambda: transaction.on_commit(lambda: calls.append("invalidated")),
    )
    with TestCase.captureOnCommitCallbacks(execute=True):
        with transaction.atomic():
            import_bundle(bundle)
    assert calls == ["invalidated"]


@pytest.mark.django_db
def test_canonical_tag_filter_keeps_historical_source_relations(subject):
    source = Tag.objects.create(name="旧极限", archived=True)
    target = Tag.objects.create(name="极限")
    source.redirect_to = target
    source.save()
    question = make_question(subject, "历史关系")
    question.tags.add(source)

    assert canonical_tag_ids([str(source.pk)]) == (str(target.pk),)
    assert tag_relation_ids([str(target.pk)]) == {str(source.pk), str(target.pk)}
    assert list(build_question_queryset({"tag": str(source.pk)})) == [question]
    assert list(build_question_queryset({"tag": str(target.pk)})) == [question]


@pytest.mark.django_db
def test_invalid_and_unresolvable_tag_filters_do_not_filter_questions(subject):
    archived = Tag.objects.create(name="已归档", archived=True)
    question = make_question(subject, "无效标签过滤")

    assert canonical_tag_ids(["missing", str(archived.pk)]) == ()
    assert list(build_question_queryset({"tag": "missing", "tags": str(archived.pk)})) == [question]


@pytest.mark.django_db
def test_tag_picker_builds_bounded_common_category_and_selected_items(subject):
    root = Tag.objects.create(name="方法")
    child = Tag.objects.create(name="夹逼", parent=root)
    leaf = Tag.objects.create(name="独立")
    selected = Tag.objects.create(name="长尾")
    question = make_question(subject, "标签题")
    question.tags.add(child, leaf)

    cache.clear()
    picker = build_tag_picker([str(subject.pk)], [str(selected.pk)])
    assert {item["id"] for item in picker["selected"]} == {str(selected.pk)}
    assert any(item["id"] == str(child.pk) for item in picker["common"])
    method_category = next(category for category in picker["categories"] if category["name"] == "方法")
    assert method_category["all"]["id"] == str(root.pk)
    assert all(item["id"] != str(root.pk) for item in method_category["items"])
    assert all(len(category["items"]) <= 6 for category in picker["categories"])


@pytest.mark.django_db
def test_tag_suggestions_are_bounded_and_exclude_selected(subject):
    first = Tag.objects.create(name="极限")
    Tag.objects.create(name="极坐标")
    Tag.objects.create(name="积分")
    suggestions = search_tag_suggestions("极", [str(subject.pk)], [str(first.pk)])

    assert len(suggestions) <= 20
    assert all(item["id"] != str(first.pk) for item in suggestions)


@pytest.mark.django_db
def test_picker_groups_nested_descendants_after_archived_parent(subject):
    archived_parent = Tag.objects.create(name="已归档父级", archived=True)
    root = Tag.objects.create(name="活动根", parent=archived_parent)
    child = Tag.objects.create(name="嵌套子级", parent=root)
    question = make_question(subject, "嵌套题")
    question.tags.add(child)

    cache.clear()
    picker = build_tag_picker([str(subject.pk)], [])
    category = next(item for item in picker["categories"] if item["id"] == str(root.pk))
    assert category["all"]["count"] == 1
    assert [item["id"] for item in category["items"]] == [str(child.pk)]
    assert category["items"][0]["depth"] == 1


@pytest.mark.django_db
def test_picker_places_legacy_parent_cycle_in_other(subject, caplog):
    root = Tag(id="00000000-0000-0000-0000-000000000011", name="循环根")
    child = Tag(id="00000000-0000-0000-0000-000000000012", name="循环子")
    Tag.objects.bulk_create([root, child])
    Tag.objects.filter(pk=root.pk).update(parent_id=child.pk)
    Tag.objects.filter(pk=child.pk).update(parent_id=root.pk)
    question = make_question(subject, "循环题")
    question.tags.add(root)

    cache.clear()
    with caplog.at_level("WARNING"):
        picker = build_tag_picker([str(subject.pk)], [])
    other = next(item for item in picker["categories"] if item["id"] == "other")
    assert [item["id"] for item in other["items"]] == [str(root.pk)]
    assert "Legacy tag parent cycle detected" in caplog.text


@pytest.mark.django_db
def test_picker_categories_sort_by_distinct_question_count(subject):
    roots = [Tag.objects.create(name=name) for name in ("分类甲", "分类乙", "分类丙")]
    children = [Tag.objects.create(name=f"{root.name}子", parent=root) for root in roots]
    questions = [make_question(subject, f"分类题{i}") for i in range(3)]
    questions[0].tags.add(children[0])
    questions[1].tags.add(children[1])
    questions[2].tags.add(children[2])
    questions[0].tags.add(children[2])
    questions[1].tags.add(children[2])

    cache.clear()
    picker = build_tag_picker([str(subject.pk)], [])
    assert [item["count"] for item in picker["categories"][:3]] == [3, 1, 1]
    tied = picker["categories"][1:3]
    assert [(item["name"].casefold(), str(item["id"])) for item in tied] == sorted(
        (item["name"].casefold(), str(item["id"])) for item in tied
    )


@pytest.mark.django_db
def test_tag_filter_handles_more_than_999_relation_ids(subject):
    tags = [Tag(name=f"批量标签{i}") for i in range(1105)]
    Tag.objects.bulk_create(tags)
    question = make_question(subject, "大批量关系")
    question.tags.add(*tags)

    cache.clear()
    sqlite_connection = connection.connection
    if not hasattr(sqlite_connection, "setlimit"):
        pytest.skip("SQLite variable limit API unavailable")
    previous_limit = sqlite_connection.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 999)
    try:
        result = list(build_question_queryset({"tag": [str(tag.pk) for tag in tags]}))
    finally:
        sqlite_connection.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, previous_limit)
    assert result == [question]


@pytest.mark.django_db
def test_picker_bounds_5000_tags_and_stays_within_query_budget(subject, django_assert_num_queries):
    popular = [Tag(name=f"常用标签{i}") for i in range(25)]
    remainder = [Tag(name=f"长尾标签{i}") for i in range(4975)]
    Tag.objects.bulk_create(popular + remainder)
    question = make_question(subject, "标签规模")
    question.tags.add(*popular)

    cache.clear()
    with django_assert_num_queries(2, exact=False):
        picker = build_tag_picker([str(subject.pk)], [])
    assert len(Tag.objects.all()) == 5000
    assert len(picker["common"]) <= 8
    assert len(picker["categories"]) <= 12
    assert all(len(category["items"]) <= 6 for category in picker["categories"])
    cache.clear()
    with django_assert_num_queries(3, exact=False):
        suggestions = search_tag_suggestions("常用", [str(subject.pk)])
    assert len(suggestions) <= 20


@pytest.mark.django_db(transaction=True)
def test_picker_does_not_cache_uncommitted_hierarchy(subject):
    baseline = Tag.objects.create(name="已提交标签")
    cache.clear()
    build_tag_picker([str(subject.pk)], [])

    with pytest.raises(RuntimeError):
        with transaction.atomic():
            ghost = Tag.objects.create(name="回滚标签")
            picker = build_tag_picker([str(subject.pk)], [str(ghost.pk)])
            assert picker["selected"]
            raise RuntimeError("rollback")

    assert canonical_tag_ids([str(baseline.pk)]) == (str(baseline.pk),)
    assert canonical_tag_ids([str(ghost.pk)]) == ()


@pytest.mark.django_db
def test_question_list_exposes_canonical_picker_context_and_real_data(client, subject):
    tag = Tag.objects.create(name="极限")
    question = make_question(subject, "带标签题")
    question.tags.add(tag)

    response = client.get(reverse("question-list"), {"subject": [str(subject.pk), str(subject.pk)]})

    assert response.status_code == 200
    assert response.context["tag_picker"]["common"]
    assert response.context["tag_picker"]["common"][0]["canonical_id"] == str(tag.pk)
    assert response.context["selected_tags"] == []
    assert str(question.pk) in response.content.decode()


@pytest.mark.django_db
def test_question_list_canonicalizes_repeated_redirect_and_invalid_tags(client, subject):
    source = Tag.objects.create(name="旧极限", archived=True)
    target = Tag.objects.create(name="极限")
    source.redirect_to = target
    source.save()
    question = make_question(subject, "历史关系")
    question.tags.add(source)

    response = client.get(
        reverse("question-list"),
        [("tag", str(source.pk)), ("tag", str(target.pk)), ("tag", "missing"), ("tags", "missing")],
    )

    assert response.status_code == 200
    assert response.context["selected_tags"] == [str(target.pk)]
    assert [item["value"] for item in response.context["active_filters"] if item["key"] == "tag"] == [str(target.pk)]
    assert str(question.pk) in response.content.decode()


@pytest.mark.django_db
def test_question_list_all_invalid_tags_do_not_filter_results(client, subject):
    question = make_question(subject, "无效标签过滤")

    response = client.get(reverse("question-list"), [("tag", "missing"), ("tag", "also-missing")])

    assert response.status_code == 200
    assert response.context["selected_tags"] == []
    assert str(question.pk) in response.content.decode()


@pytest.mark.django_db
def test_question_list_picker_only_params_do_not_change_results_and_preserve_filters(client, subject):
    tag = Tag.objects.create(name="极限")
    matching = make_question(subject, "匹配")
    other_subject = Subject.objects.create(name="代数")
    other = make_question(other_subject, "其他")
    matching.tags.add(tag)

    baseline = client.get(reverse("question-list"), {"subject": str(subject.pk), "q": "匹配"})
    picker = client.get(
        reverse("question-list"),
        {
            "subject": str(subject.pk),
            "q": "匹配",
            "tag_picker_q": "极",
            "tag_picker_group": str(tag.pk),
            "tag_picker_page": "2",
        },
    )

    assert {item.pk for item in baseline.context["questions"]} == {matching.pk}
    assert {item.pk for item in picker.context["questions"]} == {matching.pk}
    assert other.pk not in {item.pk for item in picker.context["questions"]}
    assert picker.context["tag_picker"]["query"] == "极"


@pytest.mark.django_db
def test_question_list_tag_picker_accepts_repeated_subjects_and_selected_tags(client, subject):
    selected = Tag.objects.create(name="长尾")
    question = make_question(subject, "长尾题")
    question.tags.add(selected)

    response = client.get(
        reverse("question-list"),
        [
            ("subject", str(subject.pk)),
            ("subject", str(subject.pk)),
            ("tag", str(selected.pk)),
            ("tag", str(selected.pk)),
        ],
    )

    assert response.status_code == 200
    assert [item["canonical_id"] for item in response.context["tag_picker"]["selected"]] == [str(selected.pk)]
    assert response.context["tag_picker"]["selected"][0]["count"] == 1
    assert str(question.pk) in response.content.decode()


@pytest.mark.django_db
def test_question_list_native_tag_uncheck_keeps_remaining_selection(client, subject):
    kept = Tag.objects.create(name="保留")
    removed = Tag.objects.create(name="长尾")
    kept_question = make_question(subject, "保留题")
    removed_question = make_question(subject, "长尾题")
    kept_question.tags.add(kept)
    removed_question.tags.add(removed)

    response = client.get(reverse("question-list"), [("tag", str(kept.pk))])

    assert response.status_code == 200
    assert response.context["selected_tags"] == [str(kept.pk)]
    listed_ids = {item.pk for item in response.context["questions"]}
    assert kept_question.pk in listed_ids
    assert removed_question.pk not in listed_ids
    body = response.content.decode()
    assert f'name="tag" value="{kept.pk}" checked' in body
    assert f'name="tag" value="{removed.pk}" checked' not in body


@pytest.mark.django_db
def test_tag_suggestions_endpoint_is_subject_scoped_bounded_json_and_excludes_selected(client, subject):
    selected = Tag.objects.create(name="极限")
    scoped = Tag.objects.create(name="极坐标")
    other_subject = Subject.objects.create(name="代数")
    unscoped = Tag.objects.create(name="极大值")
    make_question(subject, "作用域").tags.add(selected, scoped)
    make_question(other_subject, "其他").tags.add(unscoped)

    response = client.get(
        reverse("tag-suggestions"),
        [
            ("q", " 极 "),
            ("subject", str(subject.pk)),
            ("subject", str(subject.pk)),
            ("selected_tag", str(selected.pk)),
            ("selected_tag", str(selected.pk)),
        ],
    )

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {"items"}
    assert [item["name"] for item in payload["items"]] == ["极坐标"]
    assert set(("canonical_id", "name", "category_name", "depth", "count")) <= set(payload["items"][0])
    assert all("archived" not in item and "redirect_to" not in item for item in payload["items"])


@pytest.mark.django_db
@pytest.mark.parametrize("query", ["", "   ", "\t\n"])
def test_tag_suggestions_endpoint_returns_empty_for_blank_query(client, query):
    response = client.get(reverse("tag-suggestions"), {"q": query})

    assert response.status_code == 200
    assert response.json() == {"items": []}


@pytest.mark.django_db
def test_tag_suggestions_endpoint_rejects_query_longer_than_100(client):
    response = client.get(reverse("tag-suggestions"), {"q": "x" * 101})

    assert response.status_code == 400

