"""Send one control message to running teleops (what the panel's sliders do), for bench tuning.

  uv run python -m koch.tools.ctl ff_slew=300 ff_alpha=0.5          # pair A (control port 8766)
  uv run python -m koch.tools.ctl --ctl-port 8766,8768 ff_ke=25     # both pairs

Keys are the teleop's control keys: ff_ke, ff_cap, ff_slew, ff_alpha, ff_stall, ff_move_eps,
ff_gain, ff_floor, arm_gain, arm_cap, max_rel, mode (off/gripper/arm/vwall), resync, stop.
Risk class: network only, but it changes a live teleop's feedback at once (the teleop bounds
every value; the new values show up in the telemetry's ``params`` and the panel's sliders).
"""

import argparse
import json
import socket

from koch.telemetry import TELEMETRY_HOST


def parse_pairs(items):
    """``key=value`` strings to a dict; numbers become numbers, everything else stays text."""
    out = {}
    for item in items:
        key, sep, value = item.partition("=")
        if not sep or not key:
            raise SystemExit(f"key=value の形で: {item!r}")
        try:
            out[key] = float(value) if "." in value else int(value)
        except ValueError:
            out[key] = value
    return out


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--ctl-port",
        default="8766",
        help="制御 UDP ポート(カンマ区切り。A 8766 / B 8768)",
    )
    ap.add_argument("pairs", nargs="+", help="key=value ...")
    args = ap.parse_args()
    cmd = parse_pairs(args.pairs)
    payload = json.dumps(cmd).encode()
    ports = [int(p) for p in args.ctl_port.split(",") if p.strip()]
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        for port in ports:
            sock.sendto(payload, (TELEMETRY_HOST, port))
    print(
        f"送信 {cmd} → 制御ポート {ports}（反映はパネルのスライダ／テレメトリの params で確認）"
    )


if __name__ == "__main__":
    main()
