"""JobRunner progress operations."""
from __future__ import annotations
from . import shared


class ProgressOperations:
    def _budget_loop(self) -> None:
        while not self._shutdown_event.wait(self._budget_poll_seconds):
            self._budget_tick()


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


    def _wake_done(self, job_id: str) -> None:
        with self._lock:
            ev = self._done_events.get(job_id)
        if ev is not None:
            try:
                ev.set()
            except Exception:
                pass


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
        # Not in memory — scan disk. Jobs always live under the
        # session repo they were spawned for, so a walk is bounded.
        from openprogram.store import default_store
        store = default_store()
        if not store.root_path.exists():
            return None
        hits: list[tuple[str, shared.Job]] = []
        for sdir in sorted(store.root_path.iterdir()):
            if not sdir.is_dir():
                continue
            if (sdir / "jobs.json").exists():
                found = shared._store_load(sdir.name, job_id)
                if found is not None:
                    hits.append((sdir.name, found))
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


    def _poll_progress(
        self, job: shared.Job, stop_ev: shared.threading.Event,
    ) -> None:
        """Watch the session for sub-agent messages while the job is
        running and stream the latest message preview into the
        placeholder attach card so the chat row reflects progress
        instead of a static "(running)".

        Best-effort and idle-safe: snapshots the current high-water
        seq as the baseline, then every ~1.5s scans for new nodes
        past that mark. The latest text-bearing node's output (first
        ~300 chars) becomes the attach pointer's preview. Skips itself
        (the placeholder) and runtime-display rows. Broadcasts a
        session reload so the chat view refreshes without polling.
        """
        if not job.attach_pointer_id or not job.parent_session_id:
            return
        try:
            from openprogram.agent.session_db import default_db
            from openprogram.store import SessionNodeWriter
            db = default_db()
            target_session = job.parent_session_id
            card_session = job.caller_session_id or target_session
            target_pair = db._open(target_session)  # noqa: SLF001
            card_pair = db._open(card_session)  # noqa: SLF001
            if target_pair is None or card_pair is None:
                return
            _git, idx = target_pair
            try:
                baseline_seq = max(
                    (n.seq for n in idx.all_nodes() if n.seq is not None),
                    default=-1,
                )
            except Exception:
                baseline_seq = -1
            last_patched_id: shared.Optional[str] = None
            shim = SessionNodeWriter(db, card_session)
        except Exception:
            return
        while not stop_ev.is_set():
            if stop_ev.wait(1.5):
                break
            try:
                pair2 = db._open(target_session)  # noqa: SLF001
                card_pair2 = db._open(card_session)  # noqa: SLF001
                if pair2 is None or card_pair2 is None:
                    continue
                _, idx2 = pair2
                latest = None
                for n in idx2.all_nodes():
                    if (n.seq or 0) <= baseline_seq:
                        continue
                    if n.id == job.attach_pointer_id:
                        continue
                    md = n.metadata or {}
                    if md.get("display") == "runtime":
                        continue
                    if not (n.output or "").strip():
                        continue
                    latest = n
                if latest is None or latest.id == last_patched_id:
                    continue
                preview = str(latest.output or "").strip()
                if not preview:
                    continue
                if len(preview) > 600:
                    preview = preview[:600].rstrip() + "…"
                node = card_pair2[1].nodes_by_id.get(job.attach_pointer_id)
                if not node:
                    continue
                shim.update(job.attach_pointer_id, output=preview)
                last_patched_id = latest.id
                self.record_job_activity(job.id, "child_progress")
                try:
                    shared._broadcast_session_reload(
                        card_session, reason="job_progress",
                    )
                except Exception:
                    pass
            except Exception:
                pass


    def _update_attach_card(
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
            from openprogram.agent.session_db import default_db
            db = default_db()
            target_session = job.parent_session_id
            card_session = job.caller_session_id or target_session
            pair = db._open(card_session)  # noqa: SLF001
            if pair is None:
                return
            _git, idx = pair
            node = idx.nodes_by_id.get(job.attach_pointer_id)
            if not node:
                return
            md = dict(node.metadata or {})
            extra_raw = md.get("extra")
            try:
                extra_json = shared.json.loads(extra_raw) if isinstance(extra_raw, str) else (extra_raw or {})
            except Exception:
                extra_json = {}
            attach = dict(
                md.get("attach") or extra_json.get("attach") or {}
            )
            attach["session_id"] = target_session
            attach["job_id"] = job.id
            attach["status"] = job.status.value
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
            if job.head_id and not attach.get("source_commit_id"):
                try:
                    from openprogram.context.commit.store import (
                        load_commit_for_head,
                    )
                    src = load_commit_for_head(
                        db, target_session, job.head_id,
                    )
                    if src is not None:
                        attach["source_commit_id"] = src.id
                except Exception:
                    pass
            extra_json["attach"] = attach
            md["extra"] = shared.json.dumps(extra_json, default=str)
            # Mirror the same attach dict onto the top-level
            # ``metadata.attach`` field. The frontend's _readAttach
            # helper checks the top-level field first (set by the
            # spawn path) and only falls back to extra-json; if we
            # only patch extra, the panel keeps showing the stale
            # "running" status long after the job completes.
            md["attach"] = attach

            # Stamp the spawned branch's tip with the human label so
            # the Branches panel and DAG figure show "fox-research"
            # instead of the chain-tail fallback name (which picked
            # up the prompt text or assistant reply as a stand-in).
            # run_agent_turn does this too, but the call has slipped
            # through under specific paths — set it here as well so
            # every job → attach finalization guarantees the name.
            if job.label and job.head_id:
                try:
                    db.set_branch_name(
                        target_session,
                        job.head_id,
                        job.label,
                    )
                except Exception:
                    pass
            # Hide the spawned sub-branch from the Branches panel
            # once the job completes successfully. Same idea as
            # merge: the sub-agent's content is now reachable from
            # main via the attach pointer, so the standalone branch
            # tip is redundant in the panel. DAG nodes stay
            # intact — a user can still checkout to revisit the
            # sub-agent's history. Only retire on COMPLETED;
            # errored / cancelled jobs remain visible so the user
            # can see what failed.
            if job.head_id and job.status == shared.JobStatus.COMPLETED:
                try:
                    db.mark_merged(target_session, [job.head_id])
                except Exception:
                    pass
            # Update the persisted node's metadata + output text.
            output = (
                job.result_text or error_text or job.error or node.output or ""
            )
            try:
                from openprogram.store import SessionNodeWriter
                shim = SessionNodeWriter(db, card_session)
                shim.update(
                    job.attach_pointer_id,
                    output=output,
                    metadata=md,
                )
            except Exception:
                pass
            # The attach node just changed what the session's graph holds,
            # so both readers of that graph are told at the same point:
            # the DAG re-pulls the session, and the context ring
            # re-estimates. Firing here, at the write, is what keeps a
            # sub-agent finishing from leaving a stale graph on screen.
            shared._broadcast_session_reload(
                card_session, reason="job_attach",
            )
            shared._refresh_context_stats(card_session)
        except Exception:
            pass


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
            from openprogram.agent.session_db import default_db
            default_db().set_branch_meta(
                job.parent_session_id, job.head_id,
                archived=True, archived_at=shared.time.time(),
            )
        except Exception:
            shared._log.debug(
                "spawn branch meta finalize failed for job %s",
                job.id, exc_info=True,
            )


    def _dispatch_followup(self, job: shared.Job) -> None:
        """Auto-followup: async job finished, nobody's listening on
        the caller session — fire a synthetic user-role turn that
        prompts the parent agent to react to the result.

        A spawn's attach pointer lives in the chain already, so the next
        turn's context-commit generator expands it as
        ``[Attached from branch "X"]:`` items and the LLM sees the
        sub-agent's output naturally. A delivery to an existing branch
        writes no pointer, so its reply travels inline in the
        notification — see ``inline_reply`` below.

        **Anchoring** (dag/overview.md §4): the notification lands at the
        delivery session's HEAD *at injection time* and advances it, so
        N sub-agents finishing produce one serial chain
        ``… → notice₁ → answer₁ → notice₂ → answer₂``. Anchoring at the
        spawning node instead — which is what this used to do — made
        every notification a sibling of the same turn, and one user
        message got answered N times on N parallel branches.

        Runs on a daemon thread so the runner worker doesn't block, and
        holds the delivery session's follow-up lock so two sub-agents
        finishing together still append in sequence rather than both
        reading the same HEAD.
        """
        if not job.parent_session_id:
            return
        label = job.label or job.subject or job.id[:8]
        sub_prompt = (job.prompt or job.description or "").strip()
        # Deliver the reply back to the INITIATOR's session. Same-session
        # spawn: caller_session_id is None → deliver to parent_session_id.
        # Cross-session send_message: deliver to caller_session_id (the
        # sender), NOT the target session the job ran in.
        deliver_session = job.caller_session_id or job.parent_session_id
        # Carry the reply INLINE only when the initiator has no attach pointer
        # to expand. A cross-session spawn persists its pointer in the caller
        # session; a delivery to an EXISTING branch (``agent(to=…)`` or
        # ``send_message``) creates none because it spawns nothing to attach.
        inline_reply = not job.attach_pointer_id

        def _go():
            try:
                from openprogram.agent.dispatcher import TurnRequest
                from openprogram.agent.production_driver import CanonicalAgentAdapter
                from openprogram.agent.authority import runtime_authority
                # Followup prompt — push the parent agent to synthesize a
                # reply, not echo the sub-agent's last line. With an attach
                # pointer the sub-agent transcript is already in context via
                # the attach expansion; without one the reply text has to
                # travel inline.
                sub_request_line = (
                    f"用户原本让子 agent 做的事是：{sub_prompt}\n"
                    if sub_prompt else ""
                )
                reply_block = ""
                if inline_reply:
                    reply_text = (job.result_text or "").strip() or "(无输出)"
                    reply_block = (
                        f"分支 {job.parent_session_id}:"
                        f"{job.head_id or '?'} 的回复是：\n{reply_text}\n\n"
                    )
                if inline_reply:
                    followup_text = (
                        f"[系统消息] 你之前发消息给的另一个分支 \"{label}\" "
                        f"回复了。\n{sub_request_line}{reply_block}"
                        f"请基于这条回复继续——做总结、解读，或决定下一步"
                        f"（继续追问可再调 send_message）。"
                    )
                else:
                    followup_text = (
                        f"[系统消息] 你派发的子 agent \"{label}\" "
                        f"已经跑完了，它完整的对话记录作为附加内容嵌在上面。\n"
                        f"{sub_request_line}"
                        f"现在请你直接面向原始用户给出完整回答，"
                        f"基于子 agent 跑出来的结果做总结、解读、给"
                        f"出后续建议。不要原样复读子 agent 的最后"
                        f"一句话。如果子 agent 的输出已经直接回答"
                        f"了用户问题，用你自己的话重新组织一遍，"
                        f"并补充必要的背景或上下文。"
                    )
                req = TurnRequest(
                    session_id=deliver_session,
                    user_text=followup_text,
                    agent_id=job.agent_id or "main",
                    source="job_followup",
                    **runtime_authority(job, "job_followup"),
                    # branch_from is left at INHERIT_PARENT: the dispatcher
                    # resolves it to the delivery session's HEAD and advances
                    # it, which is exactly the serial chain this method's
                    # docstring describes. Pinning it to the spawning node
                    # is what produced the parallel-branch double answer.
                )
                adapter = CanonicalAgentAdapter()
                admission = adapter.admit(
                    req,
                    trusted_actor=runtime_authority(job, "job_followup"),
                    user_message_id=req.user_msg_id,
                    config_snapshot_ref=f"job-followup:{job.id}",
                )
                shared.asyncio.run(adapter.activate(admission))
            except Exception:
                # Best-effort — don't blow up the runner if the
                # caller session is gone / dispatcher errors.
                pass

        def _serial():
            # A fresh thread starts with empty ContextVars, so the chain
            # state this turn belongs to has to be re-bound by hand or the
            # follow-up looks like a brand-new chain: the message budget
            # would restart at 0 (A↔B ping-pong could never exhaust it)
            # and jobs spawned here would record no parent, escaping the
            # cascade in cancel_execution.
            from openprogram.programs.tools.agents.send_message.send_message.depth import (
                set_chain_generations, set_chain_messages,
            )
            # The reply hop costs what the child already spent — an
            # explicit send_message reply lands at the same count.
            set_chain_messages(int(job.chain_messages or 0))
            # Generations are the dispatcher's, not the child's: this
            # turn is the dispatcher reading a result, and reading a
            # result creates nobody. Binding the child's count instead
            # left an agent that read one worker's reply unable to
            # create any further agent in that chain, which is exactly
            # the "dispatch a batch, read it, dispatch the next batch"
            # shape the whole tool exists for.
            set_chain_generations(int(job.caller_chain_generations or 0))
            # The follow-up continues the DISPATCHER's work, not the
            # finished job's, so it chains where the job did. None for a
            # job spawned from a plain user turn, which had no job either.
            shared._current_job_id.set(job.parent_job_id)
            # One follow-up at a time per delivery session: the next one
            # reads a HEAD that already includes the previous answer.
            with self._followup_lock(deliver_session):
                _go()

        shared.threading.Thread(target=_serial, daemon=True).start()

