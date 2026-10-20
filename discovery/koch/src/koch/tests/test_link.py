"""The UDP link watchdog mirrored from ``twinarm.domain.link_watchdog``."""

import pytest

from koch import link


@pytest.mark.unit
def test_newer_sequence_is_accepted_and_counted() -> None:
    state, ok = link.accept_packet(link.LinkState(), seq=0, now=10.0)
    assert ok and state.received == 1 and state.last_seq == 0 and state.last_rx == 10.0


@pytest.mark.unit
def test_older_or_duplicate_sequence_is_dropped() -> None:
    state, _ = link.accept_packet(link.LinkState(), 5, 10.0)
    state, ok = link.accept_packet(state, 5, 10.1)
    assert not ok and state.dropped == 1 and state.received == 1


@pytest.mark.unit
def test_gaps_in_the_sequence_are_counted_as_missed() -> None:
    state, _ = link.accept_packet(link.LinkState(), 0, 10.0)
    state, _ = link.accept_packet(state, 3, 10.1)
    assert state.missed == 2


@pytest.mark.unit
@pytest.mark.parametrize(
    ("age", "expected"), [(0.1, "ok"), (0.3, "alert"), (0.5, "hold"), (3.0, "stale")]
)
def test_health_is_graded_by_the_age_of_the_last_packet(
    age: float, expected: str
) -> None:
    state, _ = link.accept_packet(link.LinkState(), 0, 10.0)
    assert link.link_health(state, 10.0 + age) == expected


@pytest.mark.unit
def test_link_is_stale_before_the_first_packet() -> None:
    assert link.link_health(link.LinkState(), 10.0) == "stale"


@pytest.mark.unit
def test_loss_ratio_is_missed_over_missed_plus_received() -> None:
    assert link.loss_ratio(link.LinkState(received=4, missed=2)) == pytest.approx(1 / 3)
    assert link.loss_ratio(link.LinkState()) == 0.0


@pytest.mark.unit
def test_default_policy_thresholds() -> None:
    assert link.DEFAULT_LINK_POLICY == link.LinkPolicy(0.3, 0.5, 3.0)
