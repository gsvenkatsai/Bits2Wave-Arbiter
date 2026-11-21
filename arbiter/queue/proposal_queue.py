"""FR-3: thread-safe 100 ms proposal queue with a hold budget.

Tick semantics (SRS R7)
-----------------------
``tick`` is an integer in *simulated* time supplied by the caller. It is never
wall-clock time: the queue only compares ticks against each other, so the
simulator, replay or tests can drive it deterministically.

Hold semantics (documented interpretation)
------------------------------------------
``hold_count`` counts ticks spent delayed, from 0 up to ``MAX_HOLD_TICKS`` (3).
Each ``hold()`` parks the command for one tick and increments the counter. When
the counter reaches ``MAX_HOLD_TICKS`` the proposal copy is flagged
``must_decide=True``: the ladder may not return DELAY for it again, and the
queue refuses any further hold, so a released ``must_decide`` command can never
enter a second hold cycle ("never held twice").
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Dict, Iterable, List, Sequence, Set

from arbiter.engine.contracts import MAX_HOLD_TICKS, CommandProposal
from arbiter.queue.emergency_lane import EmergencyBypassLane

__all__ = ["ProposalQueue", "QueueInvariantError", "MAX_HOLD_TICKS"]


class QueueInvariantError(AssertionError):
    """Raised when a hold/duplicate invariant of the queue is violated."""


@dataclass(frozen=True)
class _ParkedCommand:
    """A delayed command waiting for its re-evaluation tick."""

    command: CommandProposal
    due_tick: int


def _batch_order(command: CommandProposal) -> tuple[int, str]:
    """Deterministic batch ordering: created_tick, then command_id."""
    return (command.created_tick, command.command_id)


class ProposalQueue:
    """Buffers controller proposals per simulated tick; thread-safe."""

    def __init__(
        self,
        emergency_lane: EmergencyBypassLane | None = None,
        max_hold_ticks: int = MAX_HOLD_TICKS,
    ) -> None:
        if max_hold_ticks < 1:
            raise ValueError("max_hold_ticks must be >= 1")
        self.max_hold_ticks = max_hold_ticks
        self._lane = emergency_lane or EmergencyBypassLane()
        self._lock = threading.Lock()
        self._buffer: List[CommandProposal] = []
        self._delayed: List[_ParkedCommand] = []
        self._known_ids: Set[str] = set()
        self._hold_counts: Dict[str, int] = {}
        self._exhausted_ids: Set[str] = set()
        self._tick = 0

    # -- ingestion ---------------------------------------------------------
    def submit(self, cmd: CommandProposal) -> bool:
        """Accept a proposal; returns ``False`` for a duplicate command id.

        Emergency proposals are routed straight to the bypass lane and never
        enter the normal buffer.
        """
        with self._lock:
            if cmd.command_id in self._known_ids:
                return False
            self._known_ids.add(cmd.command_id)
            self._hold_counts.setdefault(cmd.command_id, cmd.hold_count)
            if cmd.is_emergency:
                self._lane.accept(cmd, tick=self._tick)
                return True
            self._buffer.append(cmd)
            return True

    def submit_many(self, commands: Iterable[CommandProposal]) -> int:
        """Submit several proposals; returns how many were accepted."""
        return sum(1 for command in commands if self.submit(command))

    # -- batching ----------------------------------------------------------
    def flush_batch(self, tick: int) -> List[CommandProposal]:
        """Release buffered proposals plus delayed ones now due, in order."""
        with self._lock:
            self._tick = int(tick)
            released = list(self._buffer)
            self._buffer.clear()

            still_parked: List[_ParkedCommand] = []
            for parked in self._delayed:
                if parked.due_tick <= tick:
                    released.append(parked.command)
                    if parked.command.must_decide:
                        self._exhausted_ids.add(parked.command.command_id)
                else:
                    still_parked.append(parked)
            self._delayed = still_parked

            released.sort(key=_batch_order)
            return released

    # -- hold bookkeeping --------------------------------------------------
    def is_holdable(self, cmd: CommandProposal) -> bool:
        """True while the command still has hold budget and is not parked."""
        with self._lock:
            return self._is_holdable_locked(cmd)

    def _is_holdable_locked(self, cmd: CommandProposal) -> bool:
        if cmd.is_emergency:
            return False
        command_id = cmd.command_id
        if command_id in self._exhausted_ids:
            return False
        if any(parked.command.command_id == command_id for parked in self._delayed):
            return False
        spent = self._hold_counts.get(command_id, cmd.hold_count)
        return spent < self.max_hold_ticks

    def hold(self, cmd: CommandProposal, tick: int) -> CommandProposal | None:
        """Park ``cmd`` for one tick; returns the updated copy or ``None``.

        ``None`` means the command may not be delayed: it is emergency, already
        parked, or its hold budget (``MAX_HOLD_TICKS``) is exhausted.
        """
        with self._lock:
            if not self._is_holdable_locked(cmd):
                return None
            command_id = cmd.command_id
            spent = self._hold_counts.get(command_id, cmd.hold_count) + 1
            self._hold_counts[command_id] = spent
            must_decide = spent >= self.max_hold_ticks
            updated = cmd.model_copy(
                update={"hold_count": spent, "must_decide": must_decide}
            )
            self._delayed.append(_ParkedCommand(updated, int(tick) + 1))
            if must_decide:
                self._exhausted_ids.add(command_id)
            return updated

    def increment_holds(self) -> List[CommandProposal]:
        """Advance the hold counter of every parked command by one tick.

        Used when a command stays parked for more than one tick. Counters are
        clamped at ``max_hold_ticks`` and latch ``must_decide`` at the cap.
        Returns the updated copies currently parked.
        """
        with self._lock:
            updated_commands: List[CommandProposal] = []
            for index, parked in enumerate(self._delayed):
                command_id = parked.command.command_id
                spent = min(
                    self._hold_counts.get(command_id, parked.command.hold_count) + 1,
                    self.max_hold_ticks,
                )
                self._hold_counts[command_id] = spent
                must_decide = spent >= self.max_hold_ticks
                if must_decide:
                    self._exhausted_ids.add(command_id)
                updated = parked.command.model_copy(
                    update={"hold_count": spent, "must_decide": must_decide}
                )
                self._delayed[index] = _ParkedCommand(updated, parked.due_tick)
                updated_commands.append(updated)
            return updated_commands

    def must_decide(self, cmd: CommandProposal) -> bool:
        """True when the command's hold budget is spent (no further DELAY)."""
        with self._lock:
            spent = self._hold_counts.get(cmd.command_id, cmd.hold_count)
            return (
                cmd.must_decide
                or cmd.command_id in self._exhausted_ids
                or spent >= self.max_hold_ticks
            )

    # -- invariants --------------------------------------------------------
    def check_invariants(self) -> None:
        """Assert the FR-3 hold invariants; raises :class:`QueueInvariantError`."""
        with self._lock:
            for command_id, spent in self._hold_counts.items():
                if spent > self.max_hold_ticks:
                    raise QueueInvariantError(
                        f"{command_id} has hold_count {spent} > {self.max_hold_ticks}"
                    )
            pending_ids = [command.command_id for command in self._buffer]
            parked_ids = [parked.command.command_id for parked in self._delayed]
            all_ids = pending_ids + parked_ids
            if len(all_ids) != len(set(all_ids)):
                raise QueueInvariantError("a command is queued more than once")
            for parked in self._delayed:
                if parked.command.hold_count > self.max_hold_ticks:
                    raise QueueInvariantError(
                        f"{parked.command.command_id} exceeds the hold budget"
                    )
                if parked.command.command_id in self._exhausted_ids and not (
                    parked.command.must_decide
                ):
                    raise QueueInvariantError(
                        f"{parked.command.command_id} was held after must_decide"
                    )
            for command_id in self._exhausted_ids:
                parked_again = [
                    parked
                    for parked in self._delayed
                    if parked.command.command_id == command_id
                    and not parked.command.must_decide
                ]
                if parked_again:
                    raise QueueInvariantError(
                        f"{command_id} entered a second hold cycle"
                    )

    # -- inspection --------------------------------------------------------
    @property
    def emergency_lane(self) -> EmergencyBypassLane:
        """The bypass lane used for emergency proposals."""
        return self._lane

    @property
    def current_tick(self) -> int:
        """Last simulated tick seen by the queue."""
        with self._lock:
            return self._tick

    def set_tick(self, tick: int) -> None:
        """Set the simulated tick used to stamp emergency decisions."""
        with self._lock:
            self._tick = int(tick)

    @property
    def pending_count(self) -> int:
        """Proposals waiting in the normal buffer."""
        with self._lock:
            return len(self._buffer)

    @property
    def delayed_count(self) -> int:
        """Commands parked waiting for re-evaluation."""
        with self._lock:
            return len(self._delayed)

    @property
    def emergency_count(self) -> int:
        """Audit records produced by the bypass lane (not yet drained)."""
        return self._lane.audit_count

    @property
    def is_empty(self) -> bool:
        """True when nothing is buffered or parked."""
        with self._lock:
            return not self._buffer and not self._delayed

    def snapshot(self) -> Sequence[CommandProposal]:
        """Copy of buffered and parked commands, in batch order."""
        with self._lock:
            commands = list(self._buffer) + [p.command for p in self._delayed]
        return tuple(sorted(commands, key=_batch_order))
