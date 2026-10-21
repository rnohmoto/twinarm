"""The outbound wire contract of a teleop: UDP telemetry frames and the viewer helpers.

``telemetry_frame`` fixes the key order and the rounding the panel, the live plot,
the VR bridge and the rehearsal read; the frame loop gathers its live state and
calls it. ``viewer_commands`` is what ``--plot`` / ``--panel`` spawn.
"""

import sys

TELEMETRY_HOST = "127.0.0.1"


def parse_ports(spec):
    """Turn '8765,8769' into [8765, 8769]; zero and blanks are dropped."""
    ports = []
    for part in str(spec).split(","):
        part = part.strip()
        if part and int(part) > 0:
            ports.append(int(part))
    return ports


def telemetry_frame(
    *,
    t,
    action,
    cur,
    fpos,
    ff_ma,
    mode,
    temp,
    reconnects,
    n,
    leader_only,
    arm_label,
    device,
    alerts,
    hw,
    link,
    vw_on,
    vw_joints,
    pwm_joints,
    vw_out,
    vwall_on,
    wall,
    wall_engaged,
    params,
):
    """One telemetry frame as the viewers expect it (key order and rounding included).

    ``link`` is the remote follower's link statistics or ``None`` (wired follower);
    ``vw`` / ``vw_unit`` are ``None`` unless the virtual weight is on, ``vwall`` is
    ``None`` unless a wall is set while the virtual wall mode is on.
    """
    return {
        "t": round(t, 3),
        "cur": {m: round(v, 1) for m, v in cur.items()},
        "pos": {k.replace(".pos", ""): round(float(v), 1) for k, v in action.items()},
        "fpos": {m: round(float(v), 1) for m, v in fpos.items()},
        "ff": ff_ma,
        "mode": mode,
        "temp": temp,
        "rec": reconnects,
        "n": n,
        "leader_only": leader_only,
        "arm": arm_label,
        "device": device,
        "alerts": alerts,
        "hw": dict(hw),
        "link": link,
        "vw": {j: vw_out[j] for j in vw_joints} if vw_on else None,
        "vw_unit": (
            {j: ("PWM" if j in pwm_joints else "mA") for j in vw_joints}
            if vw_on
            else None
        ),
        "vwall": (
            {"name": wall.name, "width": wall.width, "engaged": wall_engaged}
            if (vwall_on and wall)
            else None
        ),
        "params": params,
    }


def viewer_commands(*, plot, panel, cams, ctl_port, viz_ports):
    """The helper processes ``--plot`` / ``--panel`` start, as argv lists (none without a port)."""
    if not viz_ports:
        return []
    commands = []
    if plot:
        commands.append(
            [sys.executable, "-m", "koch.live_plot", "--port", str(viz_ports[0])]
        )
    if panel:
        cmd = [
            sys.executable,
            "-m",
            "koch.panel",
            "--telemetry",
            str(viz_ports[0]),
            "--ctl-port",
            str(ctl_port),
        ]
        if cams:
            cmd += ["--cams", cams]
        commands.append(cmd)
    return commands
