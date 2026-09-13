"""Platform commands."""
from __future__ import annotations
from importlib import import_module

# Bind to this workflow package, including an isolated published snapshot.
state = import_module("..", __package__)


def _run_browser_task_commands(
    *, task: str, backend: str,
    max_steps: int | None, max_seconds: float | None, runtime,
) -> dict:
    """Optional GUI Agent Harness over the same public command contract."""
    if runtime is None:
        raise ValueError("browser_agent requires a runtime argument")
    from openprogram.agent import surface_context
    from ..web_use_runtime import get_registry

    def page_unavailable(
        summary: str = "No accessible built-in Page is open.",
        *,
        reason_code: str = "page_unavailable",
        infeasible: bool = False,
        handoff_instruction: str = "",
    ) -> dict:
        return {
            "status": "infeasible" if infeasible else "failed",
            "reason_code": reason_code,
            "summary": summary,
            "handoff_instruction": handoff_instruction or (
                "Launch or reconnect the OpenProgram desktop app, then retry "
                "this GUI task."
                if infeasible else
                "Open or restore a built-in Page, then retry this GUI task."
            ),
            "backend": backend,
        }

    context = surface_context.current()
    captured_here = context is None
    if context is None:
        try:
            context = surface_context.capture_pages()
        except RuntimeError:
            context = surface_context.window_context()
    release_context_on_exit = captured_here
    auto_opened_context: dict[str, state.Any] | None = None
    initial_url = ""

    def release_captured_context() -> None:
        nonlocal release_context_on_exit
        if release_context_on_exit:
            surface_context.release_bindings(context)
            release_context_on_exit = False

    owner_id = "harness:" + str(context.get("context_id") or "unknown")
    registry = get_registry()

    def release_owner() -> None:
        cleanup = getattr(registry, "release_owner", None)
        if callable(cleanup):
            cleanup(owner_id)

    def close_auto_opened_page() -> dict[str, state.Any] | None:
        """Close a Page that was opened but never became a usable target."""
        nonlocal auto_opened_context
        if auto_opened_context is None:
            return None
        opened, auto_opened_context = auto_opened_context, None
        try:
            result = surface_context.close_page(opened)
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        if not isinstance(result, dict) or not result.get("ok"):
            return result if isinstance(result, dict) else {
                "ok": False,
                "error": "desktop app returned an invalid Page close result",
            }
        return None

    def cleanup_failed(
        close_failure: dict[str, state.Any],
        previous: dict[str, state.Any] | None = None,
    ) -> dict:
        previous_reason = str((previous or {}).get("reason_code") or "")
        prior = (
            f" after the GUI task ended with {previous_reason}"
            if previous_reason and previous_reason != "verified" else
            " after the GUI task was verified"
        )
        return page_unavailable(
            "The background Page could not be confirmed closed"
            + prior
            + " ("
            + str(close_failure.get("error") or "close was rejected")
            + ").",
            reason_code="page_cleanup_failed",
            infeasible=True,
            handoff_instruction=(
                "Close the remaining background Page in OpenProgram, "
                "then continue the task manually or retry it."
            ),
        )

    page_inventory: list[dict[str, state.Any]] = []
    page_inventory_snapshot: dict[str, state.Any] = {"pages": page_inventory}
    bound_page_identity = ("", "")

    def refresh_inventory(
    ) -> tuple[list[dict[str, state.Any]], dict[str, state.Any], str]:
        inventory_context = None
        try:
            inventory_context = surface_context.capture_pages(context)
            listed = registry.list_pages(
                context=inventory_context, owner_id=owner_id,
            )
        except Exception as exc:
            if inventory_context is not None and inventory_context is not context:
                with state.suppress(Exception):
                    surface_context.release_bindings(inventory_context)
            return [], {"pages": []}, str(exc) or "Page inventory is unavailable"
        if not isinstance(listed, dict) or not listed.get("ok"):
            if inventory_context is not context:
                with state.suppress(Exception):
                    surface_context.release_bindings(inventory_context)
            return [], {"pages": []}, str(
                (listed or {}).get("reason_code")
                if isinstance(listed, dict) else
                "invalid Page inventory result"
            )
        pages = list(listed.get("pages") or [])
        if not pages and inventory_context is not context:
            with state.suppress(Exception):
                surface_context.release_bindings(inventory_context)
        snapshot = {
            key: listed.get(key)
            for key in (
                "browser_context_id", "window_id", "inventory_revision",
                "active_tab_entry_id", "focused_page", "tab_entries", "windows",
            )
        }
        snapshot["pages"] = pages
        return pages, snapshot, ""

    page_inventory, page_inventory_snapshot, inventory_error = refresh_inventory()
    if (
        not page_inventory
        and inventory_error
        and not surface_context.tool_enabled(context)
    ):
        with state.suppress(Exception):
            release_captured_context()
        with state.suppress(Exception):
            release_owner()
        has_origin_window = bool(
            context.get("origin_window_id") or context.get("window_id")
        )
        return page_unavailable(
            f"The built-in Page inventory could not be read ({inventory_error}).",
            reason_code=(
                "page_context_stale" if has_origin_window
                else "desktop_unavailable"
            ),
            infeasible=not has_origin_window,
        )
    if not page_inventory and not surface_context.tool_enabled(context):
        windows = context.get("windows") or []
        requested_window_id = str(
            context.get("origin_window_id")
            or (
                context.get("window_id")
                if len(windows) <= 1 else ""
            )
            or ""
        )
        opened = surface_context.open_page(
            state.DEFAULT_GUI_BROWSER_START_URL,
            window_id=requested_window_id,
            background=True,
        )
        if opened.get("ok") is False:
            with state.suppress(Exception):
                release_captured_context()
            with state.suppress(Exception):
                release_owner()
            return page_unavailable(
                str(opened.get("error") or "The desktop Page could not be created."),
                reason_code=str(
                    opened.get("reason_code") or "desktop_unavailable"
                ),
                infeasible=True,
                handoff_instruction=str(opened.get("handoff_instruction") or ""),
            )
        with state.suppress(Exception):
            release_captured_context()
        context = opened
        release_context_on_exit = True
        owner_id = "harness:" + str(context.get("context_id") or "unknown")
        initial_url = state.DEFAULT_GUI_BROWSER_START_URL
        auto_opened_context = context
    if not page_inventory and not surface_context.tool_enabled(context):
        close_failure = close_auto_opened_page()
        with state.suppress(Exception):
            release_captured_context()
        with state.suppress(Exception):
            release_owner()
        unavailable = page_unavailable()
        return (
            cleanup_failed(close_failure, unavailable)
            if close_failure is not None else
            unavailable
        )
    try:
        if page_inventory:
            primary = page_inventory[0]
            bound_page_identity = (
                str(primary.get("window_id") or ""),
                str(primary.get("tab_id") or ""),
            )
            if all(bound_page_identity):
                for page in page_inventory:
                    page["bound"] = (
                        page.get("window_id"), page.get("tab_id")
                    ) == bound_page_identity
            observed = registry.execute(
                command="observe", backend=backend, owner_id=owner_id,
                page_context_token=str(primary.get("page_context_token") or ""),
            )
        else:
            token = surface_context.bind(context)
            try:
                binding_id = surface_context.resolve_binding("")
                page_key = surface_context.resolve_page_key("")
            finally:
                surface_context.reset(token)
            observed = registry.execute(
                command="observe", backend=backend, binding_id=binding_id,
                page_key=page_key, owner_id=owner_id, page_context=context,
            )
            if observed.get("web_session_id"):
                # Registry session owns the Page lease; the native Page stays.
                release_context_on_exit = False
    except state._GUI_TASK_ERRORS:
        with state.suppress(Exception):
            release_owner()
        with state.suppress(Exception):
            release_captured_context()
        raise
    session_id = str(observed.get("web_session_id") or "")
    if not session_id or "frame_id" not in observed:
        if session_id:
            with state.suppress(Exception):
                registry.execute(
                    command="close", web_session_id=session_id,
                    owner_id=owner_id,
                )
        with state.suppress(Exception):
            release_owner()
        with state.suppress(Exception):
            release_captured_context()
        return {
            "status": "failed",
            "reason_code": observed.get("reason_code", "page_unavailable"),
            "summary": "The selected Page could not be observed.",
            "backend": backend,
        }

    last: dict[str, state.Any] = {
        "result": None, "action": "", "seq": 0, "screenshot_result": None,
    }

    def dispatch(action: str, **arguments):
        nonlocal observed, session_id, page_inventory, bound_page_identity
        page_context_token = str(arguments.pop("page_context_token", "") or "")
        if action == "switch_page":
            selected_page = next((
                page for page in page_inventory
                if page.get("page_context_token") == page_context_token
            ), None)
            if not page_context_token:
                result = {"ok": False, "reason_code": "page_context_required"}
            else:
                next_observation = registry.execute(
                    command="observe", backend=backend, owner_id=owner_id,
                    page_context_token=page_context_token,
                )
                next_session_id = str(
                    next_observation.get("web_session_id") or ""
                )
                if next_session_id and "frame_id" in next_observation:
                    previous_session_id = session_id
                    session_id = next_session_id
                    observed = next_observation
                    bound_page_identity = (
                        str((selected_page or {}).get("window_id") or ""),
                        str((selected_page or {}).get("tab_id") or ""),
                    )
                    for page in page_inventory:
                        page["bound"] = (
                            all(bound_page_identity)
                            and (page.get("window_id"), page.get("tab_id"))
                            == bound_page_identity
                        )
                    registry.execute(
                        command="close",
                        web_session_id=previous_session_id,
                        owner_id=owner_id,
                    )
                    result = {
                        "ok": True,
                        "switched": True,
                        "web_session_id": session_id,
                        "frame_id": observed.get("frame_id"),
                    }
                else:
                    result = next_observation
            last.update(result=result, action=action, seq=last["seq"] + 1)
            return state._result_for_prompt(result)
        command = "verify" if action == "verify" else "act"
        arguments["action"] = action
        result = registry.execute(
            command=command,
            web_session_id=session_id,
            owner_id=owner_id,
            arguments=arguments,
        )
        last.update(result=result, action=action, seq=last["seq"] + 1)
        if isinstance(result, state.ToolReturn) and result.images:
            last["screenshot_result"] = result
        return state._result_for_prompt(result)

    action_tool = state.function(
        name="browser_page",
        description=(
            "Act on the exact Page observation. Use DOM/ARIA refs by default. "
            "Use screenshot only when refs cannot identify a visual target."
        ),
        parameters=state._GUI_TOOL_PARAMETERS,
        register_globally=False,
    )(dispatch)
    if max_seconds is not None and float(max_seconds) > 0:
        deadline = state.time.monotonic() + max(1, min(int(max_seconds), 1800))
    else:
        deadline = None
    if max_steps is not None and int(max_steps) > 0:
        step_iters = range(min(int(max_steps) * 3 + 3, 303))
    else:
        step_iters = state.itertools.count()
    pending_screenshot = None
    pending_screenshot_result = None
    summary = ""
    missed_tool_calls = 0
    terminal_result: dict[str, state.Any] | None = None
    preserve_primary_exception = False

    def finish(result: dict[str, state.Any]) -> dict[str, state.Any]:
        nonlocal terminal_result
        terminal_result = result
        return result

    try:
        for _ in step_iters:
            timeout_s = None
            if deadline is not None:
                remaining = deadline - state.time.monotonic()
                if remaining <= 0:
                    return finish({
                        "status": "failed", "reason_code": "timeout",
                        "summary": "GUI Agent Harness exceeded its time limit.",
                        "backend": backend, "web_session_id": session_id,
                    })
                timeout_s = max(1, remaining)
            content: list[dict[str, state.Any]] = [{
                "type": "text",
                "text": state._step_prompt(
                    task, initial_url, observed, state._result_for_prompt(last["result"]),
                    page_inventory_snapshot,
                ),
            }]
            sent_screenshot = pending_screenshot is not None
            sent_screenshot_session_id = session_id
            sent_screenshot_result = (
                pending_screenshot_result if sent_screenshot else None
            )
            if pending_screenshot is not None:
                content.append(pending_screenshot)
                pending_screenshot = None
                pending_screenshot_result = None
            seq_before = last["seq"]
            try:
                reply = state.agent(
                    content,
                    tools=[action_tool],
                    tool_choice="auto",
                    parallel_tool_calls=False,
                    max_iterations=1,
                    timeout_s=timeout_s,
                    execution_kind="browser_agent",
                    runtime=runtime,
                    return_raw=True,
                )
            finally:
                if sent_screenshot:
                    try:
                        registry.revoke_screenshot(sent_screenshot_session_id)
                    finally:
                        state._release_screenshot_payload(content, sent_screenshot_result)
                        if last["screenshot_result"] is sent_screenshot_result:
                            last["screenshot_result"] = None
            if isinstance(reply, str) and reply.strip():
                summary = reply.strip()
            if last["seq"] == seq_before:
                missed_tool_calls += 1
                if missed_tool_calls < 2:
                    last["result"] = {
                        "ok": False,
                        "reason_code": "tool_not_executed",
                    }
                    continue
                return finish({
                    "status": "failed",
                    "reason_code": "tool_not_executed",
                    "summary": (
                        "The model did not execute the required browser_page "
                        "tool call."
                    ),
                    "backend": backend,
                    "web_session_id": session_id,
                })
            missed_tool_calls = 0
            result = last["result"]
            if last["action"] == "verify" and isinstance(result, dict) and result.get("passed"):
                return finish({
                    "status": "succeeded", "reason_code": "verified",
                    "summary": summary or "Browser task completed and verified.",
                    "backend": backend, "web_session_id": session_id,
                })
            if last["action"] == "screenshot":
                pending_screenshot_result = result
                pending_screenshot = state._screenshot_image_block(result)
            if isinstance(result, dict) and result.get("observe_required"):
                observed = registry.execute(
                    command="observe", web_session_id=session_id,
                    owner_id=owner_id,
                )
                if "frame_id" not in observed:
                    return finish({
                        "status": "failed",
                        "reason_code": observed.get("reason_code", "page_unavailable"),
                        "summary": "The Page could not be observed after an action.",
                        "backend": backend, "web_session_id": session_id,
                    })
                page_inventory, page_inventory_snapshot, _ = refresh_inventory()
                if all(bound_page_identity):
                    for page in page_inventory:
                        page["bound"] = (
                            page.get("window_id"), page.get("tab_id")
                        ) == bound_page_identity
            elif last["action"] == "wait":
                observed = registry.execute(
                    command="observe", web_session_id=session_id,
                    owner_id=owner_id,
                )
                if "frame_id" not in observed:
                    return finish({
                        "status": "failed",
                        "reason_code": observed.get("reason_code", "page_unavailable"),
                        "summary": "The Page could not be observed after waiting.",
                        "backend": backend, "web_session_id": session_id,
                    })
                page_inventory, page_inventory_snapshot, _ = refresh_inventory()
                if all(bound_page_identity):
                    for page in page_inventory:
                        page["bound"] = (
                            page.get("window_id"), page.get("tab_id")
                        ) == bound_page_identity
        return finish({
            "status": "failed", "reason_code": "verification_missing",
            "summary": summary or "Browser task ended without verification.",
            "backend": backend, "web_session_id": session_id,
        })
    except state._GUI_TASK_ERRORS:
        preserve_primary_exception = True
        raise
    finally:
        screenshot_cleanup_error: Exception | None = None
        try:
            unreleased_screenshot = (
                pending_screenshot_result or last["screenshot_result"]
            )
            if not (
                isinstance(unreleased_screenshot, state.ToolReturn)
                and unreleased_screenshot.images
            ):
                current_result = last["result"]
                unreleased_screenshot = (
                    current_result
                    if isinstance(current_result, state.ToolReturn)
                    and current_result.images
                    else None
                )
            if unreleased_screenshot is not None:
                try:
                    registry.revoke_screenshot(session_id)
                finally:
                    state._release_screenshot_payload([], unreleased_screenshot)
                    last["screenshot_result"] = None
        except Exception as exc:
            screenshot_cleanup_error = exc
        finally:
            preserve_outcome = preserve_primary_exception
            try:
                registry.execute(
                    command="close", web_session_id=session_id,
                    owner_id=owner_id,
                )
            except Exception:
                if not preserve_outcome:
                    raise
            finally:
                try:
                    release_owner()
                except Exception:
                    if not preserve_outcome:
                        raise
                finally:
                    with state.suppress(Exception):
                        release_captured_context()
        if screenshot_cleanup_error is not None and not preserve_outcome:
            raise screenshot_cleanup_error
