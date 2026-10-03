# Question Form Workbench Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild question creation and editing as an image-first workbench with quick and complete modes, safe attachment lifecycle management, live math preview, recoverable local drafts, and concurrency protection.

**Architecture:** Keep `QuestionForm` as the field-validation boundary, then add focused attachment and versioning services for transactional behavior. Render one progressively enhanced Django form; small ES modules own editor controls, attachment queues, and IndexedDB drafts, while native form controls remain usable without JavaScript.

**Tech Stack:** Django 5.2, pytest and pytest-django, vanilla ES modules, IndexedDB, MathJax 3, Playwright Chromium, SQLite locally, Ubuntu 22.04 deployment.

---

## Repository Command Preflight

The worktree may be owned by a different Windows security principal. Before any task commit or status check, use the explicit repository-safe form below, and keep the same prefix for all later Git commands:

```powershell
git -c safe.directory=C:/Users/14633/study-vault/.worktrees/math-question-bank status --short --branch
```

If a task shows a `git add` or `git commit` command, prefix it with `git -c safe.directory=C:/Users/14633/study-vault/.worktrees/math-question-bank`.

## File Map

**Create:**

* `question_bank/attachments.py`: attachment plan parsing, validation, persistence, rollback cleanup.
* `question_bank/versioning.py`: signed optimistic-concurrency tokens.
* `question_bank/management/commands/cleanup_orphan_attachments.py`: dry-run-first orphan reconciliation.
* `static/question_bank/css/question-form.css`: workbench-only responsive styles.
* `static/question_bank/js/question-form.js`: form entry point and cross-module coordination.
* `static/question_bank/js/question-form-editor.js`: mode tabs, accessible field tabs, Markdown helpers, live preview.
* `static/question_bank/js/question-form-attachments.js`: file queue, previews, ordering, deletion, keyboard controls.
* `static/question_bank/js/question-form-drafts.js`: IndexedDB storage, session ownership, recovery, submit synchronization.
* `question_bank/tests/test_attachments.py`: attachment protocol and storage lifecycle tests.
* `question_bank/tests/test_versioning.py`: token and conflict tests.
* `tests/test_question_form_browser.py`: Playwright behavior tests.
* `requirements-dev.txt`: development and browser-test dependencies.

**Modify:**

* `question_bank/forms.py`: explicit labels, save intent normalization, hidden draft input.
* `question_bank/models.py`: safe post-commit attachment-file cleanup logging.
* `question_bank/views.py`: create/edit orchestration, preview endpoint, version conflicts, success marker.
* `question_bank/urls.py`: Markdown preview route.
* `question_bank/templates/question_bank/question_form.html`: semantic workbench and fallback controls.
* `question_bank/tests/test_crud_views.py`: create/edit behavior and integration regressions.
* `question_bank/tests/test_content_safety.py`: preview endpoint sanitization.
* `tests/test_templates.py`: template, CSS, and script contracts.
* `pytest.ini`: browser marker.
* `README.md`: development browser-test instructions.

## Task 1: Define Save Intent and Form Labels

**Files:**

* Modify: `question_bank/forms.py`
* Modify: `question_bank/tests/test_crud_views.py`

- [ ] **Step 1: Write failing save-intent tests**

Add tests proving `save_intent=draft` creates a blank draft, `save_intent=publish` rejects empty formal content, a published edit can explicitly become a draft, and an invalid request preserves `submitted_intent` in response context.

```python
response = client.post(reverse("question-create"), {"save_intent": "draft"})
assert response.status_code == 302
assert Question.objects.get().draft is True

response = client.post(reverse("question-create"), {"save_intent": "publish"})
assert response.status_code == 200
assert "正式题目至少需要" in response.content.decode()
assert response.context["submitted_intent"] == "publish"
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```powershell
& 'C:\Users\14633\anaconda3\python.exe' -m pytest question_bank/tests/test_crud_views.py -q -k "intent or draft_question or formal_question" --basetemp .runtime/pytest-form-intent-red
```

Expected: FAIL because the view still reads the raw `draft` checkbox and does not expose `submitted_intent`.

- [ ] **Step 3: Normalize intent inside `QuestionForm`**

Keep `draft` in `Meta.fields`, render it with `HiddenInput`, and add a `save_intent` constructor argument. For bound data, copy the `QueryDict` before assigning the derived `draft` value so model validation sees the intended state.

```python
def __init__(self, *args, save_intent=None, **kwargs):
    if args and args[0] is not None:
        data = args[0].copy()
        data["draft"] = "on" if save_intent == "draft" else ""
        args = (data, *args[1:])
    self.save_intent = save_intent
    super().__init__(*args, **kwargs)
```

Add explicit Chinese labels for all question fields. Reject unknown or missing bound intents with a non-field form error.

- [ ] **Step 4: Pass intent through create and edit views**

Use `request.POST.get("save_intent")` for bound requests. Add `submitted_intent` to `_question_form_context`. Do not change attachment handling yet.

- [ ] **Step 5: Run the focused tests and all CRUD tests**

Run:

```powershell
& 'C:\Users\14633\anaconda3\python.exe' -m pytest question_bank/tests/test_crud_views.py -q --basetemp .runtime/pytest-form-intent-green
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git -c safe.directory=C:/Users/14633/study-vault/.worktrees/math-question-bank add question_bank/forms.py question_bank/views.py question_bank/tests/test_crud_views.py
git -c safe.directory=C:/Users/14633/study-vault/.worktrees/math-question-bank commit -m "refactor: define question save intent"
```

## Task 2: Add Safe Live Markdown Preview

**Files:**

* Modify: `question_bank/urls.py`
* Modify: `question_bank/views.py`
* Modify: `question_bank/tests/test_content_safety.py`

- [ ] **Step 1: Write failing preview endpoint tests**

Test POST-only behavior, CSRF enforcement with `Client(enforce_csrf_checks=True)`, JSON output, sanitization, MathJax delimiter retention, and a bounded input size.

```python
response = client.post(
    reverse("markdown-preview"),
    {"source": "**重点** $x^2$ <script>alert(1)</script>"},
)
assert response.status_code == 200
payload = response.json()
assert "<strong>重点</strong>" in payload["html"]
assert "$x^2$" in payload["html"]
assert "<script" not in payload["html"]
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```powershell
& 'C:\Users\14633\anaconda3\python.exe' -m pytest question_bank/tests/test_content_safety.py -q -k preview --basetemp .runtime/pytest-preview-red
```

Expected: FAIL because `markdown-preview` is not registered.

- [ ] **Step 3: Implement the endpoint**

Add `@require_POST` view `markdown_preview`. Reject source over 100,000 characters with HTTP 400 and return `JsonResponse({"html": render_markdown(source)})`. Register `path("markdown-preview/", ...)` and retain Django CSRF middleware protection. The pytest-django tests in this task use `Client(enforce_csrf_checks=True)` to prove missing and invalid tokens return 403. Browser header behavior is implemented and tested in Task 9 after the browser harness exists.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run the command from Step 2. Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git -c safe.directory=C:/Users/14633/study-vault/.worktrees/math-question-bank add question_bank/urls.py question_bank/views.py question_bank/tests/test_content_safety.py
git -c safe.directory=C:/Users/14633/study-vault/.worktrees/math-question-bank commit -m "feat: add safe markdown preview endpoint"
```

## Task 3: Parse Complete Attachment Plans

**Files:**

* Create: `question_bank/attachments.py`
* Create: `question_bank/tests/test_attachments.py`

- [ ] **Step 1: Write failing enhanced-protocol tests**

Cover valid mixed existing and new order, duplicated tokens, unknown IDs, foreign attachments, omitted survivors, omitted uploads, deleted items still present in order, out-of-range new indices, and rejection of requests that mix `attachment_order` with fallback removal or position fields.

The public API should be:

```python
plan = parse_attachment_plan(
    post=request.POST,
    uploads=request.FILES.getlist("attachments"),
    question=question,
)
assert plan.ordered_items == (Existing(first.pk), NewUpload(1), Existing(second.pk))
```

- [ ] **Step 2: Write failing fallback-protocol tests**

When `attachment_order` is absent, test `remove_attachment`, present and missing `attachment_position_<id>` values, normalized gaps, duplicate positions, invalid values, and new files appended in multipart order.

- [ ] **Step 3: Run tests and verify RED**

Run:

```powershell
& 'C:\Users\14633\anaconda3\python.exe' -m pytest question_bank/tests/test_attachments.py -q --basetemp .runtime/pytest-attachment-plan-red
```

Expected: collection FAIL because the service module does not exist.

- [ ] **Step 4: Implement immutable plan types and parser**

Use frozen dataclasses and one validation exception carrying user-facing messages.

```python
@dataclass(frozen=True)
class ExistingAttachment:
    pk: int

@dataclass(frozen=True)
class NewUpload:
    index: int

@dataclass(frozen=True)
class AttachmentPlan:
    ordered_items: tuple[ExistingAttachment | NewUpload, ...]
    removed_ids: frozenset[int]
```

Use `post.getlist("attachment_order")` to preserve order. Validate exact set equality between submitted tokens and expected survivors plus all uploads. If any `attachment_order` field is present, reject every fallback-only field such as `attachment_position_<id>` and use no fallback interpretation. If no `attachment_order` field is present, reject enhanced-only tokens and use the native multipart protocol.

- [ ] **Step 5: Run tests and verify GREEN**

Run the command from Step 3. Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git -c safe.directory=C:/Users/14633/study-vault/.worktrees/math-question-bank add question_bank/attachments.py question_bank/tests/test_attachments.py
git -c safe.directory=C:/Users/14633/study-vault/.worktrees/math-question-bank commit -m "feat: validate attachment ordering plans"
```

## Task 4: Persist Attachments Without File Leaks

**Files:**

* Modify: `question_bank/attachments.py`
* Modify: `question_bank/models.py`
* Create: `question_bank/management/commands/cleanup_orphan_attachments.py`
* Modify: `question_bank/tests/test_attachments.py`

- [ ] **Step 1: Write failing persistence tests**

Test two-phase reorder across image and non-image attachments, deletion after commit, new images at interleaved final positions, and continuous final `sort_order` values.

- [ ] **Step 2: Write failing rollback tests**

Use a temporary `MEDIA_ROOT` and inject a failure after one new file is stored. Assert the question transaction rolls back and the new file is removed. Mock `storage.delete` failure in both the attachment service and the existing `QuestionAttachment` post-delete cleanup path, and assert an error containing the filename and exception is logged while the original exception remains visible.

- [ ] **Step 3: Write failing cleanup-command tests**

Create referenced, recent orphan, and old orphan files. Assert default execution reports candidates without deletion. Assert `--delete --older-than-hours 24` removes only old unreferenced files.

- [ ] **Step 4: Run tests and verify RED**

Run:

```powershell
& 'C:\Users\14633\anaconda3\python.exe' -m pytest question_bank/tests/test_attachments.py -q --basetemp .runtime/pytest-attachment-storage-red
```

- [ ] **Step 5: Implement persistence and cleanup**

Expose these functions:

```python
def apply_attachment_plan(question, uploads, plan, created_names): ...
def cleanup_unreferenced_files(created_names, storage): ...
```

Move survivors above `max(sort_order) + len(items) + 1` before writing final positions. Delete validated `removed_ids` inside the same transaction before writing final continuous positions, so a removed row cannot retain a unique `(question, sort_order)` slot. Append every assigned storage name to `created_names` immediately after storage assignment and before model save, including names assigned during a failed model save. Recheck `QuestionAttachment.objects.filter(file=name).exists()` before deletion. Update or bypass the existing `QuestionAttachment` post-delete signal so its `on_commit` cleanup logs filename and exception without masking the original database error.

Implement the management command with dry run as the default. Require `--delete` for mutation and use storage modification timestamps plus a 24-hour default threshold.

- [ ] **Step 6: Run focused tests and verify GREEN**

Run the command from Step 4. Expected: PASS.

- [ ] **Step 7: Commit**

```powershell
git -c safe.directory=C:/Users/14633/study-vault/.worktrees/math-question-bank add question_bank/attachments.py question_bank/models.py question_bank/management/commands/cleanup_orphan_attachments.py question_bank/tests/test_attachments.py
git -c safe.directory=C:/Users/14633/study-vault/.worktrees/math-question-bank commit -m "feat: persist and reconcile question attachments"
```

## Task 5: Add Signed Optimistic Concurrency

**Files:**

* Create: `question_bank/versioning.py`
* Create: `question_bank/tests/test_versioning.py`

- [ ] **Step 1: Write failing token tests**

Test stable signed tokens, tampering rejection, expired-token rejection, and token changes after question field, attachment, tag, or knowledge-card changes. Assert the decoded canonical payload contains the question ID, `updated_at`, every attachment ID, attachment `updated_at`, type, filename, and order, plus sorted tag IDs and knowledge-card IDs. The canonical payload converts UUID values to strings and all timestamps to UTC ISO-8601 strings before signing. The expiry test temporarily sets the module constant to a negative age so it remains deterministic.

```python
token = build_question_version(question)
assert verify_question_version(question, token)
question.tags.add(tag)
assert not verify_question_version(question, token)
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```powershell
& 'C:\Users\14633\anaconda3\python.exe' -m pytest question_bank/tests/test_versioning.py -q --basetemp .runtime/pytest-version-red
```

- [ ] **Step 3: Implement canonical signed payloads**

Use `django.core.signing.dumps/loads` with a feature-specific salt and a module-level 24-hour maximum age named `QUESTION_VERSION_MAX_AGE`. Build a JSON-serializable canonical dictionary by converting UUIDs with `str()` and timestamps with an explicit UTC ISO-8601 formatter, then sort relation IDs and attachments before signing. Expose:

```python
def build_question_version(question) -> str: ...
def verify_question_version(question, token: str) -> bool: ...
```

Treat missing, malformed, expired, or mismatched tokens as conflicts by passing `max_age=QUESTION_VERSION_MAX_AGE` to `loads`. Do not expose raw storage paths beyond the signed payload.

- [ ] **Step 4: Run tests and verify GREEN**

Run the command from Step 2. Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git -c safe.directory=C:/Users/14633/study-vault/.worktrees/math-question-bank add question_bank/versioning.py question_bank/tests/test_versioning.py
git -c safe.directory=C:/Users/14633/study-vault/.worktrees/math-question-bank commit -m "feat: protect question edits from stale writes"
```

## Task 6: Integrate Services Into Create and Edit Views

**Files:**

* Modify: `question_bank/views.py`
* Modify: `question_bank/forms.py`
* Modify: `question_bank/tests/test_crud_views.py`

- [ ] **Step 1: Write failing create and edit integration tests**

Cover enhanced ordering, fallback ordering, removal, mixed attachment types, save intent, and returned context after form errors.

- [ ] **Step 2: Write a failing two-editor conflict test**

GET the edit page twice, extract both `question_version` values, submit the first edit, then submit the second old token. Assert the second response reports conflict and no question, relation, or attachment value changes. Also assert the edit GET, ordinary validation-error response, and 409 response all provide the same context contract: `question`, `question_version`, existing attachments, `submitted_intent`, and bound form errors, so a retry preserves the token and rendered state.

- [ ] **Step 3: Run focused tests and verify RED**

Run:

```powershell
& 'C:\Users\14633\anaconda3\python.exe' -m pytest question_bank/tests/test_crud_views.py -q --basetemp .runtime/pytest-form-services-red
```

- [ ] **Step 4: Refactor the views**

Create one internal save coordinator used by both endpoints. For edits, enter `transaction.atomic()`, fetch with `select_for_update()`, verify `question_version`, bind the form to the locked instance, parse the attachment plan, save form relations, and apply attachments. Catch exceptions outside the atomic block to clean recorded new storage names.

Return HTTP 409 for version conflict while rendering the same form template with `conflict=True`. Redirect successful create and edit submissions back to the corresponding form URL with `?saved=1` so the same form script can confirm local draft cleanup. The success redirect must retain the target question identifier, and the form script must initialize on that response, read the pending `{key, revision}` marker, remove only an exact IndexedDB revision, then call `history.replaceState` to remove `saved=1`.

- [ ] **Step 5: Run CRUD and attachment tests**

Run:

```powershell
& 'C:\Users\14633\anaconda3\python.exe' -m pytest question_bank/tests/test_crud_views.py question_bank/tests/test_attachments.py question_bank/tests/test_versioning.py -q --basetemp .runtime/pytest-form-services-green
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git -c safe.directory=C:/Users/14633/study-vault/.worktrees/math-question-bank add question_bank/forms.py question_bank/views.py question_bank/tests/test_crud_views.py
git -c safe.directory=C:/Users/14633/study-vault/.worktrees/math-question-bank commit -m "feat: integrate safe question editing services"
```

## Task 7: Establish Browser Test Infrastructure

**Files:**

* Create: `requirements-dev.txt`
* Create: `tests/test_question_form_browser.py`
* Modify: `pytest.ini`
* Modify: `README.md`

- [ ] **Step 1: Add the development dependency file**

```text
-r requirements.txt
playwright==1.55.0
```

Register the `browser` marker in `pytest.ini`. Document installation:

```powershell
& 'C:\Users\14633\anaconda3\python.exe' -m pip install -r requirements-dev.txt
& 'C:\Users\14633\anaconda3\python.exe' -m playwright install chromium
```

- [ ] **Step 2: Write a baseline browser test**

Use `pytest.importorskip("playwright.sync_api")`, `live_server`, a temporary media directory, and a fresh browser context. The baseline opens `questions/new/`, checks the page title, and verifies the native form can submit a draft.

- [ ] **Step 3: Install test dependencies when unavailable**

Run the two commands from Step 1. If network access is blocked, request approval and report the browser-test limitation while keeping non-browser tests runnable.

- [ ] **Step 4: Run the baseline and verify its actual state**

Run:

```powershell
& 'C:\Users\14633\anaconda3\python.exe' -m pytest tests/test_question_form_browser.py -q -m browser --basetemp .runtime/pytest-browser-baseline
```

Expected before the new template: the basic load test passes; tests for workbench hooks will be added in later tasks and fail first.

- [ ] **Step 5: Commit**

```powershell
git -c safe.directory=C:/Users/14633/study-vault/.worktrees/math-question-bank add requirements-dev.txt pytest.ini README.md tests/test_question_form_browser.py
git -c safe.directory=C:/Users/14633/study-vault/.worktrees/math-question-bank commit -m "test: add question form browser harness"
```

## Task 8: Build the Semantic Workbench Template and Styles

**Files:**

* Rewrite: `question_bank/templates/question_bank/question_form.html`
* Create: `static/question_bank/css/question-form.css`
* Modify: `tests/test_templates.py`

- [ ] **Step 1: Write failing template structure tests**

Assert the page includes the mode control with quick mode selected by default, image workbench, three tab buttons and panels, error summary, preview region, draft recovery region, save-intent buttons, conflict region, existing non-image attachment rows, fallback positions, searchable relation-control hooks, subject and chapter filter hooks, form-specific stylesheet, stable `data-form-version`, image zoom controls, and a disabled OCR control with its future-availability label.

- [ ] **Step 2: Write a failing CSS contract test**

Assert `.question-workbench`, `.attachment-dropzone`, `.attachment-queue`, `.question-form-tabs`, `.markdown-preview`, and `@media (max-width:980px)` exist. Keep tests independent from exact colors and spacing.

- [ ] **Step 3: Run tests and verify RED**

Run:

```powershell
& 'C:\Users\14633\anaconda3\python.exe' -m pytest tests/test_templates.py -q -k "question_form or question_workbench" --basetemp .runtime/pytest-workbench-template-red
```

- [ ] **Step 4: Rewrite the template with progressive enhancement**

Render every field and fallback control in source order. Use buttons for modes and tabs, visible legends, an error summary linked to field IDs, and repeated `attachment_order` hidden inputs only after JavaScript initializes. Render existing document and other attachments with file icons and names. Add a stable `data-form-version` value to the root, a per-image zoom button that opens a contained accessible preview, and a disabled OCR button with a visible future-availability status. Keep all complete-mode panels visible by default in CSS; JavaScript may collapse inactive panels after initialization, so no-JavaScript users retain every field.

Load `question-form.css` through `extra_head`, and load `question-form.js` as `type="module"` at the end of the content block.

- [ ] **Step 5: Implement responsive styles**

Use a two-column image-first grid above 980px and one column below. Keep cards at 8px radius or less, avoid nested cards, preserve focus rings, give touch controls stable sizes, and contain formulas and images.

- [ ] **Step 6: Run template tests and verify GREEN**

Run the command from Step 3. Expected: PASS.

- [ ] **Step 7: Commit**

```powershell
git -c safe.directory=C:/Users/14633/study-vault/.worktrees/math-question-bank add question_bank/templates/question_bank/question_form.html static/question_bank/css/question-form.css tests/test_templates.py
git -c safe.directory=C:/Users/14633/study-vault/.worktrees/math-question-bank commit -m "feat: build image-first question workbench"
```

## Task 9: Implement Tabs, Preview, and Attachment Queue

**Files:**

* Create: `static/question_bank/js/question-form.js`
* Create: `static/question_bank/js/question-form-editor.js`
* Create: `static/question_bank/js/question-form-attachments.js`
* Modify: `tests/test_question_form_browser.py`
* Modify: `tests/test_templates.py`

- [ ] **Step 1: Write failing browser tests for editor behavior**

Test quick/full switching without value loss, quick mode selected on first load, three-tab keyboard navigation, `Home` and `End`, automatic activation of an error tab, error-summary focus, Markdown helper insertion, debounced preview, stale-request cancellation, sanitized preview output, `X-CSRFToken` plus `credentials: "same-origin"`, invalid-token rejection, preview network failure while retaining the latest successful HTML and showing a non-blocking status, searchable tag and knowledge-card filtering with multi-selection, subject changes filtering chapter options without losing valid selections, and a no-JavaScript view where all complete-mode panels remain visible.

- [ ] **Step 2: Write failing browser tests for attachment behavior**

Upload two images and verify previews, hidden token order, keyboard reorder, deletion, paste handling, and `DataTransfer` reconstruction. Load an edit page with image, document, and other attachments and verify each remains represented. Verify image zoom opens and closes accessibly, and every OCR control is disabled with the future-availability label.

- [ ] **Step 3: Run browser tests and verify RED**

Run:

```powershell
& 'C:\Users\14633\anaconda3\python.exe' -m pytest tests/test_question_form_browser.py -q -m browser -k "editor or attachment" --basetemp .runtime/pytest-workbench-js-red
```

- [ ] **Step 4: Implement the editor module**

Export `initializeEditor(root)`. Implement WAI-ARIA tab behavior with roving `tabindex`, quick mode as the initial state, mode switching, first-error activation, summary navigation, text insertion preserving selection, searchable tag and knowledge-card multi-select wrappers with native `select[multiple]` fallback, subject-driven chapter filtering, and an `AbortController` preview request that reads the page CSRF token, sends `X-CSRFToken`, and sets `credentials: "same-origin"`. On preview failure, retain the last successful HTML and update a non-blocking `aria-live` status. Call `MathJax.typesetPromise([preview])` when available.

- [ ] **Step 5: Implement the attachment module**

Export `initializeAttachmentQueue(root)`. Keep one ordered array of existing and new items. Rebuild the input `FileList` with `DataTransfer`, render safe DOM nodes without HTML string interpolation, expose up/down controls, image zoom controls, disabled OCR controls, and synchronize repeated hidden `attachment_order` inputs.

- [ ] **Step 6: Coordinate modules in the entry point**

Initialize only on `[data-question-form]`. Export the controllers on a narrow `window.QuestionFormWorkbench` object for draft coordination and browser-test observability.

- [ ] **Step 7: Run browser, template, and content-safety tests**

Run:

```powershell
& 'C:\Users\14633\anaconda3\python.exe' -m pytest tests/test_question_form_browser.py tests/test_templates.py question_bank/tests/test_content_safety.py -q --basetemp .runtime/pytest-workbench-js-green
node --check static/question_bank/js/question-form.js
node --check static/question_bank/js/question-form-editor.js
node --check static/question_bank/js/question-form-attachments.js
```

Expected: PASS.

- [ ] **Step 8: Commit**

```powershell
git -c safe.directory=C:/Users/14633/study-vault/.worktrees/math-question-bank add static/question_bank/js/question-form.js static/question_bank/js/question-form-editor.js static/question_bank/js/question-form-attachments.js tests/test_question_form_browser.py tests/test_templates.py
git -c safe.directory=C:/Users/14633/study-vault/.worktrees/math-question-bank commit -m "feat: add question editor and attachment interactions"
```

## Task 10: Implement IndexedDB Draft Recovery

**Files:**

* Create: `static/question_bank/js/question-form-drafts.js`
* Modify: `static/question_bank/js/question-form.js`
* Modify: `question_bank/templates/question_bank/question_form.html`
* Modify: `tests/test_question_form_browser.py`

- [ ] **Step 1: Write failing session and discovery tests**

Test refresh persistence, two new tabs receiving different IDs, copied-session collision producing a new ID, closed-session discovery by `target_key`, selecting a historical draft, deletion, and multiple create drafts remaining independent. Test the localStorage fallback lease with an active unexpired lease, an expired lease that is removed and does not block startup, and cleanup after ownership release. Assert an edit draft stores the server baseline `updated_at` plus the complete attachment signature and that recovery compares both before restoring attachment deletion or ordering. Every draft record also stores the template `form_version` from `data-form-version`; a changed form version makes the draft recover text and new Blobs only, with old attachment operations ignored.

- [ ] **Step 2: Write failing recovery and submission tests**

Test text and Blob persistence, baseline-conflict recovery without old attachment mutations, submit waiting for the IndexedDB transaction, invalid-form Blob reconstruction, matching-revision cleanup after successful redirect, preservation of a newer revision, quota failure messaging, and confirmed submission without draft storage. Define precedence explicitly in the tests: server-returned validated fields and relation selections win over stale draft values; draft-only UI state and new Blob files are restored; old attachment deletion and ordering are restored only when the stored baseline signature still matches.

- [ ] **Step 3: Run browser tests and verify RED**

Run:

```powershell
& 'C:\Users\14633\anaconda3\python.exe' -m pytest tests/test_question_form_browser.py -q -m browser -k draft --basetemp .runtime/pytest-drafts-red
```

- [ ] **Step 4: Implement the draft repository**

Use IndexedDB database `math-question-bank`, version 1, store `drafts`, primary key `key`, indexes `target_key` and `updated_at`. Export repository methods `list`, `get`, `put`, and `delete` that resolve only after transaction completion.

- [ ] **Step 5: Implement session ownership**

Generate IDs with `crypto.randomUUID()`. Store the active ID in `sessionStorage`. Claim it with `BroadcastChannel`; on an occupied response, fork to a new key and copy the draft. Use a timestamped `localStorage` lease only when the channel API is missing, with an explicit short TTL, removal of expired leases before claiming, and release on page unload so an expired lease never blocks later recovery.

- [ ] **Step 6: Implement restore and submit synchronization**

Serialize fields, selected relations, UI state, attachment order, deletion set, new File blobs, server `updated_at`, the complete existing-attachment signature, and `form_version`. Before native submission, prevent once, await an immediate revision write, record `{key, revision}` in `sessionStorage`, then call `requestSubmit(originalSubmitter)` behind a reentry flag. During recovery, keep server-returned bound field and relation values, restore draft-only UI state and new Blobs, and restore old attachment operations only when both `form_version` and the stored baseline match the current server baseline.

On invalid response, restore Blob files through the attachment controller. On `?saved=1`, delete only an exact revision match and remove the success parameter with `history.replaceState`.

- [ ] **Step 7: Run browser tests and verify GREEN**

Run the command from Step 3 without `-k`. Expected: PASS.

- [ ] **Step 8: Commit**

```powershell
git -c safe.directory=C:/Users/14633/study-vault/.worktrees/math-question-bank add static/question_bank/js/question-form-drafts.js static/question_bank/js/question-form.js question_bank/templates/question_bank/question_form.html tests/test_question_form_browser.py
git -c safe.directory=C:/Users/14633/study-vault/.worktrees/math-question-bank commit -m "feat: recover question drafts with indexeddb"
```

## Task 11: Complete Conflict and Failure UX

**Files:**

* Modify: `question_bank/templates/question_bank/question_form.html`
* Modify: `static/question_bank/js/question-form.js`
* Modify: `static/question_bank/css/question-form.css`
* Modify: `tests/test_question_form_browser.py`
* Modify: `question_bank/tests/test_crud_views.py`

- [ ] **Step 1: Write failing conflict-flow tests**

Verify HTTP 409 renders the conflict banner, leaves the local draft intact, offers server reload and local-draft retention, and suppresses stale attachment deletion/reorder restoration after reloading the new baseline.

- [ ] **Step 2: Write failing failure-state browser tests**

Cover preview outage, storage quota failure, upload validation error with filename, failed native submission without JavaScript, and published-question “转为草稿” confirmation. For the no-JavaScript validation failure, assert the response explicitly tells the user that browsers do not retain file inputs and that new files must be selected again.

- [ ] **Step 3: Run focused tests and verify RED**

Run:

```powershell
& 'C:\Users\14633\anaconda3\python.exe' -m pytest question_bank/tests/test_crud_views.py tests/test_question_form_browser.py -q -k "conflict or failure or quota or publish" --basetemp .runtime/pytest-workbench-errors-red
```

- [ ] **Step 4: Implement the states without new data paths**

Use existing context flags and draft APIs. Keep failures local to their region, focus the conflict or validation summary, and preserve native submission as the final fallback. On every bound POST response where the request included one or more new uploads, render a server-side notice that new files must be selected again because browsers do not retain file inputs, even when the validation error is on a text or relation field. Add a browser test with a valid image plus an unrelated invalid field.

- [ ] **Step 5: Run focused tests and verify GREEN**

Run the command from Step 3. Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git -c safe.directory=C:/Users/14633/study-vault/.worktrees/math-question-bank add question_bank/templates/question_bank/question_form.html static/question_bank/js/question-form.js static/question_bank/css/question-form.css tests/test_question_form_browser.py question_bank/tests/test_crud_views.py
git -c safe.directory=C:/Users/14633/study-vault/.worktrees/math-question-bank commit -m "fix: complete question form recovery states"
```

## Task 12: Final Verification and Handoff

**Files:**

* Modify only when verification reveals a scoped defect.

- [ ] **Step 1: Run all automated tests**

```powershell
& 'C:\Users\14633\anaconda3\python.exe' -m pytest -q --basetemp .runtime/pytest-question-form-final
```

Expected: all tests pass, including Playwright tests when the development dependency and Chromium are installed.

- [ ] **Step 2: Run framework and static checks**

```powershell
& 'C:\Users\14633\anaconda3\python.exe' manage.py check
& 'C:\Users\14633\anaconda3\python.exe' manage.py makemigrations --check --dry-run
& 'C:\Users\14633\anaconda3\python.exe' manage.py collectstatic --noinput
node --check static/question_bank/js/question-form.js
node --check static/question_bank/js/question-form-editor.js
node --check static/question_bank/js/question-form-attachments.js
node --check static/question_bank/js/question-form-drafts.js
git -c safe.directory=C:/Users/14633/study-vault/.worktrees/math-question-bank diff --check
```

Expected: no Django issues, no model changes, successful static collection, valid JavaScript, and no whitespace errors.

- [ ] **Step 3: Run HTTP smoke checks**

Start Django on an available loopback port and verify HTTP 200 for create, edit, Markdown preview POST, an invalid form response, and a version-conflict response. Verify successful create and edit redirects.

- [ ] **Step 4: Run visual checks**

Use desktop, 980px, 760px, and 360px viewports. Check image containment, long formulas, long filenames, tab focus, error-summary focus, upload queue stability, no overlap, and reduced-motion behavior.

- [ ] **Step 5: Run a destructive-path sandbox check**

Against temporary `MEDIA_ROOT` and test database only, verify failed attachment persistence leaves no orphan, committed deletion removes the old file after commit, and the cleanup command dry run never deletes files.

- [ ] **Step 6: Review final diff**

Confirm changes are limited to the form workbench, attachment/version services, tests, development test dependency, and documentation. Inspect `git status --short` and the complete diff.

- [ ] **Step 7: Commit verification-only fixes when needed**

Use a narrow commit message describing the verified defect. Skip this step when no fixes were needed.
