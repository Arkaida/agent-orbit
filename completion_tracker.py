"""Debounce observed Codex running-to-idle transitions without startup alerts."""

from dataclasses import dataclass
from uuid import uuid4


@dataclass
class _PaneState:
    armed: bool = False
    idle_since: float = None
    candidate_wall: float = None
    completed_at: float = None
    completion_id: str = None
    process_id: int = None

    def reset_pending(self):
        self.armed = False
        self.idle_since = None
        self.candidate_wall = None


class CompletionTracker:
    """Confirm idle only after it remains visible for ``settle_seconds``.

    Feed this class newly received snapshots, never cached rows. ``epoch`` must
    change after an SSH reconnect. Completion timestamps describe the first
    observed idle sample, while the event is emitted after debounce succeeds.
    """

    def __init__(self, settle_seconds=1.0):
        if settle_seconds < 0:
            raise ValueError('settle_seconds must be non-negative')
        self.settle_seconds = settle_seconds
        self._panes = {}
        self._epoch = None
        self.events = []

    def update(self, rows, now_mono, now_wall, epoch=0):
        if epoch != self._epoch:
            for state in self._panes.values():
                state.reset_pending()
            self._epoch = epoch

        # Pane disappearance breaks continuity, even if the same pane returns.
        current_ids = {row['pane_id'] for row in rows}
        self._panes = {key: value for key, value in self._panes.items()
                       if key in current_ids}
        decorated, events = [], []
        for row in rows:
            pane_id = row['pane_id']
            pane = self._panes.setdefault(pane_id, _PaneState())
            pid = row.get('process_id')
            if pane.process_id and pid and pid != pane.process_id:
                pane = _PaneState()
                self._panes[pane_id] = pane
            pane.process_id = pid
            state = row.get('state')
            if state == 'running':
                pane.armed = True
                pane.idle_since = None
                pane.candidate_wall = None
            elif state == 'idle' and pane.armed:
                if pane.idle_since is None:
                    pane.idle_since = now_mono
                    pane.candidate_wall = now_wall
                if now_mono - pane.idle_since >= self.settle_seconds:
                    pane.completed_at = pane.candidate_wall
                    pane.completion_id = uuid4().hex
                    event = dict(id=pane.completion_id, pane_id=pane_id,
                                 session=row['session'],
                                 completed_at=pane.completed_at)
                    events.append(event)
                    self.events.append(event)
                    # Retain a bounded history for UIs, across reconnects.
                    self.events[:] = self.events[-100:]
                    pane.reset_pending()
            elif state != 'idle':
                # Approval/history/unknown do not establish work completion.
                pane.reset_pending()
            decorated.append(dict(row, completed_at=pane.completed_at,
                                  completion_id=pane.completion_id))
        return decorated, events
