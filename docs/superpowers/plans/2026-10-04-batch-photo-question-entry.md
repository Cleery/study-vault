# Batch Photo Question Entry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let one user upload many photos or screenshots, create one draft question per image, and organize each draft afterward.

**Architecture:** Reuse `Question`, `QuestionAttachment`, `Subject`, `Section`, and `Tag`. A new upload service persists one image and one draft per request with an idempotency UUID. A batch page renders saved drafts and provides narrow metadata update endpoints. The existing question editor remains the only place for full solutions and publishing.

**Tech Stack:** Django 5.2, SQLite, Django templates, vanilla JavaScript, pytest-django, Pillow, Nginx.

**Spec:** `docs/superpowers/specs/2026-10-04-batch-photo-question-entry-design.md`

---

## File Map

- `question_bank/models.py`, new migration: nullable batch UUID on `Question`, nullable unique upload UUID and nullable SHA-256 on `QuestionAttachment`.
- `question_bank/batch_entry.py`: one-image validation, hashing, idempotent draft creation, and file cleanup. No HTTP or rendering code.
- `question_bank/forms.py`: metadata-only validation and existing subject/section resolution shared with `QuestionForm` where practical.
- `question_bank/views.py`, `question_bank/urls.py`: upload, batch detail, per-item update, bulk update, and draft filter entry.
- `question_bank/templates/question_bank/batch_upload.html`, `batch_detail.html`, `question_list.html`: upload queue, editable batch, navigation.
- `static/question_bank/js/batch-upload.js`, `static/question_bank/css/batch-entry.css`: client-side one-file-per-request queue, duplicate warning, progress, responsive layout.
- `question_bank/search.py`: explicit draft-only filter without changing ordinary search.
- `deploy/nginx.conf`, `deploy/README.md`: 12 MB whole-request cap and deployment note.
- `question_bank/tests/test_batch_entry.py`, `tests/test_batch_entry_browser.py`, existing search/template tests: behavior and regression coverage.

## Task 1: Persist Upload Identity

**Files:** `question_bank/models.py`, `question_bank/migrations/0005_batch_photo_identity.py`, `question_bank/tests/test_models.py`.

- [ ] Write a failing model test: two attachments with the same non-null `client_upload_id` raise `IntegrityError`; many ordinary attachments with `NULL` identity remain valid; `Question.batch_id` can be null or UUID.
- [ ] Run `python -m pytest question_bank/tests/test_models.py -k batch_upload_identity -q --basetemp=.pytest-batch-model-red` and confirm the missing fields cause failure.
- [ ] Add `batch_id = models.UUIDField(null=True, blank=True, db_index=True)` to `Question`; add `client_upload_id = models.UUIDField(null=True, blank=True, unique=True)` and `content_sha256 = models.CharField(max_length=64, null=True, blank=True)` to `QuestionAttachment`.
- [ ] Create the migration with `python manage.py makemigrations question_bank`; inspect it to ensure existing rows remain untouched. Keep only the three new columns and indexes.
- [ ] Run the focused test, `python manage.py check`, and `python manage.py makemigrations --check --dry-run`.
- [ ] Commit only this task's files.

## Task 2: Idempotent One-Image Upload

**Files:** Create `question_bank/batch_entry.py`; modify `question_bank/views.py`, `question_bank/urls.py`; test `question_bank/tests/test_batch_entry.py`.

- [ ] Add tests for a valid PNG creating exactly one draft, one attachment and `batch_id`; invalid format/over 10 MB creating nothing; same upload UUID plus same bytes returning the existing question; same UUID plus different bytes returning HTTP 409; a storage or database failure leaving no orphan file or draft; and concurrent/retried uniqueness handling.
- [ ] Run `python -m pytest question_bank/tests/test_batch_entry.py -k upload -q --basetemp=.pytest-batch-upload-red`; verify failures are for missing behavior.
- [ ] Implement `create_batch_draft(*, image, batch_id, client_upload_id)` in `batch_entry.py`. Compute SHA-256 server-side in chunks and reset the file pointer. Use `validate_image_upload`, validate UUIDs in the view, and handle existing UUID before writing. Create `Question(draft=True, batch_id=batch_id)` and `QuestionAttachment(file_kind="image", sort_order=0, client_upload_id=..., content_sha256=...)` inside `transaction.atomic()`. Track stored file names and call `cleanup_unreferenced_files` on exceptions. On unique-key race, re-read the winning attachment and compare hashes; never overwrite its file.
- [ ] Add `POST /questions/batch/upload/` returning JSON `{question_id, batch_id, created}` for fetch requests. A successful native single-image POST redirects back to the upload page with a saved-result banner containing “继续添加” and “整理本批次” links. Return field-specific 400 errors for invalid images/UUIDs and 409 for UUID-content conflict. Preserve CSRF protection and Basic Auth coverage.
- [ ] Run focused tests and `python manage.py check`; inspect that no browser-provided SHA-256 is trusted without server recomputation.
- [ ] Commit only this task's files.

## Task 3: Batch Selection and Native Fallback

**Files:** Create `question_bank/templates/question_bank/batch_upload.html`, `static/question_bank/js/batch-upload.js`, `static/question_bank/css/batch-entry.css`; modify `question_bank/views.py`, `question_bank/urls.py`; test `question_bank/tests/test_batch_entry.py`, `tests/test_batch_entry_browser.py`.

- [ ] Add a Django test for `GET /questions/batch/new/` showing a native one-image form, CSRF token, allowed formats, 10 MB file limit, and link back to question search. Add browser tests for multi-select preview, removing a queued image, same-content duplicate warning, sequential upload requests, individual progress/failure, and retry preserving the same upload UUID.
- [ ] Run focused tests and confirm page/JS behavior is absent.
- [ ] Implement the page with `input[type=file][accept="image/png,image/jpeg,image/webp"][multiple]`, a native `name="image"` form usable without JavaScript, and a visible queue enhanced by JS. Generate one `batch_id` for the selection session and one `client_upload_id` per queue item; use `crypto.randomUUID()` with a fallback if unavailable. Keep the UUID stable across retries. Compare files by SHA-256 within the current queue and ask before accepting exact duplicates.
- [ ] Upload files sequentially as separate `FormData` requests with CSRF token. Limit client-side size to 10 MB, map HTTP 413 to the specified request-size message, and display server validation errors per item. When failures remain, stay on the upload page with successful items marked saved and failed items ready for retry; offer an explicit “整理已上传题目” link. Navigate to `/questions/batch/<batch_id>/` only after every item succeeds or the user chooses that link. On reload, existing successful items are discoverable through the batch URL or the draft filter; do not claim failed items were saved.
- [ ] For native submission, remove `multiple` with a no-JS-compatible form path and submit exactly one file. Return a result page with “继续添加” and “整理本批次” links. Keep the overall request below 12 MB.
- [ ] Run Django and browser tests. If Playwright is unavailable, report the gap and perform manual screenshot checks at desktop and mobile sizes when a browser is available.
- [ ] Commit only this task's files.

## Task 4: Batch Detail and Per-Question Metadata

**Files:** Create `question_bank/templates/question_bank/batch_detail.html`; modify `question_bank/forms.py`, `question_bank/views.py`, `question_bank/urls.py`, `static/question_bank/css/batch-entry.css`; test `question_bank/tests/test_batch_entry.py`.

- [ ] Write tests: batch page shows only that batch's saved, undeleted questions and images; empty metadata can be saved; new subject/section are created and section belongs to subject; existing subject/section are reused; invalid section-without-subject or archived/redirected tag returns errors; edit of one card does not alter another card; published or deleted question cannot be changed from this page.
- [ ] Run focused tests and confirm expected failures.
- [ ] Add `QuestionMetadataForm` with `subject` and `section` text fields plus active canonical tag IDs. Resolve names using the existing `QuestionForm` rules, or extract a small shared resolver while keeping current `QuestionForm` tests green. Keep the update atomic; no subject or section row is left behind when the form or question save fails. Do not submit or reset title, statement, solutions, mastery, attachments, or knowledge cards.
- [ ] Add `GET /questions/batch/<uuid:batch_id>/` and `POST /questions/batch/<uuid:batch_id>/<uuid:question_id>/metadata/`. Require `batch_id` match, `draft=True`, `archived=False`, `deleted_at IS NULL`. Render one unframed row per image with separate subject, section, tags, save status, and link to existing full editor. Use compact controls on mobile.
- [ ] Run focused tests and existing `question_bank/tests/test_crud_views.py`.
- [ ] Commit only this task's files.

## Task 5: Bulk Metadata Apply

**Files:** Modify `question_bank/views.py`, `question_bank/urls.py`, `question_bank/templates/question_bank/batch_detail.html`; test `question_bank/tests/test_batch_entry.py`.

- [ ] Write tests for selected draft IDs only, foreign-batch IDs rejected, published/deleted IDs rejected, subject/section update, invalid section-without-subject, tag-add preserving existing tags, no selected field leaving all data unchanged, and invalid data rolling back the whole operation.
- [ ] Run focused tests and confirm the bulk endpoint is missing.
- [ ] Add `POST /questions/batch/<uuid:batch_id>/apply/` with explicit field toggles such as `apply_subject`, `apply_section`, `add_tags`; parse selected UUIDs, lock those questions, validate all inputs, then update within one transaction. A field is unchanged unless its toggle is set. Subject change with section unchanged must clear any section belonging to a different subject, consistent with the single-question editor. Add only active canonical tags; do not replace or remove existing tags.
- [ ] Render checkboxes and compact bulk controls at the top of the batch page. Show a count of updated questions or a field-level error; preserve selections on invalid POST.
- [ ] Run focused tests and existing tag/search tests.
- [ ] Commit only this task's files.

## Task 6: Find Drafts Again

**Files:** Modify `question_bank/search.py`, `question_bank/views.py`, `question_bank/templates/question_bank/question_list.html`; test `question_bank/tests/test_search_and_tags.py`, `tests/test_templates.py`.

- [ ] Add tests for `?draft=1` showing only undeleted, unarchived drafts, ordinary search remaining unchanged, pagination preserving `draft=1`, and a visible “待整理草稿” link plus “批量录入” link on the search page.
- [ ] Run focused tests and confirm the missing filter/UI.
- [ ] In `build_question_queryset`, apply `draft=True` only when the explicit draft parameter is active. Preserve the parameter in active filter labels and pagination. Add an edit action for draft search results, so image-only drafts can be organized after leaving the batch page.
- [ ] Run focused tests and all search tests.
- [ ] Commit only this task's files.

## Task 7: Production Limit, Regression and Release Readiness

**Files:** Modify `deploy/nginx.conf`, `deploy/README.md`; test `tests/test_deployment.py`, `question_bank/tests/test_batch_entry.py`.

- [ ] Add a deployment test that checks the packaged Nginx config has `client_max_body_size 12M` and a 413 response path/message. Confirm service-side 10 MB image validation still applies.
- [ ] Run focused deployment tests and observe the current 10M setting fail.
- [ ] Update the Nginx template and deployment instructions. Record that the live Tailscale Nginx site may use a separate config and must be changed explicitly at deployment. Do not widen unrelated endpoints beyond this site.
- [ ] Run `python manage.py makemigrations --check --dry-run`, `python manage.py check`, `node --check static/question_bank/js/batch-upload.js`, all non-browser pytest tests with a fresh project-local `--basetemp`, and browser tests if Playwright is available. Confirm no errors from `git diff --check`.
- [ ] Review exact changed files and request code review. Resolve findings, then rerun verification.
- [ ] Commit the deployment files. Do not push or deploy until the user explicitly requests that release step.
