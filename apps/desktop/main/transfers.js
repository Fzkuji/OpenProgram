// Desktop transfers. State is owned by the main-process composition.
function createTransfers({
  COMMIT_DECISION_RETRY_LIMIT,
  COMMIT_RECONCILE_INITIAL_MS,
  COMMIT_RECONCILE_MAX_MS,
  DESTINATION_UNDO_TIMEOUT_MS,
  TRANSFER_TIMEOUT_MS,
  ackTransferDecision,
  centerHiddenWindowOnCursor,
  clearActionCue,
  clearTimeout,
  closeActionCueWindow,
  closeControlOverlay,
  createWindow,
  crypto,
  loadTransferDecisions,
  putTransferDecision,
  requestPipZoomRestore,
  saveTransferDecisionsAtomic,
  setTimeout,
  showWindowSmoothly,
  transferDecisionFile,
  validateTransferPayload,
  windows,
}) {
  function reparentRecords(source, target, records) {
    if (!source || !target || source === target || !Array.isArray(records)) {
      throw new TypeError("Invalid native reparent request");
    }
    for (const record of records) {
      if (
        !record
        || record.ownerId !== source.id
        || source.views.get(record.id) !== record
        || target.views.has(record.id)
      ) {
        throw new Error("Native record ownership changed before reparent");
      }
    }

    const snapshots = [];
    snapshots.sourceVisibleViewIds = [...source.visibleViewIds];
    snapshots.targetVisibleViewIds = [...target.visibleViewIds];
    try {
      for (const record of records) {
        clearActionCue(record, true);
        closeActionCueWindow(record);
        closeControlOverlay(record);
        const snapshot = {
          record,
          sourceId: source.id,
          bounds: { ...record.view.getBounds() },
          visible: source.visibleViewIds.has(record.id),
        };
        snapshots.push(snapshot);
        source.win.contentView.removeChildView(record.view);
        source.views.delete(record.id);
        source.visibleViewIds.delete(record.id);
        target.win.contentView.addChildView(record.view);
        target.views.set(record.id, record);
        target.visibleViewIds.delete(record.id);
        record.ownerId = target.id;
        record.view.setVisible(false);
      }
      return snapshots;
    } catch (error) {
      restoreRecords(source, target, snapshots);
      throw error;
    }
  }

  function restoreRecords(source, target, snapshots) {
    if (!source || !target || !Array.isArray(snapshots)) return false;
    for (const snapshot of [...snapshots].reverse()) {
      const { record } = snapshot;
      target.visibleViewIds.delete(record.id);
      if (target.views.get(record.id) === record) target.views.delete(record.id);
      try {
        target.win.contentView.removeChildView(record.view);
      } catch (_error) {
        /* destination may already be destroyed */
      }
      try {
        source.win.contentView.addChildView(record.view);
      } catch (_error) {
        /* a destroyed source has no native surface to restore */
      }
      source.views.set(record.id, record);
      record.ownerId = source.id;
      record.view.setBounds(snapshot.bounds);
      record.view.setVisible(snapshot.visible);
      if (snapshot.visible) source.visibleViewIds.add(record.id);
      else source.visibleViewIds.delete(record.id);
    }
    if (Array.isArray(snapshots.sourceVisibleViewIds)) {
      source.visibleViewIds = new Set(snapshots.sourceVisibleViewIds);
    }
    if (Array.isArray(snapshots.targetVisibleViewIds)) {
      target.visibleViewIds = new Set(snapshots.targetVisibleViewIds);
    }
    return true;
  }

  function makeTransferCoordinator(options = {}) {
    const windowRegistry = options.windows || windows;
    const decisionPath = options.decisionFilePath || transferDecisionFile;
    const createDetachedWindow = options.createWindow || createWindow;
    const setTimer = options.setTimer || setTimeout;
    const clearTimer = options.clearTimer || clearTimeout;
    const now = options.now || (() => Date.now());
    const makeToken = options.makeToken || (() => crypto.randomUUID());
    const activeTransfers = new Map();
    const lockedRecords = new Map();
    const orphanAssignments = new Map();
    const forcedOrphanRoles = new Set();
    const allowedWindowCloses = new Set();
    let durableDecisions = loadTransferDecisions(
      typeof decisionPath === "function" ? decisionPath() : decisionPath,
    );

    const storePath = () =>
      typeof decisionPath === "function" ? decisionPath() : decisionPath;
    const roleKey = (token, role, windowId) =>
      JSON.stringify([token, role, windowId]);
    const isLive = (ctx) => !!ctx && !ctx.win.isDestroyed();
    const liveContext = (id) => {
      const ctx = windowRegistry.get(id);
      return isLive(ctx) ? ctx : null;
    };
    const send = (ctx, channel, payload) => {
      if (!isLive(ctx)) return false;
      ctx.win.webContents.send(channel, payload);
      return true;
    };
    const refreshDecisions = () => {
      durableDecisions = loadTransferDecisions(storePath());
      return durableDecisions;
    };
    const terminalDecision = (token) =>
      refreshDecisions().decisions[token] || null;

    function deleteDecision(token) {
      const store = refreshDecisions();
      if (!store.decisions[token]) return false;
      delete store.decisions[token];
      saveTransferDecisionsAtomic(storePath(), store);
      durableDecisions = store;
      return true;
    }

    function persistDecision(transaction, status, finalizedRoles = []) {
      return putTransferDecision(storePath(), {
        token: transaction.token,
        status,
        sourceId: transaction.sourceId,
        destinationId: transaction.destinationId,
        sourceEmpty: !!transaction.sourceEmpty,
        discardDestinationState:
          transaction.detachedWindowId === transaction.destinationId,
        requiredRoles: [...transaction.journalRoles.values()],
        finalizedRoles,
        decidedAt: now(),
      });
    }

    function unlock(transaction) {
      const restoreZoom = transaction.status === "committed";
      for (const id of transaction.lockedRecordIds) {
        if (lockedRecords.get(id) === transaction.token) lockedRecords.delete(id);
      }
      transaction.lockedRecordIds.clear();
      for (const record of transaction.records) {
        if (!record.pendingTransferZoomRestore) continue;
        record.pendingTransferZoomRestore = false;
        if (restoreZoom) requestPipZoomRestore(record);
      }
    }

    /** Destroy a staged tear-off window (rollback / rejected commit). Takes the
     *  id rather than the transaction so a caller can unlink it FIRST — `closed`
     *  fires synchronously and contextDestroyed must not still see this window as
     *  the transaction's destination. */
    function closeDetached(detachedWindowId) {
      if (!detachedWindowId) return;
      const destination = windowRegistry.get(detachedWindowId);
      if (!isLive(destination)) return;
      destination.pendingTransferToken = null;
      allowedWindowCloses.add(destination.id);
      try {
        destination.win.close();
      } finally {
        allowedWindowCloses.delete(destination.id);
      }
    }

    function clearActive(transaction, { closeHidden = false } = {}) {
      if (transaction.timer !== null) clearTimer(transaction.timer);
      if (transaction.undoTimer !== null) clearTimer(transaction.undoTimer);
      if (transaction.commitRetryTimer !== null) {
        clearTimer(transaction.commitRetryTimer);
      }
      transaction.timer = null;
      transaction.undoTimer = null;
      transaction.commitRetryTimer = null;
      if (activeTransfers.get(transaction.token) === transaction) {
        activeTransfers.delete(transaction.token);
      }
      unlock(transaction);
      const destination = transaction.destinationId
        ? windowRegistry.get(transaction.destinationId)
        : null;
      if (destination?.pendingTransferToken === transaction.token) {
        destination.pendingTransferToken = null;
      }
      if (closeHidden) closeDetached(transaction.detachedWindowId);
    }

    function notifyTerminal(transaction, status) {
      const receipt = {
        token: transaction.token,
        status,
        sourceId: transaction.sourceId,
        destinationId: transaction.destinationId,
      };
      send(liveContext(transaction.destinationId), `tab-transfer:${status}`, receipt);
      send(liveContext(transaction.sourceId), `tab-transfer:${status}`, receipt);
    }

    function assignmentFor(token, role, windowId) {
      return orphanAssignments.get(roleKey(token, role, windowId)) || null;
    }

    function chooseOrphanWorker(decision, ownerWindowId) {
      for (const id of [decision.sourceId, decision.destinationId]) {
        if (id && id !== ownerWindowId) {
          const candidate = liveContext(id);
          if (candidate) return candidate;
        }
      }
      for (const candidate of windowRegistry.values()) {
        if (candidate.id !== ownerWindowId && isLive(candidate)) return candidate;
      }
      return null;
    }

    function assignOrphanedRoles(decision, forced = new Set()) {
      if (!decision) return;
      const finalized = new Set(
        decision.finalizedRoles.map((item) => roleKey(decision.token, item.role, item.windowId)),
      );
      for (const required of decision.requiredRoles) {
        const key = roleKey(decision.token, required.role, required.windowId);
        if (forced.has(key)) forcedOrphanRoles.add(key);
        if (finalized.has(key)) {
          forcedOrphanRoles.delete(key);
          continue;
        }
        if (!forcedOrphanRoles.has(key) && liveContext(required.windowId)) {
          continue;
        }
        let workerId = orphanAssignments.get(key);
        const assignedWorker = liveContext(workerId);
        if (assignedWorker) continue;
        const candidate = chooseOrphanWorker(decision, required.windowId);
        workerId = candidate?.id || null;
        if (workerId) orphanAssignments.set(key, workerId);
        const worker = liveContext(workerId);
        if (worker) {
          send(worker, "tab-transfer:finalize-orphaned", {
            token: decision.token,
            status: decision.status,
            role: required.role,
            windowId: required.windowId,
            orphaned: true,
            ...(decision.discardDestinationState
              && required.role === "destination"
              && required.windowId === decision.destinationId
              ? { discardWindowState: true }
              : {}),
          });
        }
      }
    }

    function expire(token) {
      const transaction = activeTransfers.get(token);
      if (!transaction) return false;
      if (
        transaction.status === "committing"
        && (transaction.commitRetryTimer !== null
          || transaction.commitAttemptsLeft > 0)
      ) {
        // A commit-decision retry is still pending; the timeout must not
        // roll back a transfer whose source already removed its tabs.
        return false;
      }
      if (transaction.status === "prepared" && transaction.journalRoles.size === 0) {
        clearActive(transaction, { closeHidden: true });
        send(liveContext(transaction.sourceId), "tab-transfer:rejected", {
          token,
          reason: "expired",
        });
        return true;
      }
      return beginRollback(transaction, "expired");
    }

    function prepare(ctx, payloadValue) {
      if (!isLive(ctx)) return null;
      let validated;
      try {
        validated = validateTransferPayload(ctx, payloadValue);
      } catch (_error) {
        return null;
      }
      const token = makeToken();
      const transaction = {
        token,
        sourceId: ctx.id,
        destinationId: null,
        inspectedBy: null,
        payload: validated.payload,
        records: validated.records,
        recordSnapshots: [],
        lockedRecordIds: new Set(),
        journalRoles: new Map(),
        status: "prepared",
        timer: null,
        undoTimer: null,
        detachedWindowId: null,
        /** In-flight window boot, so concurrent detach() calls share one. */
        detachPromise: null,
        placement: null,
        sourceEmpty: false,
        commitIndeterminate: false,
        commitRetryTimer: null,
        commitRetryDelay: COMMIT_RECONCILE_INITIAL_MS,
        commitAttemptsLeft: COMMIT_DECISION_RETRY_LIMIT,
        commitWaiter: null,
      };
      transaction.timer = setTimer(() => expire(token), TRANSFER_TIMEOUT_MS);
      activeTransfers.set(token, transaction);
      return token;
    }

    function inspect(ctx, token) {
      const transaction = activeTransfers.get(token);
      if (
        !isLive(ctx)
        || !transaction
        || transaction.status !== "prepared"
        || ctx.id === transaction.sourceId
        || (transaction.detachedWindowId && transaction.detachedWindowId !== ctx.id)
        || (transaction.inspectedBy && transaction.inspectedBy !== ctx.id)
      ) {
        return null;
      }
      transaction.inspectedBy = ctx.id;
      return {
        token,
        status: transaction.status,
        sourceId: transaction.sourceId,
        payload: transaction.payload,
      };
    }

    function journalOpened(ctx, token, role) {
      const transaction = activeTransfers.get(token);
      if (
        !transaction
        || transaction.status === "rolling-back"
        || transaction.status === "committing"
        || transaction.commitIndeterminate
      ) return false;
      const expectedId = role === "source"
        ? transaction.sourceId
        : role === "destination"
          ? transaction.inspectedBy || transaction.destinationId
          : null;
      if (!expectedId || ctx?.id !== expectedId) return false;
      if (role === "destination" && !transaction.destinationId) {
        transaction.destinationId = ctx.id;
      }
      const value = { role, windowId: ctx.id };
      transaction.journalRoles.set(roleKey(token, role, ctx.id), value);
      return true;
    }

    function accept(ctx, token, placement) {
      const transaction = activeTransfers.get(token);
      if (
        !isLive(ctx)
        || !transaction
        || transaction.status !== "prepared"
        || transaction.inspectedBy !== ctx.id
        || (transaction.destinationId && transaction.destinationId !== ctx.id)
      ) {
        return null;
      }
      const source = liveContext(transaction.sourceId);
      if (!source) return null;
      transaction.destinationId = ctx.id;
      for (const record of transaction.records) {
        const ownerToken = lockedRecords.get(record.id);
        if (ownerToken && ownerToken !== token) return null;
      }
      for (const record of transaction.records) {
        lockedRecords.set(record.id, token);
        transaction.lockedRecordIds.add(record.id);
      }
      try {
        transaction.recordSnapshots = reparentRecords(source, ctx, transaction.records);
      } catch (_error) {
        transaction.recordSnapshots = [];
        unlock(transaction);
        return null;
      }
      transaction.placement = placement || { kind: "strip-end" };
      transaction.status = "destination-staged";
      return {
        token,
        status: "destination-staged",
        sourceId: transaction.sourceId,
        destinationId: transaction.destinationId,
        payload: transaction.payload,
        placement: transaction.placement,
        recordIds: transaction.records.map((record) => record.id),
      };
    }

    function reject(ctx, token, reason, duplicateId) {
      const transaction = activeTransfers.get(token);
      if (
        !transaction
        || transaction.status !== "prepared"
        || transaction.inspectedBy !== ctx?.id
        || transaction.journalRoles.size > 0
        || !new Set(["duplicate", "group-full"]).has(reason)
        || (reason === "duplicate" && (typeof duplicateId !== "string" || !duplicateId))
      ) {
        return null;
      }
      const result = {
        reason,
        ...(reason === "duplicate" ? { duplicateId } : {}),
      };
      clearActive(transaction, { closeHidden: true });
      send(liveContext(transaction.sourceId), "tab-transfer:rejected", {
        token,
        ...result,
      });
      return result;
    }

    function destinationReady(ctx, token, ok) {
      const transaction = activeTransfers.get(token);
      if (
        !transaction
        || transaction.destinationId !== ctx?.id
        || transaction.status !== "destination-staged"
      ) {
        return false;
      }
      if (!ok) return beginRollback(transaction, "destination-failed");
      transaction.status = "awaiting-source";
      return send(liveContext(transaction.sourceId), "tab-transfer:remove-source", {
        token,
        payload: transaction.payload,
      });
    }

    function finishCommittedTransfer(transaction, decision) {
      for (const record of transaction.records) {
        if (!Number.isInteger(record.findRequestId)) continue;
        try {
          record.view.webContents.stopFindInPage("clearSelection");
        } catch (_error) {
          /* the page may have closed after the durable commit */
        }
        record.findRequestId = null;
      }
      transaction.status = "committed";
      transaction.commitIndeterminate = false;
      clearActive(transaction);
      notifyTerminal(transaction, "committed");
      const detached = liveContext(transaction.detachedWindowId);
      // Drop-to-place: the torn-off window is created hidden at release and
      // revealed HERE, once the destination renderer has staged the tab, so it
      // never flashes empty first — then fades in at the drop point instead of
      // popping.
      if (detached) showWindowSmoothly(detached.win);
      if (decision.requiredRoles.length === 0) {
        try {
          deleteDecision(transaction.token);
        } catch (_error) {
          /* the durable committed decision remains available for later cleanup */
        }
      } else {
        assignOrphanedRoles(decision);
      }
      const waiter = transaction.commitWaiter;
      transaction.commitWaiter = null;
      waiter?.resolve(true);
      return true;
    }

    function matchesCommittedTransaction(transaction, decision) {
      return decision?.token === transaction.token
        && decision.status === "committed"
        && decision.sourceId === transaction.sourceId
        && decision.destinationId === transaction.destinationId;
    }

    function reconcileIndeterminateCommit(transaction) {
      if (
        !transaction?.commitIndeterminate
        || activeTransfers.get(transaction.token) !== transaction
      ) return false;
      let store;
      try {
        store = loadTransferDecisions(storePath());
      } catch (_error) {
        return false;
      }
      durableDecisions = store;
      const current = store.decisions[transaction.token] || null;
      if (current && !matchesCommittedTransaction(transaction, current)) {
        return false;
      }
      let decision;
      try {
        // A readable committed rename is not enough after its directory fsync
        // failed. Rewriting the same decision establishes a durable boundary.
        // Preserve any acknowledgements a renderer already recorded against
        // the possibly-landed prior write.
        decision = persistDecision(
          transaction,
          "committed",
          current?.finalizedRoles || [],
        );
      } catch (_error) {
        return false;
      }
      return finishCommittedTransfer(transaction, decision);
    }

    function commitWaiter(transaction) {
      if (transaction.commitWaiter) return transaction.commitWaiter.promise;
      let resolve;
      const promise = new Promise((settle) => { resolve = settle; });
      transaction.commitWaiter = { promise, resolve };
      return promise;
    }

    function scheduleIndeterminateCommitRetry(transaction) {
      if (
        !transaction?.commitIndeterminate
        || activeTransfers.get(transaction.token) !== transaction
        || transaction.commitRetryTimer !== null
      ) return false;
      const delay = transaction.commitRetryDelay;
      transaction.commitRetryDelay = Math.min(delay * 2, COMMIT_RECONCILE_MAX_MS);
      transaction.commitRetryTimer = setTimer(() => {
        transaction.commitRetryTimer = null;
        if (!reconcileIndeterminateCommit(transaction)) {
          scheduleIndeterminateCommitRetry(transaction);
        }
      }, delay);
      return true;
    }

    function waitForIndeterminateCommit(transaction) {
      const pending = commitWaiter(transaction);
      if (!reconcileIndeterminateCommit(transaction)) {
        scheduleIndeterminateCommitRetry(transaction);
      }
      return pending;
    }

    function abandonCommitDecision(transaction) {
      transaction.sourceEmpty = false;
      const waiter = transaction.commitWaiter;
      transaction.commitWaiter = null;
      waiter?.resolve(false);
      if (
        !beginRollback(transaction, "commit-decision-failed")
        && activeTransfers.get(transaction.token) === transaction
        && transaction.timer === null
      ) {
        // ponytail: the rolled-back decision write failed too; re-arm the
        // expire timer so rollback keeps retrying instead of stranding.
        transaction.timer = setTimer(
          () => expire(transaction.token),
          TRANSFER_TIMEOUT_MS,
        );
      }
      return false;
    }

    function scheduleCommitDecisionRetry(transaction) {
      if (
        activeTransfers.get(transaction.token) !== transaction
        || transaction.status !== "committing"
        || transaction.commitRetryTimer !== null
      ) return;
      transaction.commitAttemptsLeft -= 1;
      const delay = transaction.commitRetryDelay;
      transaction.commitRetryDelay = Math.min(delay * 2, COMMIT_RECONCILE_MAX_MS);
      transaction.commitRetryTimer = setTimer(() => {
        transaction.commitRetryTimer = null;
        if (
          activeTransfers.get(transaction.token) !== transaction
          || transaction.status !== "committing"
        ) return;
        attemptCommitDecision(transaction);
      }, delay);
    }

    function attemptCommitDecision(transaction) {
      let decision;
      try {
        decision = persistDecision(transaction, "committed");
      } catch (error) {
        if (transaction.timer !== null) clearTimer(transaction.timer);
        transaction.timer = null;
        if (error?.rollbackError) {
          // The committed decision may have reached disk; rollback is no
          // longer an option. Retry reconciliation until it is durable.
          transaction.commitIndeterminate = true;
          transaction.status = "commit-indeterminate";
          return waitForIndeterminateCommit(transaction);
        }
        // Clean failure: the previous valid file is intact and no committed
        // decision landed. Retry the write with backoff before rolling back.
        transaction.status = "committing";
        if (transaction.commitAttemptsLeft > 0) {
          scheduleCommitDecisionRetry(transaction);
          return commitWaiter(transaction);
        }
        return abandonCommitDecision(transaction);
      }
      return finishCommittedTransfer(transaction, decision);
    }

    function sourceRemoved(ctx, token, result) {
      const transaction = activeTransfers.get(token);
      if (!transaction || transaction.sourceId !== ctx?.id) {
        return false;
      }
      if (transaction.commitIndeterminate) {
        return waitForIndeterminateCommit(transaction);
      }
      if (transaction.status === "committing") {
        // An idempotent re-acknowledgement joins the pending commit attempt.
        return commitWaiter(transaction);
      }
      if (transaction.status !== "awaiting-source") return false;
      const normalized = typeof result === "boolean" ? { ok: result } : result;
      if (!normalized?.ok) return beginRollback(transaction, "source-failed");
      transaction.sourceEmpty = !!normalized.sourceEmpty;
      return attemptCommitDecision(transaction);
    }

    function discardTransferredRecords(transaction) {
      const records = new Set([
        ...transaction.records,
        ...transaction.recordSnapshots.map((snapshot) => snapshot.record),
      ]);
      for (const record of records) {
        for (const context of windowRegistry.values()) {
          if (context.views.get(record.id) !== record) continue;
          context.visibleViewIds.delete(record.id);
          context.views.delete(record.id);
          try {
            context.win.contentView.removeChildView(record.view);
          } catch (_error) {
            /* the owning native surface may already be destroyed */
          }
        }
        record.navigation = null;
        record.ownerId = null;
        try {
          record.view.webContents.close();
        } catch (_error) {
          /* the native web contents may already be closed */
        }
      }
    }

    function finalizeRollback(transaction, destinationTimedOut = false) {
      if (
        activeTransfers.get(transaction?.token) !== transaction
        || transaction.status !== "rolling-back"
      ) return false;
      const source = liveContext(transaction.sourceId);
      const destination = windowRegistry.get(transaction.destinationId);
      if (source && destination && transaction.recordSnapshots.length > 0) {
        restoreRecords(source, destination, transaction.recordSnapshots);
      } else if (!source && transaction.records.length > 0) {
        discardTransferredRecords(transaction);
      }
      clearActive(transaction, { closeHidden: true });
      notifyTerminal(transaction, "rolled-back");
      const decision = terminalDecision(transaction.token);
      if (decision?.requiredRoles.length === 0) {
        deleteDecision(transaction.token);
      } else {
        const forced = destinationTimedOut
          ? new Set([roleKey(
            transaction.token,
            "destination",
            transaction.destinationId,
          )])
          : new Set();
        assignOrphanedRoles(decision, forced);
      }
      return true;
    }

    function beginRollback(transaction, reason, destinationGone = false) {
      if (!transaction || transaction.status === "committed") return false;
      if (transaction.commitIndeterminate) {
        scheduleIndeterminateCommitRetry(transaction);
        return true;
      }
      if (transaction.status === "rolling-back") {
        if (destinationGone) return finalizeRollback(transaction);
        return true;
      }
      if (transaction.status === "prepared" && transaction.journalRoles.size === 0) {
        clearActive(transaction, { closeHidden: true });
        send(liveContext(transaction.sourceId), "tab-transfer:rolled-back", {
          token: transaction.token,
          status: "rolled-back",
          reason,
        });
        return true;
      }
      try {
        persistDecision(transaction, "rolled-back");
      } catch (_error) {
        return false;
      }
      transaction.status = "rolling-back";
      if (transaction.timer !== null) clearTimer(transaction.timer);
      transaction.timer = null;
      if (transaction.commitRetryTimer !== null) {
        clearTimer(transaction.commitRetryTimer);
        transaction.commitRetryTimer = null;
      }
      const waiter = transaction.commitWaiter;
      transaction.commitWaiter = null;
      waiter?.resolve(false);
      const destination = liveContext(transaction.destinationId);
      if (!destination || destinationGone) return finalizeRollback(transaction);
      transaction.undoTimer = setTimer(
        () => finalizeRollback(transaction, true),
        DESTINATION_UNDO_TIMEOUT_MS,
      );
      if (!send(destination, "tab-transfer:undo-destination", {
        token: transaction.token,
        reason,
        discardWindowState: transaction.detachedWindowId === destination.id,
      })) {
        if (transaction.undoTimer !== null) clearTimer(transaction.undoTimer);
        transaction.undoTimer = null;
        return finalizeRollback(transaction, true);
      }
      return true;
    }

    function destinationUndone(ctx, token, ok) {
      const transaction = activeTransfers.get(token);
      if (
        !transaction
        || transaction.status !== "rolling-back"
        || transaction.destinationId !== ctx?.id
        || !ok
      ) {
        return false;
      }
      if (transaction.undoTimer !== null) clearTimer(transaction.undoTimer);
      transaction.undoTimer = null;
      return finalizeRollback(transaction);
    }

    function rollbackTransfer(token, reason = "manual") {
      const transaction = activeTransfers.get(token);
      if (!transaction || transaction.status === "committed") return false;
      return beginRollback(transaction, reason);
    }

    function cancel(ctx, token) {
      const transaction = activeTransfers.get(token);
      if (!transaction || transaction.sourceId !== ctx?.id) return false;
      if (transaction.status === "prepared" && transaction.journalRoles.size === 0) {
        clearActive(transaction, { closeHidden: true });
        send(liveContext(transaction.sourceId), "tab-transfer:rejected", {
          token,
          reason: "cancelled",
        });
        return true;
      }
      return beginRollback(transaction, "cancelled");
    }

    function status(ctx, token) {
      const transaction = activeTransfers.get(token);
      if (transaction) {
        if (
          ctx?.id !== transaction.sourceId
          && ctx?.id !== transaction.destinationId
          && ctx?.id !== transaction.inspectedBy
        ) {
          return null;
        }
        return {
          status: transaction.status,
          sourceId: transaction.sourceId,
          destinationId: transaction.destinationId,
        };
      }
      const decision = terminalDecision(token);
      if (!decision) return null;
      const participant = ctx?.id === decision.sourceId || ctx?.id === decision.destinationId;
      const assigned = decision.requiredRoles.some((required) =>
        assignmentFor(token, required.role, required.windowId) === ctx?.id);
      if (!participant && !assigned) return null;
      return {
        status: decision.status,
        sourceId: decision.sourceId,
        destinationId: decision.destinationId,
      };
    }

    function journalFinalized(ctx, token, role, ownerWindowId = ctx?.id) {
      if (!ctx || (role !== "source" && role !== "destination")) return false;
      const decision = terminalDecision(token);
      if (!decision) return false;
      const required = decision.requiredRoles.find(
        (item) => item.role === role && item.windowId === ownerWindowId,
      );
      if (!required) return false;
      const assignedWorker = assignmentFor(token, role, ownerWindowId);
      if (ctx.id !== ownerWindowId && assignedWorker !== ctx.id) return false;
      let result;
      try {
        result = ackTransferDecision(storePath(), token, required);
      } catch (_error) {
        return false;
      }
      if (result.complete) delete durableDecisions.decisions[token];
      else durableDecisions.decisions[token] = result.decision;
      const finalizedKey = roleKey(token, role, ownerWindowId);
      orphanAssignments.delete(finalizedKey);
      forcedOrphanRoles.delete(finalizedKey);
      if (result.decision.sourceEmpty && role === "source") {
        const source = liveContext(result.decision.sourceId);
        if (source) {
          allowedWindowCloses.add(source.id);
          try {
            source.win.close();
          } finally {
            allowedWindowCloses.delete(source.id);
          }
        }
      }
      if (result.complete) {
        for (const key of [...orphanAssignments.keys()]) {
          if (key.startsWith(`[\"${token}\",`)) orphanAssignments.delete(key);
        }
        for (const key of [...forcedOrphanRoles]) {
          if (key.startsWith(`[\"${token}\",`)) forcedOrphanRoles.delete(key);
        }
      }
      return true;
    }

    function pendingTerminal(ctx, windowId) {
      if (!isLive(ctx) || ctx.id !== windowId) return [];
      const pending = [];
      let store;
      try {
        store = refreshDecisions();
      } catch (_error) {
        // A transient store read failure yields no pending work this round;
        // the renderer re-queries on its next recovery pass.
        return pending;
      }
      for (const decision of Object.values(store.decisions)) {
        const finalized = new Set(
          decision.finalizedRoles.map((item) =>
            roleKey(decision.token, item.role, item.windowId)),
        );
        for (const required of decision.requiredRoles) {
          const key = roleKey(decision.token, required.role, required.windowId);
          if (finalized.has(key)) continue;
          let orphaned = false;
          if (required.windowId !== ctx.id) {
            const assigned = orphanAssignments.get(key);
            if (assigned !== ctx.id) {
              if (assigned && liveContext(assigned)) continue;
              if (!forcedOrphanRoles.has(key) && liveContext(required.windowId)) {
                continue;
              }
              orphanAssignments.set(key, ctx.id);
            }
            orphaned = true;
          }
          pending.push({
            token: decision.token,
            status: decision.status,
            sourceId: decision.sourceId,
            destinationId: decision.destinationId,
            role: required.role,
            windowId: required.windowId,
            orphaned,
            ...(decision.discardDestinationState
              && required.role === "destination"
              && required.windowId === decision.destinationId
              ? { discardWindowState: true }
              : {}),
          });
        }
      }
      return pending;
    }

    async function detach(ctx, token) {
      const transaction = activeTransfers.get(token);
      if (
        !transaction
        || transaction.sourceId !== ctx?.id
        || transaction.status !== "prepared"
      ) {
        return null;
      }
      if (transaction.detachedWindowId) return transaction.detachedWindowId;
      // Idempotence has to latch on the in-flight BOOT, not just the finished
      // window: a single leave-the-strip event can call detach() while a
      // release's detach() is still awaiting createWindow. A completed-only
      // guard lets both through and tears off two windows, one of which is
      // instantly orphaned.
      if (transaction.detachPromise) return transaction.detachPromise;
      const booting = detachUnlatched(transaction, token);
      transaction.detachPromise = booting;
      try {
        return await booting;
      } finally {
        transaction.detachPromise = null;
      }
    }

    async function detachUnlatched(transaction, token) {
      const windowId = `window-${makeToken()}`;
      const destination = await createDetachedWindow({ windowId, show: false, detached: true });
      if (activeTransfers.get(token) !== transaction || transaction.status !== "prepared") {
        allowedWindowCloses.add(destination.id);
        try {
          destination.win.close();
        } finally {
          allowedWindowCloses.delete(destination.id);
        }
        return null;
      }
      // Chrome drops the torn-off window where the tab was released, not at
      // the saved window position. Move it while it is still hidden so the
      // reposition is never visible.
      centerHiddenWindowOnCursor(destination.win);
      transaction.detachedWindowId = destination.id;
      transaction.destinationId = destination.id;
      destination.pendingTransferToken = token;
      return destination.id;
    }

    function claimPending(ctx, windowId) {
      if (!isLive(ctx) || ctx.id !== windowId) return null;
      const token = ctx.pendingTransferToken;
      const transaction = token ? activeTransfers.get(token) : null;
      if (
        !transaction
        || transaction.detachedWindowId !== ctx.id
        || transaction.status === "committed"
        || transaction.status === "rolling-back"
      ) {
        return null;
      }
      return token;
    }

    function contextDestroyed(ctx) {
      if (!ctx) return;
      for (const transaction of [...activeTransfers.values()]) {
        if (transaction.destinationId === ctx.id) {
          beginRollback(transaction, "destination-destroyed", true);
        } else if (
          transaction.status === "prepared"
          && transaction.inspectedBy === ctx.id
        ) {
          clearActive(transaction, { closeHidden: true });
          send(liveContext(transaction.sourceId), "tab-transfer:rejected", {
            token: transaction.token,
            reason: "destination-destroyed",
          });
        } else if (transaction.sourceId === ctx.id) {
          if (
            transaction.status === "prepared"
            && transaction.journalRoles.size === 0
          ) {
            clearActive(transaction, { closeHidden: true });
          } else {
            beginRollback(transaction, "source-destroyed");
          }
        }
      }
      const store = refreshDecisions();
      for (const decision of Object.values(store.decisions)) assignOrphanedRoles(decision);
    }

    function windowClosing(ctx, event) {
      if (!ctx || allowedWindowCloses.has(ctx.id)) return false;
      const transaction = [...activeTransfers.values()].find((candidate) =>
        candidate.sourceId === ctx.id || candidate.destinationId === ctx.id);
      if (!transaction) return false;
      event?.preventDefault?.();
      beginRollback(transaction, "window-closing");
      return true;
    }

    return {
      activeTransfers,
      prepare,
      inspect,
      accept,
      reject,
      status,
      journalOpened,
      journalFinalized,
      destinationReady,
      sourceRemoved,
      destinationUndone,
      rollback: rollbackTransfer,
      cancel,
      detach,
      claimPending,
      pendingTerminal,
      contextDestroyed,
      windowClosing,
      isLocked(id) { return lockedRecords.has(id); },
    };
  }

  return { reparentRecords, restoreRecords, makeTransferCoordinator };
}

module.exports = { createTransfers };
