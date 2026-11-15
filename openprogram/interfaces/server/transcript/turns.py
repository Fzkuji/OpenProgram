"""One turn from its ids: rows read fresh from the store and shaped for the wire."""
from __future__ import annotations

from typing import Any

from .children import execution_rows
from .navigation import SINGLE, with_attach_stats, with_navigation
from .rows import project_rows
from .schema import Turn
from .turn_index import TranscriptView, TurnEntry


class TurnReader:
    """Projects turns of one view; call trees of a page's cards are built once."""

    def __init__(self, db: Any, view: TranscriptView, entries: list[TurnEntry]):
        self.db = db
        self.view = view
        self._card_ids = [row_id for entry in entries for row_id in entry.row_ids if row_id in view.card_ids]
        self._trees: dict[str, Any] | None = None

    def _tree(self, row_id: str) -> Any:
        if self._trees is None:
            from openprogram.runtime.execution.dag import build_exec_dags_by_id

            self._trees = build_exec_dags_by_id(self.view.session_id, self._card_ids)
        return self._trees.get(row_id)

    def stored_rows(self, entry: TurnEntry) -> list[dict]:
        """A turn's stored rows, read now, so a row changed after open reads fresh."""
        from openprogram.programs.tools.planning.goal.presentation import annotate_messages

        view = self.view
        rows = self.db.get_messages_by_id(view.session_id, entry.raw_ids)
        executions = {row_id: view.goal_executions[row_id] for row_id in entry.raw_ids if row_id in view.goal_executions}
        return annotate_messages(view.session_id, rows, executions=executions) if executions else rows

    def project(self, entry: TurnEntry) -> Turn:
        from openprogram.interfaces.server.persistence import aggregate_tool_messages
        from openprogram.interfaces.server.ws_actions.session.tool_output import _truncate_tool_outputs_for_wire
        from openprogram.interfaces.server.ws_actions.session.transcript import _runtime_card

        view = self.view
        raw_rows = self.stored_rows(entry)
        by_id = {row.get("id"): row for row in aggregate_tool_messages([dict(row) for row in raw_rows])}
        shown: list[dict] = []
        chain: list[dict] = []
        for row_id in entry.row_ids:
            if row_id in view.synthetic:
                shown.append(dict(view.synthetic[row_id]))
                continue
            row = by_id.get(row_id)
            if row is None:
                continue
            if row_id in view.card_ids:
                row = _runtime_card(row, self._tree(row_id))
            chain.append(row)
            drawn = with_attach_stats(with_navigation(row, view.navigation.get(row_id, SINGLE)), view.session_id)
            if row_id in view.spawned_from:
                drawn["spawned_from"] = view.spawned_from[row_id]
            shown.append(drawn)
        shown.extend(execution_rows(chain, raw_rows))
        labels = {
            row_id: facts.get("label")
            for row_id, facts in view.spawned_from.items() if facts.get("label")
        } if view.spawned_from else None
        return {"id": entry.id, "rows": project_rows(
            _truncate_tool_outputs_for_wire(shown), session_id=view.session_id,
            covered_by=view.covered_by, spawn_labels=labels, db=self.db,
        )}
