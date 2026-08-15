import os
import re

import pytest


pytestmark = pytest.mark.live


def _box(locator):
    box = locator.bounding_box()
    assert box is not None
    return box


def test_real_composer_controls_compact_and_keep_their_popovers():
    playwright = pytest.importorskip("playwright.sync_api")
    cdp_url = os.environ.get("OPENPROGRAM_E2E_CDP_URL")
    if not cdp_url:
        pytest.skip("set OPENPROGRAM_E2E_CDP_URL to the default App CDP endpoint")

    with playwright.sync_playwright() as runtime:
        browser = runtime.chromium.connect_over_cdp(cdp_url)
        page = next(
            (
                page
                for context in browser.contexts
                for page in context.pages
                if page.url.startswith("http://127.0.0.1:18100/")
            ),
            None,
        )
        if page is None:
            pytest.skip("default OpenProgram App page is not open")

        row = page.locator(".composer-bottom-row")
        row.wait_for(state="visible")
        permission = row.locator(".permission-badge")
        agents = row.locator(".agent-badge")
        effort = row.locator('button[class*="_effortText"]')
        plus = row.locator('button[class*="_plusBtn"]')
        tools = row.locator('div[class*="_toolChip"]')
        context = row.locator(".context-ring-badge")
        assert agents.count() == 2
        assert effort.count() == 1
        assert plus.count() == 1
        assert context.count() == 1
        assert tools.count() >= 1, "enable at least one composer tool for surface acceptance"

        controls = [permission, agents.nth(0), agents.nth(1), effort]
        labels = [
            permission.locator(".badge-details"),
            agents.nth(0).locator(".badge-details"),
            agents.nth(1).locator(".badge-details"),
            effort.locator("span").last,
        ]
        names = [label.text_content() for label in labels]
        assert all(name and name.strip() for name in names)

        original_style = row.get_attribute("style")
        try:
            row.evaluate("node => node.style.width = '700px'")
            page.wait_for_timeout(100)
            page.evaluate(
                "document.activeElement instanceof HTMLElement "
                "&& document.activeElement.blur()"
            )
            assert row.evaluate("node => node.getBoundingClientRect().width") > 560
            assert all(label.evaluate("node => getComputedStyle(node).position") == "static" for label in labels)
            assert all(_box(control)["width"] > 20 for control in controls)
            effort_normal_background = effort.evaluate(
                "node => getComputedStyle(node).backgroundColor"
            )

            surface_controls = controls + [plus, context] + [
                tools.nth(index) for index in range(tools.count())
            ]
            surface_normal_backgrounds = []
            for control in surface_controls:
                normal = control.evaluate(
                    "node => ({ background: getComputedStyle(node).backgroundColor, "
                    "borderWidth: getComputedStyle(node).borderTopWidth, "
                    "shadow: getComputedStyle(node).boxShadow, "
                    "outline: getComputedStyle(node).outlineStyle })"
                )
                surface_normal_backgrounds.append(normal["background"])
                assert normal["background"] not in ("transparent", "rgba(0, 0, 0, 0)")
                assert normal["borderWidth"] == "0px"
                assert normal["shadow"] == "none"
                assert normal["outline"] == "none"
                control.hover()
                hovered = control.evaluate("node => getComputedStyle(node).backgroundColor")
                assert hovered != normal["background"]
            page.mouse.move(0, 0)

            row.evaluate("node => node.style.width = '360px'")
            page.wait_for_timeout(100)
            assert all(label.evaluate("node => getComputedStyle(node).position") == "absolute" for label in labels)
            assert [label.text_content() for label in labels] == names
            assert all(round(_box(control)["width"]) == 20 for control in controls)

            boxes = sorted((_box(control) for control in controls), key=lambda box: box["x"])
            assert all(
                left["x"] + left["width"] <= right["x"]
                for left, right in zip(boxes, boxes[1:])
            )

            for trigger, normal_background in zip(
                (permission, agents.nth(0), agents.nth(1)),
                surface_normal_backgrounds[:3],
            ):
                trigger.click()
                assert trigger.get_attribute("aria-expanded") == "true"
                page.mouse.move(0, 0)
                assert (
                    trigger.evaluate("node => getComputedStyle(node).backgroundColor")
                    != normal_background
                )
                page.keyboard.press("Escape")
                assert trigger.get_attribute("aria-expanded") == "false"

            plus.click()
            playwright.expect(plus).to_have_attribute("aria-expanded", "true")
            page.mouse.move(0, 0)
            assert (
                plus.evaluate("node => getComputedStyle(node).backgroundColor")
                != surface_normal_backgrounds[4]
            )
            page.keyboard.press("Escape")
            playwright.expect(plus).to_have_attribute("aria-expanded", "false")

            context.click()
            playwright.expect(context).to_have_attribute("aria-expanded", "true")
            page.mouse.move(0, 0)
            assert (
                context.evaluate("node => getComputedStyle(node).backgroundColor")
                != surface_normal_backgrounds[5]
            )
            context.evaluate("node => node.click()")
            playwright.expect(context).to_have_attribute("aria-expanded", "false")

            effort.click()
            effort_control = effort.locator("xpath=..")
            effort_host = effort_control.locator(".effort-pill-host")
            assert effort_host.get_attribute("data-effort-expanded") == "true"
            assert effort_control.get_attribute("aria-expanded") == "true"
            page.mouse.move(0, 0)
            assert (
                effort.evaluate("node => getComputedStyle(node).backgroundColor")
                != effort_normal_background
            )
            page.evaluate(
                "document.body.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true }))"
            )
            assert effort_host.get_attribute("data-effort-expanded") != "true"

            compact_icon = effort.locator("div").first
            assert compact_icon.count() == 1
            assert compact_icon.evaluate("node => getComputedStyle(node).display") != "none"
            assert re.match(
                r"(rgb|hsl|color)\(",
                compact_icon.evaluate("node => getComputedStyle(node).color"),
            )
        finally:
            page.mouse.move(0, 0)
            page.keyboard.press("Escape")
            page.evaluate(
                "document.body.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true }))"
            )
            if context.get_attribute("aria-expanded") == "true":
                context.evaluate("node => node.click()")
            row.evaluate(
                "(node, value) => value === null "
                "? node.removeAttribute('style') "
                ": node.setAttribute('style', value)",
                original_style,
            )

        assert row.get_attribute("style") == original_style
