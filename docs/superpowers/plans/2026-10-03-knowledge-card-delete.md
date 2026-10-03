# Knowledge Card Delete Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the user delete a knowledge card after explicit confirmation while preserving questions and other cards, then publish the change.

**Architecture:** Add a dedicated GET/POST delete route and confirmation template. GET renders impact counts without mutation. POST deletes the card in one database transaction; Django cascades through-table rows only. No model or database migration is required.

**Tech Stack:** Django 5.2, pytest-django, Git, Ubuntu 22.04 with Gunicorn and Nginx.

---

### Task 1: Deletion behavior and UI

**Files:** Modify `question_bank/tests/test_crud_views.py`, `question_bank/urls.py`, `question_bank/views.py`, `question_bank/templates/question_bank/knowledge_card_detail.html`; create `question_bank/templates/question_bank/knowledge_card_confirm_delete.html`.

- [ ] Add failing tests for detail-page entry, read-only confirmation GET showing card name, associated question count, and prerequisite relation count; POST deletion, list redirect and success message; preserved related records with removed links; 404 for missing cards; and CSRF.
- [ ] Run focused tests and confirm failure is caused by the missing feature.
- [ ] Add the route, view, and two template changes with minimal code.
- [ ] Run focused tests until passing, then run the full suite, Django checks, and `git diff --check`.
- [ ] Review changed files, commit only feature files and this plan, and push `HEAD` to GitHub `main` without including pytest artifacts.

### Task 2: Server deployment

**Files:** No tracked file changes. Server paths `/srv/math-question-bank/releases`, `/srv/math-question-bank/current`, `/srv/math-question-bank/shared`.

- [ ] Verify GitHub main matches the local commit and inspect current server version, service, persistent database, and Nginx state.
- [ ] Clone or fetch the new commit into a separate release directory. Check dependencies and Django configuration before switch.
- [ ] Create and validate a SQLite snapshot under `/var/backups/math-question-bank` and retain the previous release.
- [ ] Stop Gunicorn briefly, switch `current`, run migrations and static collection, then start Gunicorn.
- [ ] Verify active services, served commit, migration state, application health, Nginx authentication response, and logs. If release verification fails, restore the previous code and service.
