"""Force-feedback laws for the leader gripper.

Pure functions over frozen dataclasses: no I/O, no lerobot, no serial. The
hardware scripts in ``descovery/koch4/`` mirror these functions verbatim
because that sandbox cannot import this package (separate uv projects); keep
the two copies in sync by hand and let the tests here pin the semantics.

Ubiquitous language (see ``README.md``): currents in milliamps; the gripper
opening in lerobot's normalized 0-100 range (0 = calibrated closed end,
100 = calibrated open end); servo positions in raw ticks.

Three laws exist, one per force-feedback style of the working prototype:

* ``spring`` — the old ALOHA style. The follower's gripper current becomes a
  return-spring current on the leader (Operating_Mode 5, Goal_Current).
* ``error`` — error reflection. The distance the follower could not travel
  becomes a signed current on the leader (Operating_Mode 0), only once the
  follower has stalled.
* ``virtual wall`` — a virtual object for the VR demo. Contact starts at a
  normalized opening; the servo's own position loop renders the wall
  (Operating_Mode 5 with Goal_Position at the wall and Goal_Current as the
  force cap), so the host loop only decides whether the wall is engaged.
"""

import math
from dataclasses import dataclass

STALL_FRAMES_MAX = 10
FREE_SPACE_P_GAIN = 800

# ------------------------------------------------------------------ spring


@dataclass(frozen=True)
class SpringLaw:
    """Follower current -> leader return-spring current (Operating_Mode 5)."""

    gain: float = 1.5
    floor_ma: int = 60
    cap_ma: int = 450
    deadband_ma: int = 25
    alpha: float = 0.2


@dataclass(frozen=True)
class SpringState:
    """Smoothed follower current and the last commanded current."""

    ema: float = 0.0
    command_ma: int = 0


def spring_step(
    law: SpringLaw, state: SpringState, follower_current_ma: float
) -> SpringState:
    """Advance the spring law by one frame.

    Parameters
    ----------
    law
        Gains and limits.
    state
        Previous frame's state.
    follower_current_ma
        Signed follower gripper current; only its magnitude matters.

    Returns
    -------
    SpringState
        New state; ``command_ma`` is the Goal_Current to write.
    """
    magnitude = max(abs(follower_current_ma) - law.deadband_ma, 0.0)
    ema = (1.0 - law.alpha) * state.ema + law.alpha * magnitude
    command = int(min(law.floor_ma + law.gain * ema, law.cap_ma))
    return SpringState(ema=ema, command_ma=command)


# -------------------------------------------------------- error reflection


@dataclass(frozen=True)
class ErrorReflectionLaw:
    """Leader-minus-follower position error -> signed leader current (mode 0)."""

    ke: float = 9.0
    deadband: float = 6.0
    sign: int = 1
    cap_ma: int = 450
    slew_ma: int = 90
    move_eps: float = 0.4
    stall_frames: int = 3
    alpha: float = 0.3
    release: float = 0.5


@dataclass(frozen=True)
class ErrorReflectionState:
    """Stall detector state, smoothed target and the last commanded current."""

    prev_follower_pos: float | None = None
    stall_frames: int = 0
    ema: float = 0.0
    command_ma: int = 0


def error_reflection_step(
    law: ErrorReflectionLaw,
    state: ErrorReflectionState,
    leader_cmd: float,
    follower_pos: float,
) -> ErrorReflectionState:
    """Advance the error-reflection law by one frame.

    The reflected current is zero while the follower is still moving (lag
    would otherwise fight the hand and oscillate) and grows only once the
    follower has stalled with an error beyond the deadband.
    """
    moving = (
        state.prev_follower_pos is not None
        and abs(follower_pos - state.prev_follower_pos) > law.move_eps
    )
    stall = 0 if moving else min(state.stall_frames + 1, STALL_FRAMES_MAX)
    error = leader_cmd - follower_pos
    if abs(error) < law.deadband or stall < law.stall_frames:
        target = 0.0
    else:
        over = error - (law.deadband if error > 0 else -law.deadband)
        target = -law.ke * over * law.sign
    ema = (1.0 - law.alpha) * state.ema + law.alpha * target
    if target == 0.0:
        ema *= law.release
    raw = max(min(ema, law.cap_ma), -law.cap_ma)
    command = int(
        max(
            min(raw, state.command_ma + law.slew_ma),
            state.command_ma - law.slew_ma,
        )
    )
    return ErrorReflectionState(
        prev_follower_pos=follower_pos, stall_frames=stall, ema=ema, command_ma=command
    )


# ------------------------------------------------------------ virtual wall


@dataclass(frozen=True)
class VirtualWall:
    """A virtual object the leader gripper can close on.

    ``width`` is the normalized opening at which the fingers first touch the
    object; ``p_gain`` is the servo position-loop gain (perceived stiffness);
    ``cap_ma`` the Goal_Current that bounds the rendered force; ``release`` the
    hysteresis above ``width`` before the wall lets go.
    """

    name: str
    width: float
    p_gain: int = 800
    cap_ma: int = 300
    release: float = 1.0


@dataclass(frozen=True)
class WallCommand:
    """Registers to write on the leader gripper for one frame."""

    goal_position_tick: int
    goal_current_ma: int
    p_gain: int
    engaged: bool


def wall_tick(width: float, closed_tick: int, open_tick: int) -> int:
    """Map a normalized opening (0-100) to a raw tick between the two ends."""
    fraction = min(max(width, 0.0), 100.0) / 100.0
    return round(closed_tick + fraction * (open_tick - closed_tick))


def virtual_wall_step(
    wall: VirtualWall | None,
    opening: float,
    closed_tick: int,
    open_tick: int,
    engaged: bool,
) -> WallCommand:
    """Decide whether the wall is engaged and what to write to the servo.

    Outside the object the gripper is free (Goal_Current 0). Once the opening
    reaches the object's width the position target sits at the wall and the
    current cap is raised, so the servo pushes the trigger back toward open.
    """
    if wall is None:
        return WallCommand(open_tick, 0, FREE_SPACE_P_GAIN, False)
    limit = wall.width + (wall.release if engaged else 0.0)
    now_engaged = opening <= limit
    return WallCommand(
        goal_position_tick=wall_tick(wall.width, closed_tick, open_tick),
        goal_current_ma=wall.cap_ma if now_engaged else 0,
        p_gain=wall.p_gain,
        engaged=now_engaged,
    )


# ----------------------------------------------------------- thermal guard


@dataclass(frozen=True)
class ThermalGuard:
    """Servo temperature policy: derate at one threshold, stop at another."""

    derate_c: int = 60
    stop_c: int = 65
    min_gain: float = 0.1


def thermal_derate(guard: ThermalGuard, temp_c: int, gain: float) -> tuple[float, bool]:
    """Return the gain to use and whether feedback must stop."""
    if temp_c >= guard.stop_c:
        return gain, True
    if temp_c >= guard.derate_c:
        return max(gain * 0.5, guard.min_gain), False
    return gain, False


# ---------------------------------------------------------- virtual weight

JOINT_SPAN_DEG = {
    "shoulder_pan": 180.0,
    "shoulder_lift": 100.0,
    "elbow_flex": 100.0,
    "wrist_flex": 100.0,
    "wrist_roll": 180.0,
}
LINK_M = (0.11, 0.108, 0.115)  # upper arm, forearm, wrist + gripper to the tip (twin)
G_MPS2 = 9.81
KT_NM_PER_A = 0.146  # XL330-M077 stall data at 5 V (0.215 N*m at 1.47 A)


@dataclass(frozen=True)
class JointMap:
    """How a normalized joint value (+-100) becomes a display/model angle."""

    span_deg: float
    offset_deg: float = 0.0
    sign: int = 1


def norm_to_rad(value: float, joint_map: JointMap) -> float:
    """Map a normalized joint value to radians with the joint's span, offset and sign."""
    degrees = value / 100.0 * joint_map.span_deg / 2.0 + joint_map.offset_deg
    return joint_map.sign * math.radians(degrees)


def tip_levers(
    lift_rad: float,
    elbow_rad: float,
    wrist_rad: float,
    links: tuple[float, float, float] = LINK_M,
) -> tuple[float, float]:
    """Horizontal lever arms [m] of the gripper tip about the shoulder_lift and elbow.

    Planar chain in the sagittal plane; 0 rad means the link points straight up,
    positive angles tilt it forward. The levers are what a payload at the tip
    multiplies by ``m * g`` to load each joint.
    """
    a1 = lift_rad
    a2 = a1 + elbow_rad
    a3 = a2 + wrist_rad
    x1 = links[0] * math.sin(a1)
    x2 = x1 + links[1] * math.sin(a2)
    x3 = x2 + links[2] * math.sin(a3)
    return x3, x3 - x1


@dataclass(frozen=True)
class VirtualWeightLaw:
    """Render a grasped object's weight as current on the leader's lift and elbow."""

    scale: float = 0.12
    cap_ma: int = 120
    alpha: float = 0.2
    release: float = 0.5
    invert_shoulder: bool = False
    invert_elbow: bool = False


@dataclass(frozen=True)
class VirtualWeightState:
    """Smoothed currents [mA] for shoulder_lift and elbow_flex."""

    shoulder_ma: float = 0.0
    elbow_ma: float = 0.0


def _weight_target_ma(law: VirtualWeightLaw, torque_nm: float, invert: bool) -> float:
    physical_ma = torque_nm / KT_NM_PER_A * 1000.0
    target = max(min(physical_ma * law.scale, law.cap_ma), -law.cap_ma)
    return -target if invert else target


def virtual_weight_step(
    law: VirtualWeightLaw,
    state: VirtualWeightState,
    engaged: bool,
    mass_g: float,
    levers_m: tuple[float, float],
) -> VirtualWeightState:
    """Advance the weight law by one frame; releases decay faster than they rise."""
    if engaged and mass_g > 0.0:
        force_n = mass_g / 1000.0 * G_MPS2
        target_s = _weight_target_ma(law, force_n * levers_m[0], law.invert_shoulder)
        target_e = _weight_target_ma(law, force_n * levers_m[1], law.invert_elbow)
        alpha = law.alpha
    else:
        target_s = target_e = 0.0
        alpha = law.release
    return VirtualWeightState(
        shoulder_ma=(1.0 - alpha) * state.shoulder_ma + alpha * target_s,
        elbow_ma=(1.0 - alpha) * state.elbow_ma + alpha * target_e,
    )
