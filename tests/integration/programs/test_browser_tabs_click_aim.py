"""The tab driver's click aim: where the preview's marker goes, in the viewport the click sees."""
import pytest

from openprogram.programs.applications.browser import tabs
from openprogram.programs.applications.browser.pages import sanitize_operation

pytestmark = pytest.mark.browser


def test_a_click_below_the_fold_aims_at_its_element_once_scrolled_into_view():
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 800, "height": 600})
            page.set_content('<div style="height:2000px"></div>'
                             '<button style="width:100px;height:40px;margin-left:300px">Go</button>')
            tab = tabs.Tab(id="t1", window_id="main", tab_id="a")
            tabs.Driver._aim(None, page, tab, page.get_by_role("button", name="Go"))
            point = sanitize_operation(action="click", arguments={}, result={"ok": True, "point": tab.aim["point"]},
                                       phase="acknowledged", viewport=tab.aim["viewport"])["point"]
            assert point["width"] == 800 and point["height"] == 600
            assert 0 <= point["x"] <= 800 and 0 <= point["y"] <= 600
            assert abs(point["x"] - (300 + 8 + 50)) < 2
        finally:
            browser.close()
