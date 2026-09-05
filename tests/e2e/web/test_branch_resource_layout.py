from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[3]
pytestmark = pytest.mark.browser


def test_branch_resource_details_expand_without_clipping_and_support_keyboard():
    playwright = pytest.importorskip("playwright.sync_api")
    css = (ROOT / "apps/web/app/styles/right-dock/branches-panel.css").read_text(
        encoding="utf-8"
    )
    html = f"""<style>:root{{--ui-list-radius:6px}}{css}</style>
    <div class='branches-list'><div class='branch-item'>
      <span class='branch-item-dot'></span><span class='branch-item-name'>job</span>
      <details class='branch-item-resource'>
        <summary aria-label='Job resource details for job'>resources</summary>
        <pre>{{"resource_state":"released","reason_code":"completed","budget":{{"tokens":10}}}}</pre>
      </details>
    </div></div>"""
    with playwright.sync_playwright() as runtime:
        browser = runtime.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(html)
        summary = page.get_by_label("Job resource details for job")
        summary.focus()
        page.keyboard.press("Enter")
        assert page.locator("details").evaluate("node => node.open") is True
        row = page.locator(".branch-item").bounding_box()
        detail = page.locator("pre").bounding_box()
        listing = page.locator(".branches-list").bounding_box()
        assert row and detail and listing
        assert row["height"] > 32
        assert detail["y"] + detail["height"] <= row["y"] + row["height"]
        assert detail["y"] + detail["height"] <= listing["y"] + listing["height"]
        summary.click()
        assert page.locator("details").evaluate("node => node.open") is False
        browser.close()
