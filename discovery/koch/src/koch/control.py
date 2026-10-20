"""Inbound control values: walls from the CLI and the VR page, twin joint maps, scaling.

Pure functions; the frame loop keeps the live state and calls these to interpret
what arrives on the control socket or the command line.
"""

import argparse
from dataclasses import replace

from koch.feedback import JointMap, VirtualWall
from koch.motors import FF_CAP_MAX_MA


def parse_wall(spec):
    """Parse ``name:width[:p_gain[:cap_ma[:release[:mass_g]]]]`` into a VirtualWall."""
    parts = spec.split(":")
    if len(parts) < 2:
        raise argparse.ArgumentTypeError(
            "--wall は name:width[:p_gain[:cap_ma[:release[:mass_g]]]]"
        )
    name, width = parts[0], float(parts[1])
    p_gain = int(parts[2]) if len(parts) > 2 else 800
    cap = int(parts[3]) if len(parts) > 3 else 300
    release = float(parts[4]) if len(parts) > 4 else 1.0
    mass_g = float(parts[5]) if len(parts) > 5 else 0.0
    return wall_from_fields(name, width, p_gain, cap, release, mass_g)


def wall_from_fields(name, width, p_gain, cap_ma, release, mass_g=0.0):
    """Build a VirtualWall with every field clamped to a safe range."""
    return VirtualWall(
        name=str(name)[:32],
        width=min(max(float(width), 0.0), 100.0),
        p_gain=int(min(max(int(p_gain), 0), 16383)),
        cap_ma=int(min(max(int(cap_ma), 0), FF_CAP_MAX_MA)),
        release=min(max(float(release), 0.0), 20.0),
        mass_g=min(max(float(mass_g), 0.0), 2000.0),
    )


def wall_from_message(payload):
    """Turn the ``vwall`` field of a control message into a VirtualWall or None."""
    if not payload:
        return None
    return wall_from_fields(
        payload.get("name", "obj"),
        payload.get("width", 50.0),
        payload.get("p_gain", 800),
        payload.get("cap_ma", 300),
        payload.get("release", 1.0),
        payload.get("mass_g", 0.0),
    )


def scale_wall(wall, wall_scale, ff_cap):
    """Apply the device's current scale (rounded), then cap at ``--ff-cap``.

    ``None`` passes through. The scale converts a wall tuned for the leader's
    XL330-M077 to a hand-moved follower's M288 (same force at ~0.45 of the current).
    """
    if wall is None:
        return None
    if wall_scale != 1.0:
        wall = replace(wall, cap_ma=round(wall.cap_ma * wall_scale))
    if wall.cap_ma > ff_cap:
        wall = replace(wall, cap_ma=int(ff_cap))
    return wall


def twin_map_from_message(current, payload):
    """Joint maps the bridge sends for the twin (weight FK): only known joints, dict entries.

    Returns the changed entries; the caller merges them, ``current`` is not touched.
    """
    joints = (payload or {}).get("joints", {})
    changed = {}
    for j, m in joints.items():
        if j in current and isinstance(m, dict):
            changed[j] = JointMap(
                float(m.get("span_deg", current[j].span_deg)),
                float(m.get("offset_deg", current[j].offset_deg)),
                1 if int(m.get("sign", current[j].sign)) >= 0 else -1,
            )
    return changed
