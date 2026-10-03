import pytest
from django.urls import reverse

from question_bank.models import Question, Subject, Tag


playwright = pytest.importorskip("playwright.sync_api")


@pytest.mark.browser
@pytest.mark.django_db(transaction=True)
def test_tag_picker_prefix_search_selects_canonical_tag_and_submits(live_server):
    subject = Subject.objects.create(name="数学分析")
    target = Tag.objects.create(name="极限")
    Tag.objects.create(name="积分")
    question = Question.objects.create(subject=subject, title="极限题", draft=False)
    question.tags.add(target)

    with playwright.sync_playwright() as instance:
        browser = instance.chromium.launch()
        try:
            page = browser.new_page()
            page.goto(f"{live_server.url}{reverse('question-list')}?subject={subject.pk}")
            field = page.locator("#tag-picker-q")
            field.fill("极")
            page.locator(".tag-picker__search-results").wait_for()
            result = page.locator(".tag-picker__search-results label").first
            assert result.inner_text().find("极限") >= 0
            result.locator("input").check()
            page.locator(".tag-picker__search-submit").click()
            page.wait_for_url("**/?*")
            assert page.locator(f'input[name="tag"][value="{target.pk}"]').is_checked()
            assert page.locator(".question-result-card").count() == 1
            assert page.locator(".question-result-card").inner_text().find("极限题") >= 0
        finally:
            browser.close()


@pytest.mark.browser
@pytest.mark.django_db(transaction=True)
def test_tag_picker_category_paging_enhances_fragment_and_falls_back_to_link(live_server):
    subject = Subject.objects.create(name="数学分析")
    category = Tag.objects.create(name="方法")
    for index in range(8):
        Tag.objects.create(name=f"方法{index:02d}", parent=category)
    question = Question.objects.create(subject=subject, title="分类题", draft=False)
    question.tags.add(category)

    with playwright.sync_playwright() as instance:
        browser = instance.chromium.launch()
        try:
            page = browser.new_page()
            page.goto(f"{live_server.url}{reverse('question-list')}?subject={subject.pk}&tag_picker_group={category.pk}")
            panel = page.locator(f'[data-tag-category-panel="{category.pk}"]')
            next_link = panel.locator(".tag-picker__pagination a").last
            assert next_link.count() == 1
            href = next_link.get_attribute("href")
            next_link.click()
            page.wait_for_function(
                "group => document.querySelector(`[data-tag-category-panel=\\\"${group}\\\"]`).textContent.includes('第 2 页')",
                str(category.pk),
            )
            assert page.locator(".question-result-card").inner_text().find("分类题") >= 0
            assert page.locator(f'[data-tag-category-panel="{category.pk}"]').is_visible()

            page.goto(f"{live_server.url}{reverse('question-list')}?subject={subject.pk}&tag_picker_group={category.pk}&tag_picker_page=2")
            failed_next = page.locator(f'[data-tag-category-panel="{category.pk}"] .tag-picker__pagination a').first
            if failed_next.count():
                failed_href = failed_next.get_attribute("href")
                assert failed_href
                aborted = {"value": False}

                def abort_first_category_request(route):
                    if not aborted["value"]:
                        aborted["value"] = True
                        route.abort()
                    else:
                        route.continue_()

                page.route("**/?*tag_picker_group=*", abort_first_category_request)
                failed_next.click()
                page.wait_for_url("**tag_picker_page=1*")
                assert page.url.endswith("tag_picker_page=1") or "tag_picker_page=1&" in page.url
        finally:
            browser.close()
