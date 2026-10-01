# Math Question Bank Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a single-user Django question bank for mathematical analysis and advanced algebra with image/LaTeX entry, searchable tags, theorem knowledge cards, review history, and an Ubuntu 22.04 deployment path.

**Architecture:** A Django monolith serves HTML templates and JSON endpoints where useful. SQLite is the initial database, local media stores uploaded images, and MathJax renders LaTeX. Nginx Basic Auth protects the deployed single-user instance; the application keeps domain logic in models/forms/services so later PostgreSQL or account support remains possible.

**Tech Stack:** Python 3, Django, SQLite, pytest/pytest-django, Django templates, vanilla JavaScript, MathJax, Gunicorn, Nginx, systemd, Certbot.

---

## Task 1: Bootstrap the Django project

**Files:**
- Create: `manage.py`
- Create: `config/settings.py`, `config/urls.py`, `config/wsgi.py`, `config/asgi.py`
- Create: `config/__init__.py`, `question_bank/__init__.py`
- Create: `question_bank/apps.py`
- Create: `pytest.ini`
- Create: `requirements.txt`
- Create: `.env.example`
- Create: `.gitignore`
- Create: `question_bank/templates/question_bank/base.html`
- Test: `question_bank/tests/test_bootstrap.py`

- [ ] **Step 1: Write the failing bootstrap test**

Verify Django can load settings and the health endpoint returns HTTP 200.

- [ ] **Step 2: Run the test and confirm it fails**

Run: `python -m pytest question_bank/tests/test_bootstrap.py -q`
Expected: collection or import failure because the project does not exist.

- [ ] **Step 3: Create the project and minimal health endpoint**

Pin Django, pytest, pytest-django, Pillow for image validation, markdown for Markdown parsing, bleach for Markdown sanitization, Gunicorn, and python-dotenv in `requirements.txt`. Configure environment-based `SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS`, `TIME_ZONE=Asia/Shanghai`, SQLite, static files, media files, CSRF settings, and an `/health/` route.

- [ ] **Step 4: Run the test and confirm it passes**

Run: `python -m pytest question_bank/tests/test_bootstrap.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

Run: `git add . && git commit -m "chore: bootstrap django project"`.

## Task 2: Add the domain models and migrations

**Files:**
- Create: `question_bank/models.py`
- Create: `question_bank/migrations/__init__.py`
- Create: `question_bank/management/__init__.py`, `question_bank/management/commands/__init__.py`
- Create: `question_bank/management/commands/seed_system_data.py`
- Test: `question_bank/tests/test_models.py`

- [ ] **Step 1: Write model tests**

Cover Question, QuestionAttachment, KnowledgeCard, Tag, ReviewRecord, formal-question validation requiring at least one of subject/title/statement, draft bypass behavior, KnowledgeCard requiring name/subject/type, unique tag names within a parent, many-to-many uniqueness, soft deletion, attachment ordering, knowledge-card prerequisite self-reference/cycle validation, and idempotent system-data seeding.

- [ ] **Step 2: Run tests and confirm they fail**

Run: `python -m pytest question_bank/tests/test_models.py -q`
Expected: model import or table errors.

- [ ] **Step 3: Implement models**

Add stable UUIDs, timestamps, draft/archive flags, model validation for the required field combinations with a draft bypass, structured subject/section fields, LaTeX and Markdown text, a QuestionAttachment model for multiple images, mastery choices, parent tags, tag redirect support, complete KnowledgeCard fields (type, formal statement, conditions, proof, usage signals, common mistakes, personal notes), prerequisite relationships, ReviewRecord fields (`reviewed_at`, `result`, `mastery_before`, `mastery_after`, `duration_seconds`, `note`, `next_review_at`), indexes for search/filter fields, and deletion behavior.

Implement `seed_system_data` as an idempotent command for subjects, common sections, and error-type candidates.

- [ ] **Step 4: Generate and apply migrations**

Run: `python manage.py makemigrations && python manage.py migrate && python manage.py seed_system_data`.
Expected: migration completes without errors.

- [ ] **Step 5: Run model tests and commit**

Run: `python -m pytest question_bank/tests/test_models.py -q`
Expected: PASS.

Run: `git add . && git commit -m "feat: add question bank domain models"`.

## Task 3: Implement safe Markdown, LaTeX, and image handling

**Files:**
- Create: `question_bank/markdown.py`
- Create: `question_bank/validators.py`
- Modify: `question_bank/models.py`
- Test: `question_bank/tests/test_content_safety.py`

- [ ] **Step 1: Write failing safety tests**

Test allowed PNG/JPEG/WebP files, rejection of oversized or invalid files, removal of raw HTML/scripts, and preservation of MathJax LaTeX.

- [ ] **Step 2: Run tests and confirm failure**

Run: `python -m pytest question_bank/tests/test_content_safety.py -q`
Expected: FAIL because validators and sanitizer are absent.

- [ ] **Step 3: Implement validation and sanitization**

Validate MIME and extension, cap uploads at 10 MB, generate random media names, sanitize Markdown to a safe subset, and configure MathJax-compatible output.

- [ ] **Step 4: Run tests and commit**

Run: `python -m pytest question_bank/tests/test_content_safety.py -q`
Expected: PASS.

Run: `git add . && git commit -m "feat: validate uploads and sanitize math content"`.

## Task 4: Build question and knowledge-card CRUD

**Files:**
- Create: `question_bank/forms.py`
- Create: `question_bank/views.py`
- Create: `question_bank/urls.py`
- Create: `question_bank/templates/question_bank/question_form.html`
- Create: `question_bank/templates/question_bank/question_detail.html`
- Create: `question_bank/templates/question_bank/knowledge_card_form.html`
- Create: `question_bank/templates/question_bank/knowledge_card_list.html`
- Create: `question_bank/templates/question_bank/knowledge_card_detail.html`
- Test: `question_bank/tests/test_crud_views.py`

- [ ] **Step 1: Write view tests**

Cover draft creation, formal-question validation, multiple image attachments with ordering, LaTeX input, editing, detail rendering, knowledge-card list filtering, knowledge-card creation, all knowledge-card fields, question/card linking, and invalid form responses.

- [ ] **Step 2: Run tests and confirm failure**

Run: `python -m pytest question_bank/tests/test_crud_views.py -q`
Expected: URL or template failures.

- [ ] **Step 3: Implement forms, views, URLs, and templates**

Keep question and knowledge-card forms separate. Render sanitized Markdown and MathJax fields, accept multiple image attachments with stable ordering, show structured subject/section fields and linked tags/cards, and expose archive actions with confirmation.

- [ ] **Step 4: Run tests and commit**

Run: `python -m pytest question_bank/tests/test_crud_views.py -q`
Expected: PASS.

Run: `git add . && git commit -m "feat: add question and knowledge card management"`.

## Task 5: Add tag management and search

**Files:**
- Create: `question_bank/search.py`
- Modify: `question_bank/forms.py`, `question_bank/views.py`, `question_bank/urls.py`
- Create: `question_bank/templates/question_bank/question_list.html`
- Create: `question_bank/templates/question_bank/tag_manage.html`
- Test: `question_bank/tests/test_search_and_tags.py`

- [ ] **Step 1: Write failing search/tag tests**

Cover keyword matching across title, statement, solutions, AND filters, OR values within one field, pagination, stable sorting, tag rename, merge redirect compression, archive behavior, exclusion of archived tags from new-question default candidates while retaining historical results, and saved-filter browser configuration.

- [ ] **Step 2: Run tests and confirm failure**

Run: `python -m pytest question_bank/tests/test_search_and_tags.py -q`
Expected: FAIL because search and tag operations are missing.

- [ ] **Step 3: Implement query builder and tag operations**

Use parameterized Django ORM filters, 20-item pages, updated-time ordering with UUID tie-breaker, tag redirect resolution, structured subject/section and parent-tag filtering, and JSON serialization for saved filters stored in browser local storage.

- [ ] **Step 4: Build the search homepage**

Add sidebar filters, global search, active-filter chips, result cards, empty states, and links to knowledge-card reverse results.

- [ ] **Step 5: Run tests and commit**

Run: `python -m pytest question_bank/tests/test_search_and_tags.py -q`
Expected: PASS.

Run: `git add . && git commit -m "feat: add searchable question index and tag management"`.

## Task 6: Implement review state transitions and due lists

**Files:**
- Create: `question_bank/review.py`
- Modify: `question_bank/views.py`, `question_bank/urls.py`
- Create: `question_bank/templates/question_bank/review_list.html`
- Create: `question_bank/templates/question_bank/review_detail.html`
- Test: `question_bank/tests/test_review.py`

- [ ] **Step 1: Write exhaustive transition tests**

Cover every current state and result combination, first review, interval calculation, skipped reviews, due-date boundaries in Asia/Shanghai, recent mistakes, overdue ordering, and duplicate prevention.

- [ ] **Step 2: Run tests and confirm failure**

Run: `python -m pytest question_bank/tests/test_review.py -q`
Expected: FAIL because review services are absent.

- [ ] **Step 3: Implement the review service**

Use the approved transition table and result-based intervals of 1, 2, 7, and 30 days. Save before/after mastery and the next review date atomically with ReviewRecord.

- [ ] **Step 4: Implement review views**

Create due-today, recent-mistake, and knowledge-card review queues. Hide reference solutions until the user expands them, then submit one record per question.

- [ ] **Step 5: Run tests and commit**

Run: `python -m pytest question_bank/tests/test_review.py -q`
Expected: PASS.

Run: `git add . && git commit -m "feat: add review history and due queues"`.

## Task 7: Add the visual system and responsive interaction layer

**Files:**
- Create: `question_bank/static/question_bank/css/app.css`
- Create: `question_bank/static/question_bank/js/app.js`
- Create: `question_bank/static/question_bank/assets/character-placeholder.svg`
- Test: `question_bank/tests/test_templates.py`

- [ ] **Step 1: Write template smoke tests**

Verify navigation, search controls, MathJax loading, keyboard labels, mobile viewport structure, and collapsible assistant panel.

- [ ] **Step 2: Implement the anime study-desk visual language**

Use CSS variables for theme colors, spacing, typography, compact information cards, accessible focus states, reduced-motion support, and a collapsible character panel that never overlays core content.

- [ ] **Step 3: Add progressive enhancement**

Use vanilla JavaScript for active-filter removal, image preview, solution reveal, local saved filters, and confirmation dialogs. Keep all core workflows functional without JavaScript.

- [ ] **Step 4: Run tests and commit**

Run: `python -m pytest question_bank/tests/test_templates.py -q`
Expected: PASS.

Run: `git add . && git commit -m "feat: add study desk interface"`.

## Task 8: Add statistics, export/import, and backups

**Files:**
- Create: `question_bank/stats.py`
- Create: `question_bank/exporting.py`
- Create: `question_bank/management/commands/export_bundle.py`
- Create: `question_bank/management/commands/import_bundle.py`
- Create: `question_bank/management/commands/backup_bundle.py`
- Create: `deploy/restore-backup.sh`
- Create: `question_bank/templates/question_bank/stats.html`
- Test: `question_bank/tests/test_export_stats.py`

- [ ] **Step 1: Write tests for fixed statistics and round-trip import**

Cover the six defined metrics, empty state, export relationships and attachments, import ID mapping, duplicate update, invalid bundle rollback, and backup checksum manifest.

- [ ] **Step 2: Implement statistics and bundle schema**

Use a versioned JSON manifest, relative attachment paths, stable IDs, transaction-wrapped import, and deterministic metric queries.

- [ ] **Step 3: Implement backup command**

Create a write pause, SQLite snapshot, media copy, checksum manifest, encrypted archive using the `age` CLI and `BACKUP_AGE_PUBLIC_KEY`, retention cleanup, copy to `BACKUP_OFFLINE_PATH`, recovery manifest, and explicit failure exit codes. The Ubuntu-native `deploy/restore-backup.sh` drill uses `age` with a root-readable `BACKUP_AGE_IDENTITY_FILE` private key, then checksums, database integrity, and media relationships. Document installation of `age` and the monthly recovery command.

- [ ] **Step 4: Run tests and commit**

Run: `python -m pytest question_bank/tests/test_export_stats.py -q`
Expected: PASS.

- [ ] **Step 5: Test attachment cleanup and recovery**

Verify deleting or replacing a question removes only unreferenced media, and restore a generated backup into a fresh directory/database while checking the checksum manifest. Include failure cases for a missing encryption key, failed offline copy, and checksum mismatch.

- [ ] **Step 6: Commit**

Run: `git add . && git commit -m "feat: add statistics export import and backups"`.

## Task 9: Prepare Ubuntu 22.04 deployment

**Files:**
- Create: `deploy/gunicorn.service`
- Create: `deploy/nginx.conf`
- Create: `deploy/check-production-config.py`
- Create: `deploy/backup.timer`, `deploy/backup.service`
- Create: `deploy/README.md`
- Modify: `.env.example`, `requirements.txt`

- [ ] **Step 1: Document deployment variables and directories**

Document dedicated systemd user, project/media/static/backup paths, Basic Auth setup, `DEV_AUTH_BYPASS` prohibition in production, HTTPS, logs, and permissions.

- [ ] **Step 2: Add service and Nginx configurations**

Configure one Gunicorn worker for SQLite, Nginx static/media routing, Basic Auth on every application and media location, upload limit, HTTPS placeholders, and log rotation expectations. Add a production startup check that rejects `DEV_AUTH_BYPASS=true`.

`deploy/check-production-config.py` must fail with a non-zero exit code when `DEBUG=true`, `DEV_AUTH_BYPASS=true`, a missing `BACKUP_AGE_PUBLIC_KEY`, or a missing `BACKUP_OFFLINE_PATH` is detected. Reference it from `ExecStartPre` in `deploy/gunicorn.service`.

- [ ] **Step 3: Validate production configuration locally**

Run: `python manage.py check --deploy`, `python manage.py collectstatic --noinput`, `nginx -t`, and `python deploy/check-production-config.py` with production-like environment variables.
Expected: deployment checks complete with only explicitly documented host/certificate values pending.

Then verify `systemctl status math-question-bank` and `curl http://127.0.0.1/health/` after installing the service on Ubuntu.

- [ ] **Step 4: Commit deployment artifacts**

Run: `git add deploy .env.example requirements.txt && git commit -m "ops: add ubuntu deployment configuration"`.

## Task 10: Full verification and handoff

**Files:**
- Modify: `README.md`
- Test: all `question_bank/tests/`

- [ ] **Step 1: Run the complete test suite**

Run: `python -m pytest -q`
Expected: all tests pass.

- [ ] **Step 2: Run Django checks and collect static assets**

Run: `python manage.py check --deploy && python manage.py collectstatic --noinput`
Expected: no blocking errors.

- [ ] **Step 3: Perform a manual smoke test**

Create a math analysis question with an image, LaTeX, tags, and two knowledge cards. Browse the knowledge-card list, search the question, complete two review outcomes, merge a child tag, export it, import it into a fresh database, and verify relationships, media cleanup, backup recovery, and Basic Auth protection.

- [ ] **Step 4: Update README with local and Ubuntu commands**

Document environment setup, migrations, development server, test command, export/backup commands, and deployment sequence.

- [ ] **Step 5: Commit the verified handoff**

Run: `git add . && git commit -m "docs: document local setup and deployment handoff"`.
