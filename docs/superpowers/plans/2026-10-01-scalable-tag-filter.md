# Scalable Tag Filter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the all-tags picker on the question retrieval page with a bounded, subject-aware tag picker that supports common tags, hierarchy browsing, server-backed prefix search, canonical merged-tag behavior, and no-JavaScript fallback.

**Architecture:** Keep canonical tag resolution, hierarchy traversal, relation-ID expansion, and aggregate counts in `question_bank.search`. Add a small tag-picker context builder that the list view and JSON suggestion endpoint share. The list template renders a bounded native fallback, while `app.js` only enhances search, category paging, and selected-tag removal. A composite model index supports canonical prefix search; tag parent-cycle validation protects cached hierarchy construction.

**Tech Stack:** Django 5.2, SQLite locally, Django ORM aggregation, vanilla ES modules/IIFE JavaScript, pytest and pytest-django, Playwright Chromium when installed.

---

## Repository Preconditions

Work in `C:\Users\14633\study-vault\.worktrees\math-question-bank` on `feature/math-question-bank`. The worktree already includes unrelated user-owned retrieval-page and question-workbench edits. Preserve and extend them without reverting their changes.

This worktree is intentionally dirty and the relevant files contain mixed ownership. Do not run whole-file `git add` or create task commits. Before each edit, inspect the local diff and use `apply_patch` to add only the tag-picker hunk. At the end, report the complete diff and leave integration to a later clean-branch step. This avoids staging or committing unrelated user changes.

Use this Git prefix because the worktree requires an explicit safe-directory setting:

```powershell
git -c safe.directory=C:/Users/14633/study-vault/.worktrees/math-question-bank
```

Use a worktree-local pytest directory to avoid Windows Temp permissions:

```powershell
& 'C:\Users\14633\anaconda3\Scripts\pytest.exe' -q --basetemp .runtime\pytest-tag-picker
```

## File Map

* Modify `question_bank/models.py`: reject indirect `Tag.parent` cycles, add the canonical prefix-search composite index, and invalidate the picker cache on every tag save/delete.
* Create `question_bank/migrations/0002_tag_picker_search_index.py`: add the new index without changing existing data.
* Modify `question_bank/forms.py`: exclude an edited tag's descendants from selectable parent choices.
* Modify `question_bank/search.py`: canonical resolution, bounded hierarchy cache, relation-ID expansion, picker counts, paging, and prefix suggestions. Keep category paging server-rendered through the existing list route, with no second category JSON contract. Use the through table in bounded batches when expanding relation IDs so SQLite never receives an oversized `IN` parameter list.
* Modify `question_bank/views.py`: construct picker context, canonical active filters, and serve the JSON suggestions endpoint.
* Modify `question_bank/urls.py`: add the same-origin tag-suggestion route.
* Modify `question_bank/templates/question_bank/question_list.html`: render selected tags, common tags, bounded category lists, native picker search, and progressive-enhancement hooks.
* Modify `static/question_bank/css/app.css`: add compact picker, count, hierarchy, paging, and responsive styles.
* Modify `static/question_bank/js/app.js`: debounce suggestion requests, render safe DOM results, manage selected-tag chip removal, and preserve native category links and controls.
* Modify `question_bank/tests/test_search_and_tags.py`: service, redirect, hierarchy, count, cycle, and query-behavior regression tests.
* Modify `question_bank/exporting.py`: explicitly invalidate the picker cache after import paths that use `QuerySet.update()` to bypass model signals.
* Modify `tests/test_templates.py`: template, CSS, and script contract tests.
* Create `tests/test_question_list_browser.py`: retrieval-page browser tests with the existing `browser` marker. Keep the question-form browser module focused on the workbench.

## Task 1: Protect Tag Hierarchy and Add Search Index

**Files:**

* Modify: `question_bank/models.py`
* Modify: `question_bank/forms.py`
* Create: `question_bank/migrations/0002_tag_picker_search_index.py`
* Modify: `question_bank/exporting.py`
* Modify: `question_bank/tests/test_search_and_tags.py`

- [ ] **Step 1: Write failing direct and indirect parent-cycle tests**

```python
def test_tag_rejects_parent_that_is_a_descendant():
    root = Tag.objects.create(name="方法")
    child = Tag.objects.create(name="夹逼", parent=root)
    root.parent = child
    with pytest.raises(ValidationError, match="父级不能形成环"):
        root.save()
```

Add a `TagForm(instance=root, data=...)` test proving a tag's own descendants are absent from its parent queryset. Add cache invalidation tests for create, rename, reparent, merge, archive, restore, delete, import-style direct `save()` paths, and the import `QuerySet.update()` path in `question_bank/exporting.py`. Use `captureOnCommitCallbacks(execute=True)` for committed mutations, `cache.clear()` between cases, and one rollback case proving the callback does not publish invalidation state before commit.

- [ ] **Step 2: Run the focused tests and verify RED**

```powershell
& 'C:\Users\14633\anaconda3\Scripts\pytest.exe' question_bank/tests/test_search_and_tags.py -q -k "tag_rejects_parent_that_is_a_descendant or parent_queryset or cache_invalidation" --basetemp .runtime\pytest-tag-model-red
```

Expected: FAIL because `Tag.clean()` rejects only self-parenting and `TagForm` removes only the edited row.

- [ ] **Step 3: Implement guarded ancestry traversal**

Add a focused helper that walks prospective parent IDs with a visited set and treats reaching the instance ID as a validation error. Make it tolerate legacy cycles by stopping on a repeated ID. Use the helper in `Tag.clean()`. In `TagForm.__init__`, calculate descendants of the edited tag and exclude them, along with the edited tag, from the `parent` queryset.

- [ ] **Step 4: Implement post-commit cache invalidation hooks**

Add `post_save` and `post_delete` receivers for `Tag` that lazily import a scheduling helper. The helper must call `transaction.on_commit(invalidate_tag_picker_cache)` so a rolled-back transaction cannot publish a stale or partially written hierarchy. Cover tag creation, direct edits, tag-management reparenting, archive/restore, merge redirects, deletion, and import code that calls `save()` without a circular import. Add an explicit invalidation call after every `QuerySet.update()` in `question_bank/exporting.py`, also scheduled with `transaction.on_commit`.

- [ ] **Step 5: Add the canonical picker index and migration**

Add `models.Index(fields=["archived", "redirect_to", "name"], name="tag_picker_prefix_idx")` to `Tag.Meta.indexes`. Run `makemigrations question_bank`, inspect the generated migration, and keep only the expected `AddIndex` operation.

- [ ] **Step 6: Add a migration/index regression test**

```python
def test_tag_picker_prefix_index_is_declared():
    assert any(index.name == "tag_picker_prefix_idx" for index in Tag._meta.indexes)
```

- [ ] **Step 7: Run focused model tests and migration checks**

```powershell
& 'C:\Users\14633\anaconda3\Scripts\pytest.exe' question_bank/tests/test_search_and_tags.py -q -k "tag_rejects_parent_that_is_a_descendant or test_tag_picker_prefix_index_is_declared or parent_queryset" --basetemp .runtime\pytest-tag-model-green
& 'C:\Users\14633\anaconda3\python.exe' manage.py makemigrations --check --dry-run
```

Expected: PASS and `No changes detected` after the migration file is present.

- [ ] **Step 8: Review only the scoped diff**

Use `git diff -- question_bank/models.py question_bank/forms.py question_bank/exporting.py question_bank/tests/test_search_and_tags.py`, then inspect the untracked migration explicitly with `Get-Content question_bank/migrations/0002_tag_picker_search_index.py`. Do not stage or commit in this dirty worktree.

## Task 2: Canonical Tag Resolution and Bounded Picker Service

**Files:**

* Modify: `question_bank/search.py`
* Modify: `question_bank/tests/test_search_and_tags.py`

- [ ] **Step 1: Write failing canonical-filter tests**

Cover each of these cases with real `Question.tags` relations:

```python
def test_tag_filter_resolves_redirect_source_and_keeps_historical_relation(subject):
    source = Tag.objects.create(name="旧极限", archived=True)
    target = Tag.objects.create(name="极限")
    source.redirect_to = target
    source.save()
    question = make_question(subject, "历史关系")
    question.tags.add(source)

    assert list(build_question_queryset({"tag": str(source.pk)})) == [question]
    assert list(build_question_queryset({"tag": str(target.pk)})) == [question]
```

Also test invalid IDs, archived tags without active redirect targets, non-archived redirect sources, selected parent tags with descendants, and all-invalid tag parameters producing an unfiltered tag dimension.

- [ ] **Step 2: Run canonical tests and verify RED**

```powershell
& 'C:\Users\14633\anaconda3\Scripts\pytest.exe' question_bank/tests/test_search_and_tags.py -q -k "canonical or redirect_source or archived_tag_parameter or invalid_tag" --basetemp .runtime\pytest-tag-canonical-red
```

Expected: FAIL because the current service treats archived and invalid requested IDs as direct fallback filter values and does not define canonical selected values.

- [ ] **Step 3: Write failing bounded-picker tests**

Add fixtures proving:

* no subject versus a selected subject changes common-tag ordering;
* count ties use name and ID ordering;
* categories are limited to 12 and sorted by total distinct-question count, then name and ID;
* zero-count and archived tags do not appear in common/category lists;
* selected zero-count canonical tags remain in selected items;
* a root with children has an `All in <category>` item and subtree count;
* root leaves and legacy-cycle nodes appear under `Other`;
* archived-parent chains and nested descendants have stable grouping and counts;
* historical source relations contribute to canonical and parent counts with `DISTINCT` semantics;
* active redirect sources, all-invalid IDs, and non-redirect canonical tags have defined results;
* category and search responses obey six, 12, and 20 bounds;
* the first 12 categories are ordered by total distinct-question count descending, then category name and stable ID;
* a 5,000-tag fixture does not expand the normal picker payload beyond its bounds;
* `django_assert_num_queries` stays within the fixed budget of at most eight queries for a cold-cache normal picker build, five queries for a warm-cache normal picker build, and five queries for a suggestion request; the test clears and warms the configured cache explicitly;
* relation-ID expansion uses the `Question.tags.through` table in batches below the SQLite parameter limit and never raises `too many SQL variables`;
* cache invalidation forces a rebuilt hierarchy after create, rename, reparent, merge, archive, restore, delete, and direct import-style save.

Represent picker context as frozen dataclasses or immutable dictionaries with only what the view needs: selected items, eight common items, at most 12 categories, six initial members per category, a selected category page of at most 20 items, and at most 20 search items.

- [ ] **Step 4: Run the picker tests and verify RED**

```powershell
& 'C:\Users\14633\anaconda3\Scripts\pytest.exe' question_bank/tests/test_search_and_tags.py -q -k "tag_picker or common_tags or category_count or legacy_cycle or query_budget or cache_invalidation" --basetemp .runtime\pytest-tag-picker-red
```

Expected: FAIL because the picker service and bounded context do not exist.

- [ ] **Step 5: Define the tag-picker service API**

In `question_bank.search`, add narrowly named functions with documented return types:

```python
def canonical_tag_ids(tag_ids: Iterable[str]) -> tuple[str, ...]: ...
def tag_relation_ids(canonical_ids: Iterable[str]) -> set[str]: ...
def build_tag_picker(subject_ids: Iterable[str], selected_tag_ids: Iterable[str], *, query="", group=None, page=1) -> TagPicker: ...
def search_tag_suggestions(prefix: str, subject_ids: Iterable[str], selected_tag_ids: Iterable[str] = ()) -> list[TagPickerItem]: ...
```

- [ ] **Step 6: Implement the cached hierarchy snapshot**

Build an immutable hierarchy snapshot containing active canonical tags, descendants, root/category grouping, and all redirect-source relation IDs. Cache it for five minutes using Django's configured cache backend. In a multi-process deployment, configure a shared cache backend such as Redis; the local-memory backend is supported for development and single-process use only. Add a local invalidation function and connect it to the post-commit hooks from Task 1. The snapshot builder must detect legacy parent cycles, log the affected IDs, and place active members into `Other` without looping.

Define active canonical tags as `archived=False` and `redirect_to__isnull=True`. Resolve requested source IDs through their redirect chain only when the final target is active canonical. Return canonical IDs only. Suggestions must receive selected canonical IDs and exclude them from every result list.

- [ ] **Step 7: Implement bounded aggregation and paging**

For every picker item, aggregate distinct question IDs across its canonical relation-ID union and active descendants, filtering `deleted_at__isnull=True`, `archived=False`, plus selected subject IDs when present. Use the same relation-ID union for result filtering and counts. Use `Count("question_id", distinct=True)` semantics. Resolve relation IDs through `Question.tags.through` in fixed batches below SQLite's parameter limit. Every final count and question-filter predicate must also use bounded through-table joins, correlated subqueries, or OR-ed `__in` batches; never bind the combined relation-ID set as one oversized `IN` expression. The regression test must exercise the final count and filtering path with more than 999 relation IDs. Do not compute counts per tag in a Python loop. Sort common tags and categories by descending distinct question count, then name and stable ID; sort members with the same deterministic tie-breakers. Ensure the normal response and suggestion endpoint stay inside the tested query budget.

- [ ] **Step 8: Run picker tests and verify GREEN**

```powershell
& 'C:\Users\14633\anaconda3\Scripts\pytest.exe' question_bank/tests/test_search_and_tags.py -q -k "canonical or tag_picker or common_tags or category_count or legacy_cycle or query_budget or cache_invalidation" --basetemp .runtime\pytest-tag-picker-green
```

Expected: PASS.

- [ ] **Step 9: Review only the tag-picker diff**

Use `git diff -- question_bank/search.py question_bank/tests/test_search_and_tags.py` and confirm no unrelated hunk from the existing retrieval page or question workbench was changed. Do not stage or commit in this dirty worktree.

## Task 3: Expose Picker Context and Suggestion Endpoint

**Files:**

* Modify: `question_bank/views.py`
* Modify: `question_bank/urls.py`
* Modify: `question_bank/tests/test_search_and_tags.py`

- [ ] **Step 1: Write failing view and endpoint tests**

```python
def test_question_list_exposes_canonical_tag_picker_context(client, subject):
    tag = Tag.objects.create(name="极限")
    question = make_question(subject, "带标签题")
    question.tags.add(tag)
    response = client.get(reverse("question-list"), {"subject": subject.pk})
    assert response.status_code == 200
    assert response.context["tag_picker"].common_tags

def test_tag_suggestions_are_subject_scoped_and_bounded(client, subject):
    response = client.get(reverse("tag-suggestions"), {"q": "极", "subject": subject.pk})
    assert response.status_code == 200
    assert len(response.json()["items"]) <= 20
```

Test empty and whitespace queries, repeated selected subject values, repeated selected tag values, pagination parameters, canonical `selected_tags` context, invalid tag parameters, JSON response shape, selected-tag exclusion from suggestions, a query longer than 100 characters returning HTTP 400, and a category page that preserves question filters.

- [ ] **Step 2: Run view tests and verify RED**

```powershell
& 'C:\Users\14633\anaconda3\Scripts\pytest.exe' question_bank/tests/test_search_and_tags.py -q -k "tag_picker_context or tag_suggestions" --basetemp .runtime\pytest-tag-view-red
```

Expected: FAIL because no endpoint or picker context exists.

- [ ] **Step 3: Update `question_list` context construction**

Use canonical tag IDs from the service for both `build_question_queryset` and active-filter display. Preserve all existing non-tag filter values and pagination query behavior. Pass `tag_picker_q`, `tag_picker_group`, and `tag_picker_page` only to the picker builder. Do not let picker-only parameters affect question results.

Build tag active-filter chips from canonical items. Keep one canonical `tag` value per selected item so removal works for old redirect URLs.

- [ ] **Step 4: Add GET-only suggestion view and route**

Register `path("tags/suggestions/", views.tag_suggestions, name="tag-suggestions")`. Accept `q`, repeated `subject`, and repeated canonical `selected_tag` query values. Return `JsonResponse({"items": [...]})` with canonical tag ID, name, category label, descendant depth, and count. Reject a query longer than 100 characters with HTTP 400. Return an empty list for blank prefixes. Do not expose archived or redirect-source tags, and exclude selected canonical IDs from the response.

- [ ] **Step 5: Run focused view tests and full search tests**

```powershell
& 'C:\Users\14633\anaconda3\Scripts\pytest.exe' question_bank/tests/test_search_and_tags.py -q --basetemp .runtime\pytest-tag-view-green
```

Expected: PASS.

- [ ] **Step 6: Review only the scoped diff**

Use `git diff -- question_bank/views.py question_bank/urls.py question_bank/tests/test_search_and_tags.py` and inspect only the tag-picker hunks. Do not stage or commit in this dirty worktree.

## Task 4: Render Bounded Native Tag Picker

**Files:**

* Modify: `question_bank/templates/question_bank/question_list.html`
* Modify: `static/question_bank/css/app.css`
* Modify: `tests/test_templates.py`

- [ ] **Step 1: Write failing template and CSS contract tests**

Assert the retrieval page contains:

```python
assert 'data-tag-picker' in body
assert 'name="tag_picker_q"' in body
assert 'data-tag-suggestions-url=' in body
assert 'data-selected-tags' in body
assert 'data-common-tags' in body
assert 'data-tag-category' in body
assert 'data-tag-count' in body
assert 'data-category-expand' in body
```

Add contracts for an `All in` category checkbox, selected tag checkbox fieldset, removable selected-tag chip linked to its checkbox, native search submit control, `aria-expanded`, `aria-controls` with a matching category panel ID, a `data-tag-category-panel` fragment root, and count text. Assert every rendered tag option includes a count element, new CSS selectors support `.tag-picker`, `.tag-picker__selected`, `.tag-picker__common`, `.tag-picker__category`, and responsive wrapping, and an empty search result includes a link to tag management.

- [ ] **Step 2: Run template tests and verify RED**

```powershell
& 'C:\Users\14633\anaconda3\Scripts\pytest.exe' tests/test_templates.py -q -k "tag_picker or question_list" --basetemp .runtime\pytest-tag-template-red
```

Expected: FAIL because the current template renders every tag in one checkbox group.

- [ ] **Step 3: Replace only the tag fieldset markup**

Leave the top-level filter-panel structure and non-tag filters in place. In source order render:

1. `Selected tags` native checkbox fieldset, including selected zero-count values.
2. Prefix-search input `tag_picker_q`, visible native submit command, suggestion status, and a bounded server-rendered search-result list.
3. Common tag checkboxes with count values.
4. At most 12 category groups, each with an `All in <category>` checkbox, six visible members, and a normal link for the next server page.
5. `Other` inside the same category list when needed.

Each normal picker list excludes already selected IDs. Selected tags render both a checked native checkbox and a visual chip; the chip's remove control unchecks or removes the corresponding checkbox while the checkbox remains usable without JavaScript. Every checkbox uses canonical `name="tag" value="..."`; no JavaScript is needed for submission or deselection. Category buttons use `aria-expanded` and `aria-controls` pointing to the matching panel ID. Preserve GET action, active filters, saved filters, and result-anchor behavior. When a server-rendered prefix search has no results, show a link to the tag-management page.

- [ ] **Step 4: Add the selected/common/category CSS styles**

Use existing colors and spacing. Keep selected tags distinct from common tags without nested card styling. Ensure counts have a fixed visual treatment, hierarchy depth has clear indentation, long names wrap, buttons and checkboxes retain touch-size minimums, and narrow widths use one column.

- [ ] **Step 5: Run template tests and inspect server-rendered fallback**

```powershell
& 'C:\Users\14633\anaconda3\Scripts\pytest.exe' tests/test_templates.py question_bank/tests/test_search_and_tags.py -q -k "tag_picker or question_list" --basetemp .runtime\pytest-tag-template-green
```

Expected: PASS.

- [ ] **Step 6: Review only the scoped diff**

Use `git diff -- question_bank/templates/question_bank/question_list.html static/question_bank/css/app.css tests/test_templates.py` and inspect only the tag-picker hunks. Do not stage or commit in this dirty worktree.

## Task 5: Add Progressive Search and Category Enhancement

**Files:**

* Modify: `static/question_bank/js/app.js`
* Modify: `tests/test_templates.py`
* Create: `tests/test_question_list_browser.py`

- [ ] **Step 1: Write failing static and browser tests**

Cover debounced request creation, `credentials: "same-origin"`, `AbortController` cancellation, safe DOM-node rendering, 20-result bound, loading and error status, keyboard navigation, category expansion state, navigation through the normal category page link, selected-tag removal, and native controls remaining usable after a suggestion request failure.

The browser test in `tests/test_question_list_browser.py` should contain only Playwright scenarios: load a retrieval page with more than eight tags, type a prefix, check a suggestion, submit, and assert the canonical tag query returns the expected question. Add the no-JavaScript Django-client request test that unchecks a selected long-tail tag and submits the form to `question_bank/tests/test_search_and_tags.py`, so it still runs when Playwright is unavailable. Keep `tests/test_question_form_browser.py` unchanged for the workbench.

- [ ] **Step 2: Run browser tests and record the actual state**

```powershell
& 'C:\Users\14633\anaconda3\Scripts\pytest.exe' tests/test_question_list_browser.py -q -m browser -k "tag_picker" --basetemp .runtime\pytest-tag-browser-red
```

Expected before implementation: FAIL when Playwright and Chromium are installed. If the dependency remains absent, record the module skip and continue with static and Django tests only.

- [ ] **Step 3: Implement progressive prefix search**

Initialize only under `[data-tag-picker]`. Debounce prefix requests by 200ms. Read the canonical IDs from `data-selected-tags` and append them as repeated `selected_tag` query parameters to every suggestion request. Cancel stale suggestions with `AbortController`. Render response text with `document.createElement` and `textContent`, then insert normal checkbox labels. Announce search status in the designated live region.

- [ ] **Step 4: Implement category toggles and selected-chip removal**

For category pages, let the existing list-route links remain valid and do not introduce a second category JSON API. Implement category enhancement by fetching the existing `tag_picker_group` and `tag_picker_page` link with `credentials: "same-origin"`, parsing the returned HTML with `DOMParser`, extracting the matching `[data-tag-category-panel="<group>"]` fragment, and replacing the current panel. Update both the corresponding `aria-expanded` state and `aria-controls` panel relationship. On fetch, parse, or selector failure, retain the ordinary link and its navigation behavior. The browser test must click the category paging control, verify that the bounded fragment loads without changing unrelated question results, and verify failure fallback leaves the link usable. Selected-tag chip removal must update the linked native checkbox and selected state without changing form serialization semantics. Do not hide selected checkbox controls or change form serialization semantics.

- [ ] **Step 5: Run static, Django, and available browser tests**

```powershell
& 'C:\Users\14633\anaconda3\Scripts\pytest.exe' question_bank/tests/test_search_and_tags.py tests/test_templates.py -q --basetemp .runtime\pytest-tag-js-green
& 'C:\Users\14633\anaconda3\Scripts\pytest.exe' tests/test_question_list_browser.py -q -m browser -k "tag_picker" --basetemp .runtime\pytest-tag-browser-green
node --check static/question_bank/js/app.js
& 'C:\Users\14633\anaconda3\python.exe' -m compileall -q tests/test_question_list_browser.py question_bank/migrations/0002_tag_picker_search_index.py
```

Expected: non-browser checks PASS. Browser result is PASS only when Playwright is installed; otherwise report the observed skip.

- [ ] **Step 6: Review only the scoped diff**

Use `git diff -- static/question_bank/js/app.js tests/test_templates.py`, then inspect the untracked browser module explicitly with `Get-Content tests/test_question_list_browser.py`. Do not stage or commit in this dirty worktree.

## Task 6: Final Verification

**Files:**

* Modify only when verification exposes a scoped defect.

- [ ] **Step 1: Run all tests**

```powershell
& 'C:\Users\14633\anaconda3\Scripts\pytest.exe' -q --basetemp .runtime\pytest-scalable-tag-filter-final
```

Expected: all non-browser tests pass. Report browser skips accurately if Playwright remains unavailable. Do not commit verification output in this dirty worktree.

- [ ] **Step 2: Run framework and static checks**

```powershell
& 'C:\Users\14633\anaconda3\python.exe' manage.py check
& 'C:\Users\14633\anaconda3\python.exe' manage.py makemigrations --check --dry-run
& 'C:\Users\14633\anaconda3\python.exe' manage.py collectstatic --noinput
node --check static/question_bank/js/app.js
git -c safe.directory=C:/Users/14633/study-vault/.worktrees/math-question-bank diff --check
& 'C:\Users\14633\anaconda3\python.exe' -m compileall -q tests/test_question_list_browser.py question_bank/migrations/0002_tag_picker_search_index.py
```

- [ ] **Step 3: Run HTTP smoke checks**

Start Django on an unused loopback port. Verify question list 200 responses for empty search, subject-scoped common tags, canonical redirect URL, all-invalid tag IDs, native `tag_picker_q`, and suggestion JSON. Confirm the response never contains more than its specified common, category, and suggestion bounds.

- [ ] **Step 4: Inspect scope and worktree status**

Use `git status --short` and review the full diff. Confirm work stays limited to the tag picker service, one migration, list view/template/style/script, and tests. Keep prior question-workbench and retrieval-page changes intact.

- [ ] **Step 5: Apply and review verification-only fixes when needed**

If verification exposes a scoped defect, use `apply_patch` for the smallest affected hunk, rerun the relevant check, and inspect the resulting diff. Leave all changes unstaged and uncommitted, including verification fixes, so unrelated pre-existing work remains separable.
