# Question Search Layout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the question search page around one complete top filter panel and a readable single-column result stream that shows full sanitized statements and all question images.

**Architecture:** Keep the existing URL parameters and search service stable. Extend the result queryset and view context for attachments, rendered statements, and clean pagination URLs, then replace only the search-page template, page-specific CSS, and progressive JavaScript hooks. Core filtering and navigation must remain functional without JavaScript.

**Tech Stack:** Django 5 templates and ORM, pytest and pytest-django, vanilla CSS, vanilla JavaScript, MathJax.

---

## Task 1: Prepare result data and stable pagination URLs

**Files:**
- Modify: `question_bank/search.py`
- Modify: `question_bank/views.py`
- Modify: `tests/test_templates.py`
- Test: `tests/test_templates.py`

- [ ] **Step 1: Write failing tests for rendered statements, ordered attachments, and pagination parameters**

Extend `tests/test_templates.py` with fixtures that create a question containing Markdown, LaTeX, unsafe HTML, and two ordered image attachments. Add assertions that the list response exposes sanitized `statement_html`, retains MathJax delimiters, and returns attachments in `sort_order` order. Add a filtered request and verify active-filter labels contain human-readable names instead of database IDs.

Add 21 matching questions and request page 2 with other filters. Assert the pagination base query excludes every existing `page` value while retaining `q`, `subject`, and tag parameters.

Example assertions:

```python
assert "<strong>重点</strong>" in response.context["questions"][0].statement_html
assert "<script" not in response.context["questions"][0].statement_html
assert [item.sort_order for item in response.context["questions"][0].attachments.all()] == [0, 1]
assert "科目: 数学分析" in [item["label"] for item in response.context["active_filters"]]
assert "page=" not in response.context["pagination_query"]
assert "q=%E6%9E%81%E9%99%90" in response.context["pagination_query"]
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```powershell
& 'C:\Users\14633\anaconda3\python.exe' -m pytest tests/test_templates.py -q --basetemp .runtime/pytest-search-context-red
```

Expected: FAIL because attachments are not prefetched, list questions have no `statement_html`, and `pagination_query` is absent.

- [ ] **Step 3: Implement the minimal queryset and context changes**

In `question_bank/search.py`, add `attachments` to the existing `prefetch_related` call:

```python
return queryset.select_related("subject", "section").prefetch_related(
    "tags", "knowledge_cards", "attachments"
).distinct().order_by("-updated_at", "id")
```

In `question_bank/views.py`, materialize the filter option querysets once, build lookup dictionaries for subjects, sections, tags, knowledge cards, and mastery choices, then use those dictionaries for human-readable active-filter labels. Render each paginated question statement with the existing sanitizer and build a page-free query string:

```python
for question in page.object_list:
    question.statement_html = render_markdown(question.statement)

pagination_params = request.GET.copy()
pagination_params.pop("page", None)
context["pagination_query"] = pagination_params.urlencode()
```

Do not add a new serializer or view model for this single template.

- [ ] **Step 4: Run the focused tests and verify GREEN**

Run the command from Step 2.

Expected: all `tests/test_templates.py` tests pass.

- [ ] **Step 5: Commit the data preparation change**

```bash
git add question_bank/search.py question_bank/views.py tests/test_templates.py
git commit -m "refactor: prepare rich question search results"
```

## Task 2: Replace the search page information architecture

**Files:**
- Modify: `question_bank/templates/question_bank/question_list.html`
- Modify: `tests/test_templates.py`
- Test: `tests/test_templates.py`

- [ ] **Step 1: Write failing structural template tests**

Add a test that requests the question list and checks:

```python
assert 'class="filter-panel"' in body
assert 'class="filter-panel__search"' in body
assert 'class="filter-group"' in body
assert 'id="question-results"' in body
assert 'class="question-result-card"' in body
assert 'class="question-statement"' in body
assert 'class="question-attachments"' in body
assert 'data-clear-filters' in body
assert 'data-filter-key=' in body
```

Also assert that personal solution, reference solution, and error-note text do not appear in the list response.

- [ ] **Step 2: Run the structural test and verify RED**

Run:

```powershell
& 'C:\Users\14633\anaconda3\python.exe' -m pytest tests/test_templates.py -q -k "question_list" --basetemp .runtime/pytest-search-template-red
```

Expected: FAIL because the current template uses a side filter and truncated plain text.

- [ ] **Step 3: Build the top filter panel**

Rewrite `question_list.html` with this semantic structure:

```html
<header class="search-heading">
  <div><p class="eyebrow">题库</p><h1>题目检索</h1></div>
  <p class="result-count">{{ paginator.count }} 道题目</p>
</header>

<section class="filter-panel" aria-labelledby="filter-heading">
  <form method="get" action="{% url 'question-list' %}#question-results" id="question-filter-form">
    <div class="filter-panel__search">...</div>
    <div class="filter-row">科目和章节...</div>
    <div class="filter-row">标签和知识卡片...</div>
    <div class="filter-row">掌握程度、到期和操作...</div>
  </form>
</section>
```

Every fieldset must keep a visible legend. Checkbox choices use label elements with a shared `filter-option` class. Keep standard form controls so the page works without JavaScript.

- [ ] **Step 4: Build the full-content result stream**

Each result uses one `article.question-result-card` with metadata, title, `question.statement_html|safe`, ordered attachment images, tag chips, mastery status, review time, linked knowledge-card names, detail link, and review link. Images use `loading="lazy"` and `decoding="async"`.

Do not include personal solution, reference solution, error note, or knowledge-card body. Do not render an empty attachment container when a question has no images.

Render active filters as buttons when JavaScript is available and as readable labels otherwise. Add a normal link to `{% url 'question-list' %}` for clearing all filters.

Build pagination URLs with:

```django
?{% if pagination_query %}{{ pagination_query }}&{% endif %}page={{ page_number }}#question-results
```

- [ ] **Step 5: Run template tests and verify GREEN**

Run the command from Step 1 without `-k`.

Expected: all `tests/test_templates.py` tests pass.

- [ ] **Step 6: Commit the template change**

```bash
git add question_bank/templates/question_bank/question_list.html tests/test_templates.py
git commit -m "feat: reorganize question search page"
```

## Task 3: Add the responsive search-page visual system

**Files:**
- Modify: `static/question_bank/css/app.css`
- Modify: `tests/test_templates.py`
- Test: `tests/test_templates.py`

- [ ] **Step 1: Write a failing CSS contract test**

Read `static/question_bank/css/app.css` and assert that it defines the page-specific selectors and responsive breakpoint:

```python
assert ".filter-panel" in css
assert ".filter-row" in css
assert ".filter-option" in css
assert ".question-result-card" in css
assert ".question-attachments" in css
assert "@media(max-width:760px)" in css.replace(" ", "")
```

Keep assertions structural. Do not lock tests to exact color values or pixel spacing.

- [ ] **Step 2: Run the CSS contract test and verify RED**

Run:

```powershell
& 'C:\Users\14633\anaconda3\python.exe' -m pytest tests/test_templates.py -q -k "search_page_css" --basetemp .runtime/pytest-search-css-red
```

Expected: FAIL because the new selectors are absent.

- [ ] **Step 3: Implement desktop styling**

Append a focused search-page section to `app.css`. Use the existing color variables and add only semantic state variables when necessary. Requirements:

1. Filter panel is one unframed top work surface with at most 8px corner radius.
2. Search input occupies the full first row.
3. Filter groups use responsive grid tracks and stable minimum widths.
4. Checkbox labels look selectable while retaining native inputs and visible focus.
5. Selected options use teal background tint and clear border contrast.
6. Results form a single column with restrained separators and no nested cards.
7. Full statement typography supports MathJax and long formulas.
8. Images use stable aspect constraints, `object-fit: contain`, and no cropping.
9. Metadata stays visually secondary to the statement.

- [ ] **Step 4: Implement mobile styling**

At `760px` and below, make filter grids one column, allow option wrapping, make primary actions easy to tap, prevent long labels from overflowing, and keep images within the viewport. At a smaller breakpoint, stack result actions and metadata where needed.

Honor the existing `prefers-reduced-motion` rules and keep letter spacing at zero.

- [ ] **Step 5: Run template tests and verify GREEN**

Run all template tests.

Expected: PASS.

- [ ] **Step 6: Commit the visual layer**

```bash
git add static/question_bank/css/app.css tests/test_templates.py
git commit -m "style: refine question search layout"
```

## Task 4: Add progressive filter interactions

**Files:**
- Modify: `static/question_bank/js/app.js`
- Modify: `question_bank/templates/question_bank/question_list.html`
- Modify: `tests/test_templates.py`
- Test: `tests/test_templates.py`

- [ ] **Step 1: Write failing interaction-hook tests**

Assert that active-filter controls provide `data-filter-key` and `data-filter-value`, the clear link uses `data-clear-filters`, the save button has an `aria-live` status target, and the JavaScript asset contains handlers for these hooks.

- [ ] **Step 2: Run the hook tests and verify RED**

Run:

```powershell
& 'C:\Users\14633\anaconda3\python.exe' -m pytest tests/test_templates.py -q -k "filter_interaction" --basetemp .runtime/pytest-search-js-red
```

Expected: FAIL because removable filter controls and accessible save feedback are absent.

- [ ] **Step 3: Implement active-filter removal**

In `app.js`, bind buttons with `data-filter-key`. Build a `URL` from `window.location.href`, remove only the selected key/value pair, delete `page`, set `hash` to `question-results`, and navigate to the new URL. Never build the query string by concatenating raw values.

- [ ] **Step 4: Move saved-filter behavior into the shared asset**

Move the existing inline local-storage code from the template into `app.js`. Keep the storage key `math-question-bank:saved-filters`, retain the newest 20 entries, catch invalid stored JSON, and update the `aria-live` status without changing button width.

- [ ] **Step 5: Run template tests and the complete suite**

Run:

```powershell
& 'C:\Users\14633\anaconda3\python.exe' -m pytest tests/test_templates.py -q --basetemp .runtime/pytest-search-js-green
& 'C:\Users\14633\anaconda3\python.exe' -m pytest -q --basetemp .runtime/pytest-search-full
```

Expected: all tests pass.

- [ ] **Step 6: Commit the interaction change**

```bash
git add static/question_bank/js/app.js question_bank/templates/question_bank/question_list.html tests/test_templates.py
git commit -m "feat: improve question filter interactions"
```

## Task 5: Final verification and handoff

**Files:**
- Modify only if verification reveals a scoped defect.

- [ ] **Step 1: Run all automated verification**

```powershell
& 'C:\Users\14633\anaconda3\python.exe' -m pytest -q --basetemp .runtime/pytest-search-final
& 'C:\Users\14633\anaconda3\python.exe' manage.py check
& 'C:\Users\14633\anaconda3\python.exe' manage.py makemigrations --check --dry-run
& 'C:\Users\14633\anaconda3\python.exe' manage.py collectstatic --noinput
```

Expected: all tests pass, Django reports no issues or model changes, and static collection succeeds.

- [ ] **Step 2: Perform HTTP smoke checks**

Start the development server on an available loopback port. Verify the default list, a multi-filter list, page 2, an empty result, and a question with multiple images all return HTTP 200.

- [ ] **Step 3: Perform visual checks when browser permission is available**

Check desktop and mobile widths for filter wrapping, statement readability, MathJax rendering, image containment, focus states, and overlap. If browser permission remains unavailable, report the limitation without claiming visual verification.

- [ ] **Step 4: Review the branch diff**

Confirm only the search context, template, CSS, JavaScript, and associated tests changed. Run `git diff --check` and inspect the final status.

- [ ] **Step 5: Commit any verification-only fixes**

Use a narrowly scoped commit message only when Step 2 or Step 3 required a correction.
