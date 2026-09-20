"""Watchdog for a follower link carried over UDP (stdlib only).

The wireless follower (``descovery/koch4/koch4_follower_host.py``) and the
Mac-side ``RemoteFollower`` in ``koch4_teleop.py`` mirror these functions:
packets carry a sequence number, only newer packets are accepted, and the age
of the last accepted packet grades the link as ok / alert / hold / stale.
The policy thresholds are what the hardware scripts act on: warn the operator
at ``alert_after_s``, stop writing new goals (hold the pose) at
``hold_after_s``, and treat the link as lost at ``stale_after_s``.
"""

import math
from dataclasses import dataclass, replace


@dataclass(frozen=True)
class LinkPolicy:
    """Ages [s] at which the link is downgraded."""

    alert_after_s: float = 0.3
    hold_after_s: float = 0.5
    stale_after_s: float = 3.0


DEFAULT_LINK_POLICY = LinkPolicy()


@dataclass(frozen=True)
class LinkState:
    """Bookkeeping of what arrived: last sequence, its time and the counters."""

    last_seq: int = -1
    last_rx: float = -math.inf
    received: int = 0
    dropped: int = 0  # out-of-order or duplicate packets that were ignored
    missed: int = 0  # gaps in the sequence (packets that never arrived)


def accept_packet(state: LinkState, seq: int, now: float) -> tuple[LinkState, bool]:
    """Register a packet; only a newer sequence number is accepted."""
    if seq <= state.last_seq:
        return replace(state, dropped=state.dropped + 1), False
    gap = seq - state.last_seq - 1 if state.last_seq >= 0 else 0
    return (
        LinkState(
            last_seq=seq,
            last_rx=now,
            received=state.received + 1,
            dropped=state.dropped,
            missed=state.missed + gap,
        ),
        True,
    )


def link_health(
    state: LinkState, now: float, policy: LinkPolicy = DEFAULT_LINK_POLICY
) -> str:
    """Grade the link by the age of the last accepted packet."""
    age = now - state.last_rx
    if state.received == 0 or age >= policy.stale_after_s:
        return "stale"
    if age >= policy.hold_after_s:
        return "hold"
    if age >= policy.alert_after_s:
        return "alert"
    return "ok"


def loss_ratio(state: LinkState) -> float:
    """Fraction of packets that never arrived (gaps over gaps plus received)."""
    total = state.received + state.missed
    return state.missed / total if total else 0.0
