"""The tab driver's upload: a file field directly, anything else through the file chooser its click opens."""
import pytest

from openprogram.programs.applications.browser.tabs import (
    ELEMENT_STATE_SCRIPT, FILE_FIELDS_SCRIPT, FILE_INPUT_SCRIPT)

pytestmark = pytest.mark.browser

PAGE = """<div><button type="button">Upload CV</button><input id="cv" type="file" style="display:none"></div>
<div><label for="photo">Photo</label><input id="photo" type="file" hidden></div>"""


def test_only_a_file_input_or_its_label_stands_for_one_and_a_button_beside_it_does_not():
    # A button is clicked and answered through its file chooser: the input next to it may be another field.
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page()
            page.set_content(PAGE)
            assert page.locator("#cv").evaluate_handle(FILE_INPUT_SCRIPT).as_element().get_attribute("id") == "cv"
            assert page.locator("label").evaluate_handle(FILE_INPUT_SCRIPT).as_element().get_attribute("id") == "photo"
            assert page.get_by_role("button", name="Upload CV").evaluate_handle(FILE_INPUT_SCRIPT).as_element() is None
        finally:
            browser.close()


def test_a_chooser_caught_while_clicking_takes_the_file_even_for_an_input_made_on_click():
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page()
            page.set_content("""<button onclick="const i = document.createElement('input'); i.type = 'file';
              i.onchange = () => document.title = i.files[0].name; i.click()">Attach</button>""")
            caught = []

            def hold(chooser):
                caught.append(chooser)
            page.on("filechooser", hold)
            page.get_by_role("button", name="Attach").click()
            page.wait_for_timeout(200)
            page.remove_listener("filechooser", hold)
            caught[0].set_files({"name": "cv.pdf", "mimeType": "application/pdf", "buffer": b"%PDF"})
            page.wait_for_function("document.title === 'cv.pdf'", timeout=2000)
        finally:
            browser.close()


def test_the_file_fields_listed_when_a_button_opens_no_chooser_say_which_sits_beside_it():
    # Listed for the model to pick, never picked for it: two fields side by side are not told apart.
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page()
            page.set_content("""
              <section><h3>Resume</h3><div><button>Upload CV</button></div>
                <input id="resumeKey" type="file" accept=".pdf" hidden></section>
              <section><button>Attach</button><input name="a" type="file" multiple hidden>
                <input name="b" type="file" hidden></section>
              <section><label>Photo <input id="photo" type="file" disabled></label></section>""")
            fields = page.get_by_role("button", name="Upload CV").evaluate(FILE_FIELDS_SCRIPT)
            assert [(f["id"] or f["name"], f["accept"], f["multiple"], f["disabled"], f["beside"]) for f in fields] == [
                ("resumeKey", ".pdf", False, False, True), ("a", "", True, False, False),
                ("b", "", False, False, False), ("photo", "", False, True, False)]
            assert "Resume" in fields[0]["near"] and fields[3]["near"] == "Photo"
            assert not any(f["beside"] for f in page.get_by_role("button", name="Attach").evaluate(FILE_FIELDS_SCRIPT))
            page.locator('input[type="file"]').nth(fields[0]["index"]).set_input_files(
                {"name": "cv.pdf", "mimeType": "application/pdf", "buffer": b"%PDF"})
            assert page.locator("#resumeKey").evaluate("i => i.files[0].name") == "cv.pdf"
        finally:
            browser.close()


def test_the_state_of_a_button_stuck_in_an_earlier_upload_is_read_back():
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page()
            page.set_content('<div role="button" aria-disabled="true">Uploading...\n 100%</div>')
            state = page.get_by_role("button").evaluate(ELEMENT_STATE_SCRIPT)
            assert state == {"text": "Uploading... 100%", "disabled": True}
        finally:
            browser.close()
