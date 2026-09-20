"""Tests for the follower-link watchdog (sequence handling and health)."""

import pytest

from twinarm.domain.link_watchdog import (
    LinkPolicy,
    LinkState,
    accept_packet,
    link_health,
)


@pytest.mark.unit
def test_accept_packet_keeps_newer_sequence_and_drops_stale_ones() -> None:
    state = LinkState()

    state, ok1 = accept_packet(state, seq=5, now=10.0)
    state, ok2 = accept_packet(state, seq=4, now=10.1)
    state, ok3 = accept_packet(state, seq=6, now=10.2)

    assert (ok1, ok2, ok3) == (True, False, True)
    assert state.last_seq == 6
    assert state.received == 2
    assert state.dropped == 1
    assert state.last_rx == 10.2


@pytest.mark.unit
def test_link_health_grades_by_age() -> None:
    policy = LinkPolicy(alert_after_s=0.3, hold_after_s=0.5, stale_after_s=3.0)
    state = LinkState(last_seq=1, last_rx=100.0, received=1)

    assert link_health(state, now=100.1, policy=policy) == "ok"
    assert link_health(state, now=100.4, policy=policy) == "alert"
    assert link_health(state, now=101.0, policy=policy) == "hold"
    assert link_health(state, now=104.0, policy=policy) == "stale"


@pytest.mark.unit
def test_link_health_is_stale_before_the_first_packet() -> None:
    assert link_health(LinkState(), now=0.0, policy=LinkPolicy()) == "stale"


@pytest.mark.unit
def test_loss_ratio_counts_gaps_in_sequence() -> None:
    from twinarm.domain.link_watchdog import loss_ratio

    state = LinkState()
    for seq in (1, 2, 5, 6):  # 3 and 4 never arrived
        state, _ = accept_packet(state, seq=seq, now=float(seq))

    assert state.received == 4
    assert state.missed == 2
    assert loss_ratio(state) == pytest.approx(2 / 6)
