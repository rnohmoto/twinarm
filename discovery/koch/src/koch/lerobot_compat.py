"""The one place that imports lerobot, lazily, and the bus retries it installs.

lerobot 0.5+ defaults every bus call to ``num_retry=0``; on the Koch bus a single
lost packet would then end the session, so ``load_lerobot()`` patches a floor onto
``MotorsBus`` before the robot classes are returned. Nothing here runs at import.
"""

import inspect

RETRY_SPEC = (("sync_read", 10), ("read", 5), ("write", 5), ("sync_write", 5))


def _with_retry_floor(orig, floor):
    def patched(self, *args, **kwargs):
        kwargs["num_retry"] = max(int(kwargs.get("num_retry", 0) or 0), floor)
        return orig(self, *args, **kwargs)

    return patched


def install_retries(bus_cls, spec=RETRY_SPEC):
    """Raise ``num_retry`` to a floor on each method of ``bus_cls`` named in ``spec``.

    Methods without a ``num_retry`` parameter are left alone; an explicit larger
    count passed by a caller is kept.
    """
    for name, floor in spec:
        orig = getattr(bus_cls, name)
        if "num_retry" not in inspect.signature(orig).parameters:
            continue
        setattr(bus_cls, name, _with_retry_floor(orig, floor))


def load_lerobot():
    """Import lerobot, install the retries, return the Koch classes and the retry errors."""
    from lerobot.motors.motors_bus import MotorsBus

    install_retries(MotorsBus)

    from lerobot.robots.koch_follower import KochFollower, KochFollowerConfig
    from lerobot.teleoperators.koch_leader import KochLeader, KochLeaderConfig

    try:
        from lerobot.errors import DeviceNotConnectedError  # ty: ignore[unresolved-import]

        retry_errors = (ConnectionError, DeviceNotConnectedError)
    except Exception:  # noqa: BLE001 - older lerobot without the error module
        retry_errors = (ConnectionError,)
    return KochFollower, KochFollowerConfig, KochLeader, KochLeaderConfig, retry_errors
