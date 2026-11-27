"""JobRunner progress operations."""
from __future__ import annotations
import json
import time
from openprogram.runtime.jobs.runner import shared


def _card_attach(node) -> tuple[dict, dict]:
    """An attach card's ``attach`` dict and its ``extra`` JSON, as copies.
    The top-level ``metadata.attach`` wins; ``extra.attach`` is where older
    cards kept it."""
    md = node.metadata or {}
    extra_raw = md.get("extra")
    try:
        extra_json = json.loads(extra_raw) if isinstance(extra_raw, str) else dict(extra_raw or {})
    except Exception:
        extra_json = {}
    if not isinstance(extra_json, dict):
        extra_json = {}
    return dict(md.get("attach") or extra_json.get("attach") or {}), extra_json


class ProgressOperations:
    def _budget_loop(self) -> None:
        # Nothing here is looked at on a fixed tick. A pass runs when a Job
        # starts, a stop is recorded for one (a ``commands`` commit), a
        # helper's usage is written, or the earliest budget deadline comes;
        # with no Job it sleeps until one starts.
        while not self._shutdown_event.is_set():
            self._budget_wake.clear()
            if self._shutdown_event.is_set():
                break
            with self._lock:
                busy = bool(self._jobs)
            if busy:
                self._budget_tick()
            self._budget_wake.wait(self._budget_timeout() if busy else shared._DISPATCH_RESYNC_SECS)


    def _budget_timeout(self) -> float:
        """Seconds until the next pass: the earliest runtime, idle, stall or
        hard-stop deadline of a running Job, the next spend check a usage
        write asked for, or the resync."""
        resync = shared._DISPATCH_RESYNC_SECS
        poll = self._budget_poll_seconds
        if not getattr(self, "_budget_real_clock", True):
            return poll if poll is not None else resync
        now = self._monotonic()
        due = self._budget_due()
        timeout = resync if due is None else max(0.0, due - now) + 0.01
        if poll is not None:
            timeout = min(timeout, poll)
        return min(timeout, resync)


    def _budget_due(self) -> shared.Optional[float]:
        """The earliest monotonic time a pass has something to do."""
        deadlines: list[float] = []
        with self._lock:
            for entry in self._jobs.values():
                started = entry.get("started_monotonic")
                if started is None or entry.get("budget_cancelled"):
                    continue
                runtime_limit, idle_limit = entry.get("time_limits", (None, None))
                if runtime_limit is not None:
                    deadlines.append(started + float(runtime_limit))
                if idle_limit is not None:
                    deadlines.append(entry.get("last_activity_monotonic", started) + float(idle_limit))
        delegation = self._delegation_due()
        if delegation is not None:
            deadlines.append(delegation)
        return min(deadlines, default=None)


    def _budget_tick(self) -> None:
        """Process one deterministic runtime/idle budget-monitor pass."""
        now = self._monotonic()
        expired: list[tuple[str, str]] = []
        pending_cancels: list[tuple[str, str, int, shared.threading.Event]] = []
        with self._lock:
            for job_id, entry in self._jobs.items():
                attempt_id = entry.get("attempt_id")
                attempt_generation = entry.get("attempt_generation")
                if (
                    attempt_id is not None
                    and isinstance(attempt_generation, int)
                ):
                    pending_cancels.append(
                        (
                            job_id,
                            attempt_id,
                            attempt_generation,
                            entry["event"],
                        )
                    )
                started = entry.get("started_monotonic")
                if started is None or entry.get("budget_cancelled"):
                    continue
                runtime_limit, idle_limit = entry.get(
                    "time_limits", (None, None),
                )
                reason_code = None
                if (
                    runtime_limit is not None
                    and now - started >= float(runtime_limit)
                ):
                    reason_code = "budget.runtime_exhausted"
                elif (
                    idle_limit is not None
                    and now - entry["last_activity_monotonic"] >= float(idle_limit)
                ):
                    reason_code = "budget.idle_exhausted"
                if reason_code is not None:
                    entry["budget_cancelled"] = True
                    expired.append((job_id, reason_code))
        for job_id, attempt_id, generation, cancel_event in pending_cancels:
            self._consume_pending_canonical_cancel(
                job_id, attempt_id, generation, cancel_event,
            )
        for job_id, reason_code in expired:
            try:
                self._cancel_cascade(
                    job_id,
                    reason=reason_code.replace(".", " "),
                    root_reason_code=reason_code,
                )
            except Exception:
                shared._log.exception(
                    "failed to cancel job %s after budget expiry", job_id,
                )
        try:
            self._delegation_tick(now)
        except Exception:
            shared._log.warning("delegation bounds check failed", exc_info=True)


    def _delegation_entries(self) -> list[tuple[str, str, float, float]]:
        with self._lock:
            return [
                (job_id, entry["session_id"], entry["started_monotonic"],
                 entry.get("last_activity_monotonic", entry["started_monotonic"]))
                for job_id, entry in self._jobs.items()
                if entry.get("started_monotonic") is not None
                and not entry.get("budget_cancelled") and entry.get("session_id")
            ]


    def _delegation_due(self) -> shared.Optional[float]:
        """When the delegation bounds next need a look: a Job's stall or
        hard-stop deadline, a spend check a usage write asked for (at least
        CHECK_EVERY_S after the last), or None."""
        from openprogram.runtime.jobs import delegation_watch as watch
        entries = self._delegation_entries()
        if not entries:
            return None
        last = getattr(self, "_delegation_checked_at", None)
        if last is None:
            return self._monotonic()
        if getattr(self, "_delegation_spend_changed", False):
            return last + watch.CHECK_EVERY_S
        memory = getattr(self, "_delegation_memory", None) or {}
        stopped, told = memory.get("stopped", set()), memory.get("stalled", set())
        run_limit, stall_limit = watch.max_run_seconds(), watch.stall_seconds()
        deadlines: list[float] = []
        for job_id, _session, started, last_activity in entries:
            if job_id in stopped:
                continue
            if run_limit:
                deadlines.append(started + run_limit)
            if stall_limit and job_id not in told:
                deadlines.append(last_activity + stall_limit)
        return min(deadlines, default=None)


    def _delegation_tick(self, now: float) -> None:
        """The default bounds on agent Jobs (runtime/jobs/delegation_watch.py):
        hard stop, stall notice, and spend per delegation tree. They read Job
        files and the ledger, so they run only when a Job is new to them, a
        stall or hard-stop deadline came, a helper's usage was written (at
        most every CHECK_EVERY_S), or a resync passed."""
        from openprogram.runtime.jobs import delegation_watch as watch
        last = getattr(self, "_delegation_checked_at", None)
        due = self._delegation_due()
        if last is not None and now - last < shared._DISPATCH_RESYNC_SECS and (due is None or now < due):
            return
        self._delegation_checked_at = now
        self._delegation_spend_changed = False
        entries = self._delegation_entries()
        if not entries:
            return
        memory = getattr(self, "_delegation_memory", None)
        if memory is None:
            memory = self._delegation_memory = {}

        def cancel(job_id: str, reason_code: str) -> None:
            with self._lock:
                entry = self._jobs.get(job_id)
                if entry is not None:
                    entry["budget_cancelled"] = True
            try:
                self._cancel_cascade(
                    job_id, reason=reason_code.replace(".", " "),
                    root_reason_code=reason_code,
                )
            except Exception:
                shared._log.exception("failed to stop job %s (%s)", job_id, reason_code)

        def stalled(job) -> None:
            try:
                from openprogram.runtime.jobs import notices
                notices.queue_stalled(job, minutes=round(watch.stall_seconds() / 60))
            except Exception:
                shared._log.warning("stall notice for job %s not queued", job.id, exc_info=True)

        try:
            watch.check_running(
                entries, now=now, load=shared._store_load, ledger=self._governor.ledger,
                cancel=cancel, stalled=stalled, already=memory,
            )
        except Exception:
            shared._log.warning("delegation bounds check failed", exc_info=True)


    def _wake_done(self, job_id: str) -> None:
        with self._lock:
            ev = self._done_events.get(job_id)
        if ev is not None:
            ev.set()


    def _lookup_or_load(self, job_id: str) -> shared.Optional[shared.Job]:
        """Find the session for this job (via in-memory map) and load
        the entity from disk."""
        sid = self._find_session_for_job(job_id)
        if not sid:
            return None
        return shared._store_load(sid, job_id)


    def _find_session_for_job(self, job_id: str) -> shared.Optional[str]:
        with self._lock:
            info = self._jobs.get(job_id)
        if info:
            return info["session_id"]
        # Canonical executions already record their owning session. In
        # particular, foreground turns have no Job file: do not scan every
        # conversation on disk to establish that fact for each Activity row.
        execution_store = getattr(self, "_execution_store", None)
        if execution_store is not None:
            execution = execution_store.get_execution(job_id)
            if execution is not None:
                found = shared._store_load(execution.session_id, job_id)
                return execution.session_id if found is not None else None
        # Not in memory — scan disk. Jobs always live under the
        # session repo they were spawned for, so a walk is bounded.
        from openprogram.state.store import default_store
        store = default_store()
        if not store.root_path.exists():
            return None
        hits: list[tuple[str, shared.Job]] = []
        from openprogram.state.store.session.files.placement import iter_session_dirs
        for sid, sdir in iter_session_dirs(store.root_path):
            if store.state_file_at(sdir, "jobs.json").exists():
                found = shared._store_load(sid, job_id)
                if found is not None:
                    hits.append((sid, found))
        if not hits:
            return None
        # Prefer the execution home. A linked job is mirrored into the
        # caller session with parent_session_id rewritten; that copy
        # must not win cancel / load over the target that holds the
        # inbox entry.
        for sid, found in hits:
            if found.caller_session_id and found.caller_session_id != sid:
                return sid
        return hits[0][0]


    def _update_attach_card(
        self, job: shared.Job, *, error_text: shared.Optional[str] = None,
    ) -> None:
        """Every path that ends a job comes through here: patch its card,
        archive a branch it created with archive_when_done, then queue the
        notice that tells its sender (agent/job/notices.py). The card goes
        first so the notice turn finds the report in context.
        """
        self._patch_attach_card(job, error_text=error_text)
        if shared.is_terminal(job.status):
            self._finalize_spawn_branch_meta(job)
            try:
                from openprogram.runtime.jobs import notices
                from openprogram.runtime.jobs.store import settle_notice
                notices.queue(job)
                settle_notice(job.parent_session_id, job.id)
            except Exception:
                shared._log.warning("job notice for %s not queued", job.id, exc_info=True)

    def _patch_attach_card(
        self, job: shared.Job, *, error_text: shared.Optional[str] = None,
    ) -> None:
        """Patch the placeholder attach card the spawn path wrote so its
        ``extra.attach`` reflects the final job outcome. Best-effort —
        the attach card pickup path in the existing UI already shows
        ``result.final_text``; this layer adds the job_id linkage
        and status badge.
        """
        if not job.attach_pointer_id:
            return
        try:
            from openprogram.runtime.context.session_db import default_db
            db = default_db()
            target_session = job.parent_session_id
            card_session = job.caller_session_id or target_session
            pair = db._open(card_session)  # noqa: SLF001
            if pair is None:
                return
            _session_log, idx = pair
            node = idx.nodes_by_id.get(job.attach_pointer_id)
            if not node:
                return
            # The fields this runner owns. A synchronous caller settles the
            # same card when its wait ends (write_attach_pointer_for_spawn),
            # so they are merged into the card as it is at the write.
            attach: dict = {}
            attach["session_id"] = target_session
            attach["job_id"] = job.id
            attach["status"] = job.status.value
            if shared.is_terminal(job.status):
                # The result block's usage and changed files, kept on the card
                # for the interface; the caller's prompt is not re-rendered.
                try:
                    from openprogram.runtime.jobs.agent_result import files_of, status_of, usage_of
                    attach["usage"] = usage_of(job, ledger=self._governor.ledger)
                    attach["result_status"], attach["result_reason"] = status_of(job)
                    files = files_of(job)
                    if files:
                        attach["files"] = files
                except Exception:
                    shared._log.debug("usage of job %s not stored", job.id, exc_info=True)
            execution = self._execution_store.get_execution(job.id)
            if execution is not None:
                attach["execution_id"] = execution.execution_id
                attach["status_version"] = execution.status_version
            if job.head_id:
                attach["head_id"] = job.head_id
            # The human name of the sub-agent ("后端架构"). It lives on the
            # Job, and the attach node is the only thing the graph wire and
            # the transcript both read — without it here every reader falls
            # back to a hex id and the branch has no identity anywhere.
            if job.label or job.subject:
                attach["label"] = job.label or job.subject
            # When the job completes, fill source_commit_id from the
            # ContextCommit that ended up on its branch. The existing
            # _run_spawn does this in synchronous mode — we mirror.
            source_commit_id = None
            if job.head_id and not _card_attach(node)[0].get("source_commit_id"):
                try:
                    from openprogram.state.store.session.context_commit.store import (
                        load_commit_for_head,
                    )
                    src = load_commit_for_head(
                        db, target_session, job.head_id,
                    )
                    if src is not None:
                        source_commit_id = src.id
                except Exception:
                    # The attach still records the session; only the commit link is missing.
                    shared._log.debug("source commit not found for job %s", job.id, exc_info=True)
            # Stamp the spawned branch's tip with the human label so
            # the Branches panel and DAG figure show "fox-research"
            # instead of the chain-tail fallback name (which picked
            # up the prompt text or assistant reply as a stand-in).
            # run_agent_turn does this too, but the call has slipped
            # through under specific paths — set it here as well so
            # every job → attach finalization guarantees the name.
            # A name given meanwhile (a rename, an automatic label) stays.
            if job.label and job.head_id:
                try:
                    db.name_spawned_branch(
                        target_session,
                        job.head_id,
                        job.label,
                    )
                except Exception:
                    # The branch keeps its fallback name.
                    shared._log.warning("branch name not set for job %s", job.id, exc_info=True)
            # Hide the spawned sub-branch from the Branches panel
            # once the job completes successfully. Same idea as
            # merge: the sub-agent's content is now reachable from
            # main via the attach pointer, so the standalone branch
            # tip is redundant in the panel. DAG nodes stay
            # intact — a user can still checkout to revisit the
            # sub-agent's history. Only retire on COMPLETED;
            # errored / cancelled jobs remain visible so the user
            # can see what failed.
            # A /spawn the user waited on keeps its branch listed.
            keeps_branch = job.wait and not _card_attach(node)[0].get("tool_call_id")
            if job.head_id and job.status == shared.JobStatus.COMPLETED and not keeps_branch:
                try:
                    db.mark_merged(target_session, [job.head_id])
                except Exception:
                    # The sub-branch stays visible in the Branches panel.
                    shared._log.warning("branch of job %s not marked merged", job.id, exc_info=True)
            # Update the persisted node's metadata + output text, merged
            # into the card as it is now.
            def settle(current) -> dict:
                card, extra_json = _card_attach(current)
                card.update(attach)
                if source_commit_id and not card.get("source_commit_id"):
                    card["source_commit_id"] = source_commit_id
                extra_json["attach"] = card
                return {
                    "output": job.result_text or error_text or job.error or current.output or "",
                    # The same attach dict goes on the top-level
                    # ``metadata.attach`` field, which readers check first,
                    # and into ``extra``, the older place for it.
                    "metadata": {"extra": json.dumps(extra_json, default=str), "attach": card},
                }

            try:
                from openprogram.state.store import SessionNodeWriter
                SessionNodeWriter(db, card_session).merge(job.attach_pointer_id, settle)
            except Exception:
                # The context refresh below still runs.
                shared._log.warning("attach card %s not updated for job %s", job.attach_pointer_id, job.id, exc_info=True)
            # The attach node just changed what the session's graph holds.
            # The transcript publisher sends the card's change to every view
            # holding it; the context ring re-estimates here, at the write.
            shared._refresh_context_stats(card_session)
        except Exception:
            # Card update is secondary to the job's own terminal status.
            shared._log.warning("attach card not finalized for job %s", job.id, exc_info=True)


    def _finalize_spawn_branch_meta(self, job: shared.Job) -> None:
        """Terminal-state meta for a branch this job CREATED.

        Only the agent tool's spawn form sets ``archive_when_done``
        (deliveries to existing branches leave it False) — nothing here
        runs for them. Archiving stops further send_message / agent(to=)
        deliveries to the branch and keeps its history.
        Best-effort: failures are logged and swallowed.
        """
        if not job.archive_when_done or not job.head_id:
            return
        try:
            from openprogram.runtime.context.session_db import default_db
            db = default_db()
            # A branch archived first by hand keeps its own archived_at.
            if db.get_branch_meta(job.parent_session_id, job.head_id).get("archived"):
                return
            db.set_branch_meta(
                job.parent_session_id, job.head_id,
                archived=True, archived_at=time.time(),
            )
        except Exception:
            shared._log.debug(
                "spawn branch meta finalize failed for job %s",
                job.id, exc_info=True,
            )


    def _dispatch_followup(self, job: shared.Job, *more: shared.Job) -> None:
        """Start the turn that tells the sender its background job(s)
        ended (agent/job/notices.py decides when). Several jobs that
        finished while the sender was busy share one turn.

        A spawn's attach pointer lives in the chain already, so the next
        turn's context-commit generator puts the sub-agent's report in
        context. A delivery to an existing branch writes no pointer, so
        its reply travels inline in the notice — see ``notice_text``.

        **Anchoring** (dag/overview.md §4): the notice lands at the
        delivery session's HEAD *at injection time* and advances it, so
        N sub-agents finishing produce one serial chain
        ``… → notice₁ → answer₁ → notice₂ → answer₂``. Anchoring at the
        spawning node instead made every notice a sibling of the same
        turn, and one user message got answered N times on N parallel
        branches.

        Runs on a daemon thread so the caller doesn't block, and holds
        the delivery session's follow-up lock so two notices still
        append in sequence rather than both reading the same HEAD.
        """
        if not job.parent_session_id:
            return
        jobs = [job, *more]
        from openprogram.runtime.jobs.notices import deliver_session_of, notice_items, notice_text
        # Deliver back to the INITIATOR's session. Same-session spawn:
        # caller_session_id is None → parent_session_id. Cross-session
        # send_message: caller_session_id (the sender), NOT the target
        # session the job ran in.
        deliver_session = deliver_session_of(job)
        # The budget counts follow the furthest job: replying to a batch
        # costs what its longest chain already spent.
        chain_messages = max(int(j.chain_messages or 0) for j in jobs)
        chain_generations = max(int(j.caller_chain_generations or 0) for j in jobs)

        def _go():
            try:
                from openprogram.runtime.agent.turn_request import TurnRequest
                from openprogram.runtime import conversation_link
                from openprogram.state.authority import runtime_authority
                from openprogram.state.memory.policy import source_writable
                memory_override = None if all(
                    source_writable(j.parent_session_id, j.head_id) for j in jobs
                ) else {"mode": "read_only"}
                marks = [(self._execution_store.get_job_agent_input(j.id) or {}).get("turn_request") or {} for j in jobs]
                # The result returns to the branch of the turn that started
                # the job, even when the person selected another meanwhile.
                caller_branch = conversation_link.branch_holding(deliver_session, job.caller_msg_id or job.parent_msg_id)
                req = TurnRequest(
                    **({"branch_id": caller_branch} if caller_branch else {}),
                    session_id=deliver_session,
                    automation=next((mark["automation"] for mark in marks if mark.get("automation")), None),
                    detached=any(mark.get("detached") for mark in marks),
                    memory_policy_override=memory_override,
                    user_text=notice_text(jobs),
                    notice=notice_items(jobs),
                    agent_id=job.agent_id or "main",
                    source="job_followup",
                    **conversation_link.conversation_permission(deliver_session),
                    **runtime_authority(job, "job_followup"),
                    # branch_from is left at INHERIT_PARENT: the dispatcher
                    # resolves it to the tip of the turn's branch (the
                    # delivery session's HEAD while that branch is selected)
                    # and advances it, which is exactly the serial chain
                    # this method's docstring describes.
                )
                from openprogram.runtime.events import emit_ws_frame
                # The notice turn streams to the open chat like any turn.
                adapter = conversation_link.agent_adapter(event_sink=emit_ws_frame)
                admission = adapter.admit(
                    req,
                    trusted_actor=runtime_authority(job, "job_followup"),
                    user_message_id=req.user_msg_id,
                    config_snapshot_ref=f"job-followup:{job.id}",
                )
                shared.asyncio.run(adapter.activate(admission))
            except Exception:
                # Best-effort — don't blow up if the caller session is
                # gone or the dispatcher errors.
                shared._log.warning("job notice turn failed for %s", deliver_session, exc_info=True)
            finally:
                # An answer held for these replies (a message this
                # conversation passed on, notices.hold) goes on now.
                from openprogram.runtime.jobs.notices import answered
                try:
                    answered(deliver_session, [j.id for j in jobs])
                except Exception:
                    shared._log.warning("answered replies not recorded for %s", deliver_session, exc_info=True)

        def _serial():
            # A fresh thread starts with empty ContextVars, so the chain
            # state this turn belongs to has to be re-bound by hand or the
            # follow-up looks like a brand-new chain: the message budget
            # would restart at 0 (A↔B ping-pong could never exhaust it)
            # and jobs spawned here would record no parent, escaping the
            # cascade in cancel_execution.
            from openprogram.runtime.jobs.chain_budgets import (
                set_chain_generations, set_chain_messages,
            )
            # The reply hop costs what the child already spent — an
            # explicit send_message reply lands at the same count.
            # Generations are the dispatcher's, not the child's: this
            # turn is the dispatcher reading a result, and reading a
            # result creates nobody. Binding the child's count instead
            # left an agent that read one worker's reply unable to
            # create any further agent in that chain, which is exactly
            # the "dispatch a batch, read it, dispatch the next batch"
            # shape the whole tool exists for.
            set_chain_messages(chain_messages)
            set_chain_generations(chain_generations)
            # The follow-up continues the DISPATCHER's work, not the
            # finished job's, so it chains where the job did. None for a
            # job spawned from a plain user turn, which had no job either.
            shared._current_job_id.set(job.parent_job_id)
            # One follow-up at a time per delivery session: the next one
            # reads a HEAD that already includes the previous answer.
            with self._followup_lock(deliver_session):
                _go()

        shared.threading.Thread(target=_serial, daemon=True).start()

