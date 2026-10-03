# Create Form Cancel Links Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ensure canceling an unsaved knowledge card or question returns to its list, while editing a saved record returns to its detail page.

**Architecture:** Django UUID models receive primary keys before database insertion. Template navigation must use the view's explicit create or edit context instead of primary key presence. Cover both card cancel links, the question cancel link, and question create-only button text.

**Tech Stack:** Django templates, pytest-django, Git, Ubuntu deployment.

---

### Task 1: Regression tests and repair

**Files:** `question_bank/tests/test_crud_views.py`, `question_bank/templates/question_bank/knowledge_card_form.html`, `question_bank/templates/question_bank/question_form.html`.

- [ ] Add tests for both cancel links and mode label on new card GET and invalid POST, edit cancel destination, new question cancel destination and button text, edit question cancel destination.
- [ ] Run tests and observe expected failures.
- [ ] Replace primary-key truth checks used for create/edit navigation with an explicit saved-state check.
- [ ] Run focused tests, full suite, Django checks, and diff check.
- [ ] Commit only planned source and test files, then push to GitHub main.

### Task 2: Deploy

- [ ] Confirm live version and persistent database location, then fetch or clone the new release.
- [ ] Verify server environment and take a validated database snapshot before switching.
- [ ] Switch current to the new release, run migrations and static collection, restart Gunicorn.
- [ ] Verify served commit, health endpoint, Nginx authorization response, and logs. Preserve previous release for rollback.
