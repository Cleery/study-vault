import pytest
from django.urls import reverse

from question_bank.models import KnowledgeCard, Question, QuestionAttachment, Section, Subject, Tag


playwright = pytest.importorskip("playwright.sync_api")


@pytest.mark.browser
@pytest.mark.django_db(transaction=True)
def test_new_question_form_loads_and_saves_draft_without_javascript(live_server, settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path / "media"

    with playwright.sync_playwright() as playwright_instance:
        browser = playwright_instance.chromium.launch()
        context = browser.new_context(java_script_enabled=False)
        try:
            page = context.new_page()
            page.goto(f"{live_server.url}{reverse('question-create')}")

            assert page.title() == "新建题目 | 数学题库"
            assert page.get_by_role("heading", name="新建题目").is_visible()
            assert page.locator('[data-tab-panel="solution"]').is_visible()
            assert page.locator('[data-tab-panel="relations"]').is_visible()

            page.get_by_label("标题").fill("浏览器草稿")
            page.get_by_role("button", name="保存草稿").click()

            question = Question.objects.get(title="浏览器草稿")
            assert question.draft is True
            assert page.url == f"{live_server.url}{reverse('question-edit', args=[question.pk])}?saved=1"
        finally:
            context.close()
            browser.close()


@pytest.mark.browser
@pytest.mark.django_db(transaction=True)
def test_editor_modes_tabs_and_formatting(live_server):
    with playwright.sync_playwright() as instance:
        browser = instance.chromium.launch()
        try:
            page = browser.new_page()
            page.goto(f"{live_server.url}{reverse('question-create')}")
            form = page.locator("[data-question-form]")
            assert form.get_attribute("data-mode") == "quick"
            statement = page.locator("#id_statement")
            statement.fill("题干")
            page.locator('[data-form-mode="complete"]').click()
            assert form.get_attribute("data-mode") == "complete"
            assert statement.input_value() == "题干"
            tab = page.get_by_role("tab", name="题目")
            tab.focus()
            tab.press("End")
            assert page.get_by_role("tab", name="关联").get_attribute("aria-selected") == "true"
            page.get_by_role("tab", name="关联").press("Home")
            assert tab.get_attribute("aria-selected") == "true"
            statement.fill("x")
            statement.focus()
            statement.evaluate("el => el.setSelectionRange(0, 1)")
            page.locator('[data-insert="bold"]').click()
            assert statement.input_value() == "**x**"
        finally:
            browser.close()


@pytest.mark.browser
@pytest.mark.django_db(transaction=True)
def test_editor_preview_uses_csrf_and_preserves_html_on_failure(live_server):
    with playwright.sync_playwright() as instance:
        browser = instance.chromium.launch()
        try:
            page = browser.new_page()
            requests = []

            def preview(route):
                request = route.request
                requests.append((request.header_value("x-csrftoken"), request.post_data))
                if len(requests) == 1:
                    route.fulfill(status=200, content_type="application/json", body='{"html":"<strong>ok</strong>"}')
                else:
                    route.abort()

            page.route("**/markdown-preview/", preview)
            page.goto(f"{live_server.url}{reverse('question-create')}")
            field = page.locator("#id_statement")
            field.fill("first")
            page.locator("[data-markdown-preview] strong").wait_for()
            field.fill("second")
            page.wait_for_function("() => document.querySelector('[data-preview-status]').textContent.includes('失败')")
            assert page.locator("[data-markdown-preview]").inner_html() == "<strong>ok</strong>"
            assert requests[0][0]
            assert "source=first" in requests[0][1]
        finally:
            browser.close()


@pytest.mark.browser
@pytest.mark.django_db(transaction=True)
def test_attachment_queue_reorders_and_serializes_new_files(live_server):
    with playwright.sync_playwright() as instance:
        browser = instance.chromium.launch()
        try:
            page = browser.new_page()
            page.goto(f"{live_server.url}{reverse('question-create')}")
            page.locator("#id_attachments").set_input_files([
                {"name": "a.png", "mimeType": "image/png", "buffer": b"a"},
                {"name": "b.png", "mimeType": "image/png", "buffer": b"b"},
            ])
            rows = page.locator("[data-attachment-queue] > li")
            assert rows.count() == 2
            assert page.locator('input[name="attachment_order"]').evaluate_all(
                "nodes => nodes.map(node => node.value)"
            ) == ["new:0", "new:1"]
            rows.nth(1).locator('[data-attachment-move="up"]').click()
            assert page.locator('input[name="attachment_order"]').evaluate_all(
                "nodes => nodes.map(node => node.value)"
            ) == ["new:0", "new:1"]
            assert page.locator("#id_attachments").evaluate("el => [...el.files].map(file => file.name)") == ["b.png", "a.png"]
            rows.first.locator('[data-attachment-remove]').click()
            assert page.locator("#id_attachments").evaluate("el => [...el.files].map(file => file.name)") == ["a.png"]
        finally:
            browser.close()


@pytest.mark.browser
@pytest.mark.django_db(transaction=True)
def test_attachment_drag_handle_reorders_queue(live_server):
    with playwright.sync_playwright() as instance:
        browser = instance.chromium.launch()
        try:
            page = browser.new_page()
            page.goto(f"{live_server.url}{reverse('question-create')}")
            page.locator("#id_attachments").set_input_files([
                {"name": "a.png", "mimeType": "image/png", "buffer": b"a"},
                {"name": "b.png", "mimeType": "image/png", "buffer": b"b"},
            ])
            rows = page.locator("[data-attachment-queue] > li")
            rows.first.locator("[data-attachment-drag-handle]").drag_to(rows.nth(1))
            assert page.locator('input[name="attachment_order"]').evaluate_all(
                "nodes => nodes.map(node => node.value)"
            ) == ["new:0", "new:1"]
            assert page.locator("#id_attachments").evaluate("el => [...el.files].map(file => file.name)") == ["b.png", "a.png"]
        finally:
            browser.close()


@pytest.mark.browser
@pytest.mark.django_db(transaction=True)
def test_attachment_reorder_skips_deleted_items(live_server):
    with playwright.sync_playwright() as instance:
        browser = instance.chromium.launch()
        try:
            page = browser.new_page()
            page.goto(f"{live_server.url}{reverse('question-create')}")
            page.locator("#id_attachments").set_input_files([
                {"name": name, "mimeType": "image/png", "buffer": b"a"}
                for name in ("a.png", "b.png", "c.png")
            ])
            rows = page.locator("[data-attachment-queue] > li")
            rows.nth(1).locator("[data-attachment-remove]").click()
            rows.nth(1).locator('[data-attachment-move="up"]').click()
            assert page.locator('input[name="attachment_order"]').evaluate_all(
                "nodes => nodes.map(node => node.value)"
            ) == ["new:0", "new:1"]
            assert page.locator("#id_attachments").evaluate("el => [...el.files].map(file => file.name)") == ["c.png", "a.png"]
        finally:
            browser.close()


@pytest.mark.browser
@pytest.mark.django_db(transaction=True)
def test_attachment_removal_keeps_keyboard_focus_in_queue_or_upload(live_server):
    with playwright.sync_playwright() as instance:
        browser = instance.chromium.launch()
        try:
            page = browser.new_page()
            page.goto(f"{live_server.url}{reverse('question-create')}")
            page.locator('#id_attachments').set_input_files([
                {"name": name, "mimeType": "image/png", "buffer": b"a"}
                for name in ("a.png", "b.png", "c.png")
            ])
            rows = page.locator('[data-attachment-queue] > li')
            rows.nth(1).locator('[data-attachment-remove]').click()
            assert rows.nth(1).locator('[data-attachment-remove]').evaluate(
                "el => document.activeElement === el"
            )
            rows.nth(1).locator('[data-attachment-remove]').click()
            assert rows.first.locator('[data-attachment-remove]').evaluate(
                "el => document.activeElement === el"
            )
            rows.first.locator('[data-attachment-remove]').click()
            assert page.locator('#id_attachments').evaluate("el => document.activeElement === el")
        finally:
            browser.close()


@pytest.mark.browser
@pytest.mark.django_db(transaction=True)
def test_editor_accepts_custom_meta_fields_and_searches_multiselects(live_server):
    analysis = Subject.objects.create(name="分析")
    algebra = Subject.objects.create(name="代数")
    first = Section.objects.create(subject=analysis, name="极限")
    second = Section.objects.create(subject=algebra, name="矩阵")
    selected_tag = Tag.objects.create(name="导数")
    Tag.objects.create(name="积分")
    card = KnowledgeCard.objects.create(name="链式法则", subject=analysis, type="theorem")
    with playwright.sync_playwright() as instance:
        browser = instance.chromium.launch()
        try:
            page = browser.new_page()
            page.goto(f"{live_server.url}{reverse('question-create')}")
            page.locator('[data-form-mode="complete"]').click()
            page.locator("#id_subject").fill(analysis.name)
            page.locator("#id_section").fill(first.name)
            page.locator("#id_subject").fill(algebra.name)
            assert page.locator("#id_section").input_value() == ""
            page.get_by_role("tab", name="关联").click()
            page.locator("#id_tags").select_option(str(selected_tag.pk))
            page.locator('[data-relation-search="tags"]').fill("积分")
            assert page.locator(f'#id_tags option[value="{selected_tag.pk}"]').evaluate("el => !el.hidden && el.selected")
            page.locator("#id_knowledge_cards").select_option(str(card.pk))
            page.locator('[data-relation-search="knowledge_cards"]').fill("无匹配")
            assert page.locator(f'#id_knowledge_cards option[value="{card.pk}"]').evaluate("el => !el.hidden && el.selected")
        finally:
            browser.close()


@pytest.mark.browser
@pytest.mark.django_db(transaction=True)
def test_existing_attachment_kinds_zoom_and_removal(live_server):
    subject = Subject.objects.create(name="数学")
    question = Question.objects.create(subject=subject, title="附件题")
    image = QuestionAttachment.objects.create(question=question, file="questions/figure.png", file_kind="image", sort_order=0)
    document = QuestionAttachment.objects.create(question=question, file="questions/notes.pdf", file_kind="document", sort_order=1)
    other = QuestionAttachment.objects.create(question=question, file="questions/data.bin", file_kind="other", sort_order=2)
    with playwright.sync_playwright() as instance:
        browser = instance.chromium.launch()
        try:
            page = browser.new_page()
            page.goto(f"{live_server.url}{reverse('question-edit', args=[question.pk])}")
            for attachment in (image, document, other):
                assert page.locator(f'[data-attachment-id="{attachment.pk}"]').count() == 1
            page.locator(f'[data-attachment-id="{image.pk}"] [data-image-zoom]').click()
            assert page.locator('[data-image-dialog]').evaluate("el => el.open")
            page.locator('[data-image-dialog-close]').click()
            assert not page.locator('[data-image-dialog]').evaluate("el => el.open")
            page.locator(f'[data-attachment-id="{document.pk}"] [data-attachment-remove]').click()
            assert page.locator('input[name="removed_attachment"]').evaluate_all(
                "nodes => nodes.map(node => node.value)"
            ) == [str(document.pk)]
            assert page.locator('[data-ocr]').is_disabled()
        finally:
            browser.close()


@pytest.mark.browser
@pytest.mark.django_db(transaction=True)
def test_section_error_opens_complete_mode_and_summary_focuses_field(live_server, client):
    first = Subject.objects.create(name="分析")
    second = Subject.objects.create(name="代数")
    wrong_section = Section.objects.create(subject=second, name="矩阵")
    url = reverse("question-create")
    invalid = client.post(url, {
        "save_intent": "publish", "title": "章节错误", "subject": str(first.pk),
        "section": str(wrong_section.pk),
    })
    assert invalid.status_code == 200
    with playwright.sync_playwright() as instance:
        browser = instance.chromium.launch()
        try:
            page = browser.new_page()
            page.route(f"{live_server.url}{url}", lambda route: route.fulfill(
                status=200, content_type="text/html", body=invalid.content
            ))
            page.goto(f"{live_server.url}{url}")
            assert page.locator('[data-question-form]').get_attribute("data-mode") == "complete"
            assert page.locator('#id_section').is_visible()
            assert page.locator('[data-error-summary]').evaluate("el => document.activeElement === el")
            page.locator('[data-error-summary] a[href="#id_section"]').click()
            assert page.locator('#id_section').evaluate("el => document.activeElement === el")
        finally:
            browser.close()


@pytest.mark.browser
@pytest.mark.django_db(transaction=True)
def test_attachment_queue_keeps_invalid_file_for_server_validation(live_server):
    with playwright.sync_playwright() as instance:
        browser = instance.chromium.launch()
        try:
            page = browser.new_page()
            page.goto(f"{live_server.url}{reverse('question-create')}")
            page.locator('#id_attachments').set_input_files({
                "name": "notes.pdf", "mimeType": "application/pdf", "buffer": b"%PDF-1.4"
            })
            assert page.locator('[data-attachment-queue] > li').count() == 1
            assert page.locator('[data-attachment-queue] > li').inner_text().find("notes.pdf") >= 0
            assert page.locator('[data-attachment-queue] [data-image-zoom]').count() == 0
            assert page.locator('#id_attachments').evaluate("el => [...el.files].map(file => file.name)") == ["notes.pdf"]
        finally:
            browser.close()


@pytest.mark.browser
@pytest.mark.django_db(transaction=True)
def test_missing_datatransfer_keeps_native_attachment_controls(live_server):
    with playwright.sync_playwright() as instance:
        browser = instance.chromium.launch()
        try:
            page = browser.new_page()
            page.add_init_script("Object.defineProperty(window, 'DataTransfer', {value: undefined})")
            page.goto(f"{live_server.url}{reverse('question-create')}")
            assert not page.locator('[data-question-form]').evaluate("el => el.classList.contains('is-enhanced')")
            assert page.locator('[data-attachment-protocol]').is_disabled()
            assert page.locator('[data-tab-panel="solution"]').is_visible()
            assert "原生附件" in page.locator('[data-attachment-count]').inner_text()
        finally:
            browser.close()


@pytest.mark.browser
@pytest.mark.django_db(transaction=True)
def test_local_draft_restores_fields_ui_and_new_file(live_server):
    with playwright.sync_playwright() as instance:
        browser = instance.chromium.launch()
        try:
            page = browser.new_page()
            url = f"{live_server.url}{reverse('question-create')}"
            page.goto(url)
            page.locator('#id_title').fill('未提交的题目')
            page.locator('#id_statement').fill('草稿题干')
            page.locator('[data-form-mode="complete"]').click()
            page.get_by_role('tab', name='解答').click()
            page.locator('#id_attachments').set_input_files({
                'name': 'diagram.png', 'mimeType': 'image/png', 'buffer': b'png',
            })
            page.wait_for_function("""async () => {
              const {createDraftRepository} = await import('/static/question_bank/js/question-form-drafts.js');
              const drafts = await createDraftRepository().list('question:create');
              return drafts.some(draft => draft.fields.title === '未提交的题目' && draft.attachments.files.length === 1);
            }""")
            page.reload()
            page.get_by_role('button', name='恢复草稿').click()
            assert page.locator('#id_title').input_value() == '未提交的题目'
            assert page.locator('#id_statement').input_value() == '草稿题干'
            assert page.locator('[data-question-form]').get_attribute('data-mode') == 'complete'
            assert page.get_by_role('tab', name='解答').get_attribute('aria-selected') == 'true'
            assert page.locator('#id_attachments').evaluate('el => [...el.files].map(file => file.name)') == ['diagram.png']
            assert page.locator('#id_attachments').evaluate(
                'async el => Array.from(new Uint8Array(await el.files[0].arrayBuffer()))'
            ) == list(b'png')
        finally:
            browser.close()


@pytest.mark.browser
@pytest.mark.django_db(transaction=True)
def test_successful_submit_clears_only_saved_draft_revision(live_server):
    with playwright.sync_playwright() as instance:
        browser = instance.chromium.launch()
        try:
            page = browser.new_page()
            page.goto(f"{live_server.url}{reverse('question-create')}")
            page.locator('#id_title').fill('提交前持久化')
            page.get_by_role('button', name='保存草稿').click()
            page.wait_for_url('**/edit/*')
            page.wait_for_function("() => !location.search.includes('saved=1')")
            assert Question.objects.filter(title='提交前持久化', draft=True).exists()
            assert page.evaluate("""async () => {
              const db = await new Promise((resolve, reject) => {
                const request = indexedDB.open('math-question-bank', 1);
                request.onsuccess = () => resolve(request.result);
                request.onerror = () => reject(request.error);
              });
              return await new Promise((resolve, reject) => {
                const request = db.transaction('drafts').objectStore('drafts').getAll();
                request.onsuccess = () => resolve(request.result.some(draft => draft.fields.title === '提交前持久化'));
                request.onerror = () => reject(request.error);
              });
            }""") is False
        finally:
            browser.close()


@pytest.mark.browser
@pytest.mark.django_db(transaction=True)
def test_changed_server_baseline_does_not_restore_old_attachment_deletion(live_server):
    subject = Subject.objects.create(name='分析')
    question = Question.objects.create(subject=subject, title='原题')
    first = QuestionAttachment.objects.create(question=question, file='questions/first.png', file_kind='image', sort_order=0)
    second = QuestionAttachment.objects.create(question=question, file='questions/second.png', file_kind='image', sort_order=1)
    with playwright.sync_playwright() as instance:
        browser = instance.chromium.launch()
        try:
            page = browser.new_page()
            url = f"{live_server.url}{reverse('question-edit', args=[question.pk])}"
            page.goto(url)
            page.locator(f'[data-attachment-id="{first.pk}"] [data-attachment-remove]').click()
            page.wait_for_function("() => document.querySelector('[data-draft-status]').textContent.includes('已保存')")
            question.title = '服务器新版本'
            question.save()
            page.reload()
            page.get_by_role('button', name='恢复草稿').click()
            assert page.locator(f'[data-attachment-id="{first.pk}"]').count() == 1
            assert page.locator(f'[data-attachment-id="{second.pk}"]').count() == 1
        finally:
            browser.close()


@pytest.mark.browser
@pytest.mark.django_db(transaction=True)
def test_restore_historical_draft_reinstates_existing_attachments_and_order(live_server):
    subject = Subject.objects.create(name='分析')
    question = Question.objects.create(subject=subject, title='原题')
    first = QuestionAttachment.objects.create(question=question, file='questions/first.png', file_kind='image', sort_order=0)
    second = QuestionAttachment.objects.create(question=question, file='questions/second.png', file_kind='image', sort_order=1)
    with playwright.sync_playwright() as instance:
        browser = instance.chromium.launch()
        try:
            page = browser.new_page()
            page.goto(f"{live_server.url}{reverse('question-edit', args=[question.pk])}")
            baseline = page.evaluate("""() => {
              const root = document.querySelector('[data-question-form]');
              const attachments = [...root.querySelectorAll('[data-attachment-id]')].map(row => ({
                id: row.dataset.attachmentId, type: row.dataset.fileKind,
                sort_order: Number(row.dataset.attachmentSortOrder),
                filename: row.dataset.attachmentFilename.split(/[\\\\/]/).pop(),
                updated_at: row.dataset.attachmentUpdatedAt
              })).sort((a, b) => a.sort_order - b.sort_order || a.id.localeCompare(b.id));
              return JSON.stringify({question_updated_at: root.dataset.questionUpdatedAt || '', attachments});
            }""")
            page.evaluate("""async ({questionId, baseline, formVersion, first, second}) => {
              const {createDraftRepository} = await import('/static/question_bank/js/question-form-drafts.js');
              await createDraftRepository().put({
                key: `question:${questionId}:historical`, target_key: `question:${questionId}`,
                updated_at: new Date().toISOString(), revision: 1, form_version: formVersion,
                baseline, fields: {title: '历史草稿'},
                attachments: {files: [], removed: [], order: [`existing:${second}`, `existing:${first}`]}
              });
            }""", {
                'questionId': question.pk,
                'baseline': baseline,
                'formVersion': page.locator('[data-question-form]').get_attribute('data-form-version'),
                'first': str(first.pk), 'second': str(second.pk),
            })
            page.locator(f'[data-attachment-id="{first.pk}"] [data-attachment-remove]').click()
            page.reload()
            page.get_by_role('button', name='恢复草稿').click()
            assert page.locator('[data-attachment-id]').evaluate_all("rows => rows.map(row => row.dataset.attachmentId)") == [str(second.pk), str(first.pk)]
            assert page.locator('input[name="removed_attachment"]').count() == 0
        finally:
            browser.close()


@pytest.mark.browser
@pytest.mark.django_db(transaction=True)
def test_malformed_manual_draft_is_rejected_without_unhandled_restore_error(live_server):
    with playwright.sync_playwright() as instance:
        browser = instance.chromium.launch()
        try:
            page = browser.new_page()
            page.goto(f"{live_server.url}{reverse('question-create')}")
            page.evaluate("""async () => {
              const {createDraftRepository} = await import('/static/question_bank/js/question-form-drafts.js');
              await createDraftRepository().put({
                key: 'question:create:malformed', target_key: 'question:create',
                updated_at: new Date().toISOString(), revision: 1, fields: null,
                attachments: {files: {}, removed: [], order: []}
              });
            }""")
            page.reload()
            page.get_by_role('button', name='恢复草稿').click()
            assert '本地草稿保存或提交记录失败' in page.locator('[data-save-status]').inner_text()
        finally:
            browser.close()


@pytest.mark.browser
@pytest.mark.django_db(transaction=True)
def test_broadcast_channel_respects_existing_local_storage_lease(live_server):
    with playwright.sync_playwright() as instance:
        browser = instance.chromium.launch()
        try:
            page = browser.new_page()
            page.add_init_script("""(() => {
              const original = window.BroadcastChannel;
              window.BroadcastChannel = class extends original {
                postMessage(message) {
                  if (message.type === 'claim') setTimeout(() => {}, 500);
                  return super.postMessage(message);
                }
              };
            })()""")
            page.goto(f"{live_server.url}{reverse('question-create')}")
            old_id = page.evaluate("""() => {
              const id = sessionStorage.getItem('math-question-bank:editor-id');
              localStorage.setItem(`math-question-bank:editor-lease:${id}`, JSON.stringify({owner: 'other', expires: Date.now() + 10000}));
              location.reload();
              return id;
            }""")
            page.wait_for_function("() => document.querySelector('[data-draft-status]')")
            assert page.evaluate("() => sessionStorage.getItem('math-question-bank:editor-id')") != old_id
        finally:
            browser.close()


@pytest.mark.browser
@pytest.mark.django_db(transaction=True)
def test_unchanged_server_baseline_restores_attachment_deletion_after_timestamp_changes(live_server):
    subject = Subject.objects.create(name='分析')
    question = Question.objects.create(subject=subject, title='原题')
    first = QuestionAttachment.objects.create(question=question, file='questions/first.png', file_kind='image', sort_order=0)
    with playwright.sync_playwright() as instance:
        browser = instance.chromium.launch()
        try:
            page = browser.new_page()
            page.goto(f"{live_server.url}{reverse('question-edit', args=[question.pk])}")
            page.locator(f'[data-attachment-id="{first.pk}"] [data-attachment-remove]').click()
            page.wait_for_function("() => document.querySelector('[data-draft-status]').textContent.includes('已保存')")
            page.wait_for_timeout(1200)
            page.reload()
            page.get_by_role('button', name='恢复草稿').click()
            assert page.locator(f'[data-attachment-id="{first.pk}"]').count() == 0
        finally:
            browser.close()


@pytest.mark.browser
@pytest.mark.django_db(transaction=True)
def test_create_draft_key_integer_revision_and_exact_saved_cleanup(live_server):
    with playwright.sync_playwright() as instance:
        browser = instance.chromium.launch()
        try:
            page = browser.new_page()
            url = f"{live_server.url}{reverse('question-create')}"
            page.goto(url)
            page.locator('#id_title').fill('revision one')
            page.wait_for_function("() => document.querySelector('[data-draft-status]').textContent.includes('已保存')")
            first = page.evaluate("""async () => {
              const {createDraftRepository} = await import('/static/question_bank/js/question-form-drafts.js');
              return (await createDraftRepository().list('question:create'))[0];
            }""")
            assert first['key'].startswith('question:create:')
            assert first['revision'] == 1
            page.locator('#id_title').fill('revision two')
            page.wait_for_function("""async () => {
              const {createDraftRepository} = await import('/static/question_bank/js/question-form-drafts.js');
              const drafts = await createDraftRepository().list('question:create');
              return drafts[0]?.revision === 2;
            }""")
            page.evaluate("""key => sessionStorage.setItem('math-question-bank:pending-submit',
              JSON.stringify({key, revision: 1}))""", first['key'])
            page.goto(url + '?saved=1')
            page.wait_for_function('() => !location.search.includes("saved=1")')
            assert page.evaluate("""async key => {
              const {createDraftRepository} = await import('/static/question_bank/js/question-form-drafts.js');
              return (await createDraftRepository().get(key))?.revision;
            }""", first['key']) == 2
            page.evaluate("""key => sessionStorage.setItem('math-question-bank:pending-submit',
              JSON.stringify({key, revision: 2}))""", first['key'])
            page.goto(url + '?saved=1')
            page.wait_for_function('() => !location.search.includes("saved=1")')
            assert page.evaluate("""async key => {
              const {createDraftRepository} = await import('/static/question_bank/js/question-form-drafts.js');
              return await createDraftRepository().get(key);
            }""", first['key']) is None
        finally:
            browser.close()


@pytest.mark.browser
@pytest.mark.django_db(transaction=True)
def test_draft_put_resolves_after_indexeddb_transaction_completes(live_server):
    with playwright.sync_playwright() as instance:
        browser = instance.chromium.launch()
        try:
            page = browser.new_page()
            page.goto(f"{live_server.url}{reverse('question-create')}")
            assert page.evaluate("""async () => {
              let completed = false;
              const original = IDBDatabase.prototype.transaction;
              IDBDatabase.prototype.transaction = function(...args) {
                const transaction = original.apply(this, args);
                if (args[1] === 'readwrite') transaction.addEventListener('complete', () => { completed = true; });
                return transaction;
              };
              try {
                const {createDraftRepository} = await import('/static/question_bank/js/question-form-drafts.js');
                await createDraftRepository().put({key: 'test:complete', target_key: 'test', updated_at: '', revision: 1});
                return completed;
              } finally { IDBDatabase.prototype.transaction = original; }
            }""") is True
        finally:
            browser.close()


@pytest.mark.browser
@pytest.mark.django_db(transaction=True)
def test_duplicated_tab_copies_draft_and_expired_lease_is_cleaned(live_server):
    with playwright.sync_playwright() as instance:
        browser = instance.chromium.launch()
        try:
            context = browser.new_context()
            page = context.new_page()
            page.goto(f"{live_server.url}{reverse('question-create')}")
            page.locator('#id_title').fill('跨标签草稿')
            page.wait_for_function("() => document.querySelector('[data-draft-status]').textContent.includes('已保存')")
            old_id = page.evaluate("sessionStorage.getItem('math-question-bank:editor-id')")
            page.evaluate("localStorage.setItem('math-question-bank:editor-lease:expired', JSON.stringify({owner:'old', expires:0}))")
            with page.expect_popup() as popup_info:
                page.evaluate('window.open(location.href)')
            popup = popup_info.value
            popup.wait_for_function("old => sessionStorage.getItem('math-question-bank:editor-id') !== old", arg=old_id)
            assert popup.evaluate("sessionStorage.getItem('math-question-bank:editor-id')") != old_id
            assert popup.evaluate("localStorage.getItem('math-question-bank:editor-lease:expired')") is None
            popup.wait_for_function("""async () => {
              const {createDraftRepository} = await import('/static/question_bank/js/question-form-drafts.js');
              return (await createDraftRepository().list('question:create')).length === 2;
            }""")
            assert popup.evaluate("""async () => {
              const {createDraftRepository} = await import('/static/question_bank/js/question-form-drafts.js');
              return (await createDraftRepository().list('question:create')).filter(d => d.fields.title === '跨标签草稿').length;
            }""") == 2
            context.close()
        finally:
            browser.close()


@pytest.mark.browser
@pytest.mark.django_db(transaction=True)
def test_session_pending_write_failure_requires_confirmation(live_server):
    with playwright.sync_playwright() as instance:
        browser = instance.chromium.launch()
        try:
            page = browser.new_page()
            page.add_init_script("""(() => {
              const original = Storage.prototype.setItem;
              Storage.prototype.setItem = function(key, value) {
                if (key === 'math-question-bank:pending-submit') throw new Error('blocked');
                return original.call(this, key, value);
              };
            })()""")
            page.goto(f"{live_server.url}{reverse('question-create')}")
            page.locator('#id_title').fill('提交确认')
            page.once('dialog', lambda dialog: dialog.dismiss())
            page.get_by_role('button', name='保存草稿').click()
            assert page.locator('#id_title').input_value() == '提交确认'
            assert '重新选择' in page.locator('[data-save-status]').inner_text()
            assert page.get_by_role('button', name='保存草稿').is_enabled()
            assert page.get_by_role('button', name='保存题目').is_enabled()
            page.once('dialog', lambda dialog: dialog.accept())
            page.get_by_role('button', name='保存草稿').click()
            page.wait_for_url('**/edit/*')
            assert Question.objects.filter(title='提交确认', draft=True).exists()
        finally:
            browser.close()


@pytest.mark.browser
@pytest.mark.django_db(transaction=True)
def test_storage_denied_in_cloned_tab_uses_separate_draft_key(live_server):
    with playwright.sync_playwright() as instance:
        browser = instance.chromium.launch()
        try:
            context = browser.new_context()
            page = context.new_page()
            page.goto(f"{live_server.url}{reverse('question-create')}")
            page.locator('#id_title').fill('原标签')
            page.wait_for_function("() => document.querySelector('[data-draft-status]').textContent.includes('已保存')")
            original_id = page.evaluate("sessionStorage.getItem('math-question-bank:editor-id')")
            context.add_init_script("""(() => {
              const original = Storage.prototype.setItem;
              Storage.prototype.setItem = function(key, value) {
                if (key.startsWith('math-question-bank:editor-lease:')) throw new Error('blocked');
                return original.call(this, key, value);
              };
            })()""")
            with page.expect_popup() as popup_info:
                page.evaluate('window.open(location.href)')
            popup = popup_info.value
            popup.wait_for_function("old => sessionStorage.getItem('math-question-bank:editor-id') !== old", arg=original_id)
            popup.locator('#id_title').fill('复制标签')
            popup.wait_for_function("() => document.querySelector('[data-draft-status]').textContent.includes('已保存')")
            assert page.evaluate("""async () => {
              const {createDraftRepository} = await import('/static/question_bank/js/question-form-drafts.js');
              return (await createDraftRepository().list('question:create')).map(d => d.fields.title).sort();
            }""") == ['原标签', '复制标签']
            context.close()
        finally:
            browser.close()


@pytest.mark.browser
@pytest.mark.django_db(transaction=True)
def test_storage_denied_single_tab_reload_keeps_editor_key(live_server):
    with playwright.sync_playwright() as instance:
        browser = instance.chromium.launch()
        try:
            page = browser.new_page()
            page.add_init_script("""(() => {
              const original = Storage.prototype.setItem;
              Storage.prototype.setItem = function(key, value) {
                if (key.startsWith('math-question-bank:editor-lease:')) throw new Error('blocked');
                return original.call(this, key, value);
              };
            })()""")
            page.goto(f"{live_server.url}{reverse('question-create')}")
            page.locator('#id_title').fill('刷新前')
            page.wait_for_function("() => document.querySelector('[data-draft-status]').textContent.includes('已保存')")
            editor_id = page.evaluate("sessionStorage.getItem('math-question-bank:editor-id')")
            page.reload()
            page.wait_for_function("() => Boolean(window.QuestionFormWorkbench?.drafts)")
            assert page.evaluate("sessionStorage.getItem('math-question-bank:editor-id')") == editor_id
            page.locator('#id_title').fill('刷新后')
            page.wait_for_function("""async () => {
              const {createDraftRepository} = await import('/static/question_bank/js/question-form-drafts.js');
              const drafts = await createDraftRepository().list('question:create');
              return drafts.length === 1 && drafts[0].fields.title === '刷新后';
            }""")
            assert page.evaluate("""async id => {
              const {createDraftRepository} = await import('/static/question_bank/js/question-form-drafts.js');
              return (await createDraftRepository().list('question:create'))[0].key === `question:create:${id}`;
            }""", editor_id) is True
        finally:
            browser.close()


@pytest.mark.browser
@pytest.mark.django_db(transaction=True)
def test_start_blank_draft_after_cancel_keeps_previous_create_draft(live_server):
    with playwright.sync_playwright() as instance:
        browser = instance.chromium.launch()
        try:
            page = browser.new_page()
            url = f"{live_server.url}{reverse('question-create')}"
            page.goto(url)
            page.locator('#id_title').fill('旧草稿')
            old_id = page.evaluate("sessionStorage.getItem('math-question-bank:editor-id')")
            page.get_by_role('link', name='取消').click()
            page.wait_for_url(f"{live_server.url}{reverse('question-list')}")
            page.goto(url)
            page.get_by_role('button', name='开始空白草稿').click()
            page.wait_for_function("old => sessionStorage.getItem('math-question-bank:editor-id') !== old", arg=old_id)
            assert page.locator('#id_title').input_value() == ''
            page.locator('#id_title').fill('新草稿')
            page.wait_for_function("""async () => {
              const {createDraftRepository} = await import('/static/question_bank/js/question-form-drafts.js');
              const drafts = await createDraftRepository().list('question:create');
              return drafts.some(draft => draft.fields.title === '新草稿');
            }""")
            assert page.evaluate("""async () => {
              const {createDraftRepository} = await import('/static/question_bank/js/question-form-drafts.js');
              return (await createDraftRepository().list('question:create')).map(draft => draft.fields.title).sort();
            }""") == ['新草稿', '旧草稿']
        finally:
            browser.close()


@pytest.mark.browser
@pytest.mark.django_db(transaction=True)
def test_start_blank_flushes_latest_unsaved_text_to_previous_key(live_server):
    with playwright.sync_playwright() as instance:
        browser = instance.chromium.launch()
        try:
            page = browser.new_page()
            url = f"{live_server.url}{reverse('question-create')}"
            page.goto(url)
            page.locator('#id_title').fill('旧内容')
            page.wait_for_function("() => document.querySelector('[data-draft-status]').textContent.includes('已保存')")
            old_id = page.evaluate("sessionStorage.getItem('math-question-bank:editor-id')")
            page.locator('#id_title').fill('最后输入')
            page.get_by_role('button', name='开始空白草稿').click()
            page.wait_for_function("old => sessionStorage.getItem('math-question-bank:editor-id') !== old", arg=old_id)
            assert page.evaluate("""async old => {
              const {createDraftRepository} = await import('/static/question_bank/js/question-form-drafts.js');
              return (await createDraftRepository().get(`question:create:${old}`))?.fields.title;
            }""", old_id) == '最后输入'
            assert page.locator('#id_title').input_value() == ''
        finally:
            browser.close()


@pytest.mark.browser
@pytest.mark.django_db(transaction=True)
def test_invalid_post_keeps_server_fields_and_rebuilds_draft_blob(live_server, client):
    url = reverse('question-create')
    invalid = client.post(url, {'save_intent': 'publish', 'title': '服务器回显', 'mastery': 'invalid'})
    assert invalid.status_code == 200
    with playwright.sync_playwright() as instance:
        browser = instance.chromium.launch()
        try:
            page = browser.new_page()

            def return_invalid_post(route):
                if route.request.method == 'POST':
                    route.fulfill(status=200, content_type='text/html', body=invalid.content)
                else:
                    route.continue_()

            page.route(f'{live_server.url}{url}', return_invalid_post)
            page.goto(f'{live_server.url}{url}')
            page.locator('#id_title').fill('本地草稿')
            page.locator('#id_attachments').set_input_files({
                'name': 'new.png', 'mimeType': 'image/png', 'buffer': b'blob-content',
            })
            page.get_by_role('button', name='保存题目').click()
            page.wait_for_function("() => document.querySelector('[data-recovery-status]').textContent.includes('已恢复')")
            assert page.locator('#id_title').input_value() == '服务器回显'
            assert page.locator('#id_attachments').evaluate(
                'async el => new TextDecoder().decode(await el.files[0].arrayBuffer())'
            ) == 'blob-content'
        finally:
            browser.close()


@pytest.mark.browser
@pytest.mark.django_db(transaction=True)
def test_quota_failure_requires_confirmation_before_submit(live_server):
    with playwright.sync_playwright() as instance:
        browser = instance.chromium.launch()
        try:
            page = browser.new_page()
            page.add_init_script("""(() => {
              const original = IDBObjectStore.prototype.put;
              IDBObjectStore.prototype.put = function(...args) {
                if (this.name === 'drafts') throw new DOMException('full', 'QuotaExceededError');
                return original.apply(this, args);
              };
            })()""")
            page.goto(f"{live_server.url}{reverse('question-create')}")
            page.locator('#id_title').fill('配额错误')
            page.once('dialog', lambda dialog: dialog.dismiss())
            page.get_by_role('button', name='保存草稿').click()
            assert page.locator('#id_title').input_value() == '配额错误'
            assert not Question.objects.filter(title='配额错误').exists()
            assert '重新选择新图片' in page.locator('[data-save-status]').inner_text()
            page.once('dialog', lambda dialog: dialog.accept())
            page.get_by_role('button', name='保存草稿').click()
            page.wait_for_url('**/edit/*')
            assert Question.objects.filter(title='配额错误', draft=True).exists()
        finally:
            browser.close()


@pytest.mark.browser
@pytest.mark.django_db(transaction=True)
def test_delayed_broadcast_occupied_response_forks_before_first_write(live_server):
    with playwright.sync_playwright() as instance:
        browser = instance.chromium.launch()
        try:
            page = browser.new_page()
            page.add_init_script("""(() => {
              sessionStorage.setItem('math-question-bank:editor-id', 'cloned-editor');
              window.BroadcastChannel = class {
                sent = false;
                postMessage(message) {
                  if (message.type === 'claim' && !this.sent) {
                    this.sent = true;
                    setTimeout(() => this.onmessage({data: {
                      type: 'occupied', editorId: message.editorId, owner: 'existing-tab'
                    }}), 80);
                  }
                }
                close() {}
              };
            })()""")
            page.goto(f"{live_server.url}{reverse('question-create')}")
            page.wait_for_function("() => sessionStorage.getItem('math-question-bank:editor-id') !== 'cloned-editor'")
            page.locator('#id_title').fill('独立草稿')
            page.wait_for_function("() => document.querySelector('[data-draft-status]').textContent.includes('已保存')")
            assert page.evaluate("""async () => {
              const {createDraftRepository} = await import('/static/question_bank/js/question-form-drafts.js');
              const drafts = await createDraftRepository().list('question:create');
              return drafts[0].key !== 'question:create:cloned-editor';
            }""") is True
        finally:
            browser.close()


@pytest.mark.browser
@pytest.mark.django_db(transaction=True)
def test_old_form_version_keeps_server_attachment_operations(live_server):
    subject = Subject.objects.create(name='分析')
    question = Question.objects.create(subject=subject, title='原题')
    attachment = QuestionAttachment.objects.create(question=question, file='questions/first.png', file_kind='image', sort_order=0)
    with playwright.sync_playwright() as instance:
        browser = instance.chromium.launch()
        try:
            page = browser.new_page()
            page.goto(f"{live_server.url}{reverse('question-edit', args=[question.pk])}")
            page.locator(f'[data-attachment-id="{attachment.pk}"] [data-attachment-remove]').click()
            page.wait_for_function("() => document.querySelector('[data-draft-status]').textContent.includes('已保存')")
            page.evaluate("""async () => {
              const {createDraftRepository} = await import('/static/question_bank/js/question-form-drafts.js');
              const repo = createDraftRepository();
              const draft = (await repo.list(document.querySelector('[data-question-form]').dataset.questionId
                ? `question:${document.querySelector('[data-question-form]').dataset.questionId}` : 'question:create'))[0];
              draft.form_version = 'old';
              await repo.put(draft);
            }""")
            page.reload()
            page.get_by_role('button', name='恢复草稿').click()
            assert page.locator(f'[data-attachment-id="{attachment.pk}"]').count() == 1
        finally:
            browser.close()


@pytest.mark.browser
@pytest.mark.django_db(transaction=True)
def test_draft_storage_failure_does_not_block_submit(live_server):
    with playwright.sync_playwright() as instance:
        browser = instance.chromium.launch()
        try:
            page = browser.new_page()
            page.add_init_script("Object.defineProperty(window, 'indexedDB', {value: {open() { throw new Error('unavailable'); }}})")
            page.goto(f"{live_server.url}{reverse('question-create')}")
            page.locator('#id_title').fill('无草稿存储')
            dialogs = []
            page.on('dialog', lambda dialog: (dialogs.append(dialog.message), dialog.accept()))
            page.get_by_role('button', name='保存草稿').click()
            page.wait_for_url('**/edit/*')
            assert '重新选择' in dialogs[0]
            assert Question.objects.filter(title='无草稿存储', draft=True).exists()
        finally:
            browser.close()


@pytest.mark.browser
@pytest.mark.django_db(transaction=True)
def test_rapid_submit_locks_actions_before_draft_save_completes(live_server):
    with playwright.sync_playwright() as instance:
        browser = instance.chromium.launch()
        try:
            page = browser.new_page()
            page.goto(f"{live_server.url}{reverse('question-create')}")
            page.locator('#id_title').fill('单次提交')
            page.evaluate("""() => {
              const form = document.querySelector('[data-question-form]');
              const submitter = form.querySelector('button[name="save_intent"][value="draft"]');
              window.submitEvents = [];
              form.addEventListener('submit', event => {
                window.submitEvents.push({
                  disabled: [...form.querySelectorAll('button[name="save_intent"]')]
                    .every(button => button.disabled)
                });
                event.preventDefault();
              });
              form.dispatchEvent(new SubmitEvent('submit', {bubbles: true, cancelable: true, submitter}));
              form.dispatchEvent(new SubmitEvent('submit', {bubbles: true, cancelable: true, submitter}));
            }""")
            page.wait_for_function("() => window.submitEvents.length >= 3")
            page.wait_for_timeout(200)
            assert page.evaluate("window.submitEvents.length") == 3
            assert page.evaluate("window.submitEvents.every(event => event.disabled)")
        finally:
            browser.close()


@pytest.mark.browser
@pytest.mark.django_db(transaction=True)
def test_locked_submit_preserves_save_intent_in_real_post(live_server):
    with playwright.sync_playwright() as instance:
        browser = instance.chromium.launch()
        try:
            page = browser.new_page()
            page.goto(f"{live_server.url}{reverse('question-create')}")
            page.locator('#id_title').fill('锁定后保存草稿')
            page.get_by_role('button', name='保存草稿').click()
            page.wait_for_url('**/edit/*')
            question = Question.objects.get(title='锁定后保存草稿')
            assert question.draft is True
        finally:
            browser.close()


@pytest.mark.browser
@pytest.mark.django_db(transaction=True)
def test_published_question_draft_transition_requires_confirmation(live_server):
    subject = Subject.objects.create(name="数学")
    question = Question.objects.create(subject=subject, title="正式题目", draft=False)
    url = reverse("question-edit", args=[question.pk])
    with playwright.sync_playwright() as instance:
        browser = instance.chromium.launch()
        try:
            page = browser.new_page()
            requests = []
            page.on("request", lambda request: requests.append(request)
                    if request.method == "POST" and request.url.endswith(url) else None)
            page.goto(f"{live_server.url}{url}")
            page.once("dialog", lambda dialog: dialog.dismiss())
            page.get_by_role("button", name="转为草稿").click()
            page.wait_for_timeout(150)
            question.refresh_from_db()
            assert question.draft is False
            assert requests == []

            page.once("dialog", lambda dialog: dialog.accept())
            page.get_by_role("button", name="转为草稿").click()
            page.wait_for_timeout(400)
            question.refresh_from_db()
            assert question.draft is True
            assert len(requests) == 1
        finally:
            browser.close()
