"""Session watchdog for one koch teleop (network only; never opens a serial port).

Listens to a copy of the teleop telemetry (UDP) and stops the teleop when the base joint
(shoulder_pan) reading becomes impossible — the fault seen on pair A's follower on
2026-09-30 (Present_Position far outside one turn, jumps of ~1800 ticks at rest):

  sat    |follower pan| or |leader pan| pinned at the normalization limit (±100)
  jump   follower pan moves faster than the servo physically can between two frames
  stuck  |leader pan − follower pan| stays large (the base is not following)

Stop = {"stop": true} on the teleop's control port (clean exit: torque released, bus closed),
then SIGINT to the launcher if telemetry keeps flowing. It also writes a summary line every
10 s (fps, tracking error, currents, temperature, alerts) for the session record.
"""

import argparse
import json
import os
import signal
import socket
import subprocess
import time
from pathlib import Path

from koch.motors import MOTORS

JOINTS = MOTORS  # bus order, IDs 1-6


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--telemetry", type=int, default=8771)
    ap.add_argument("--ctl-port", type=int, default=8766)
    ap.add_argument(
        "--pidfile", type=Path, default=None, help="launcher PID (SIGINT fallback)"
    )
    ap.add_argument("--log", type=Path, required=True)
    ap.add_argument("--joint", default="shoulder_pan")
    ap.add_argument("--sat", type=float, default=99.5)
    ap.add_argument(
        "--jump",
        type=float,
        default=15.0,
        help="max plausible change per 1/30 s [norm]",
    )
    ap.add_argument(
        "--stuck",
        type=float,
        default=25.0,
        help="|L-F| considered not following [norm]",
    )
    ap.add_argument("--stuck-sec", type=float, default=1.5)
    ap.add_argument("--first-timeout", type=float, default=180.0)
    ap.add_argument("--idle-exit", type=float, default=15.0)
    ap.add_argument("--summary-sec", type=float, default=10.0)
    args = ap.parse_args()

    logf = open(args.log, "a", encoding="utf-8", buffering=1)  # noqa: SIM115 - lives as long as the process

    def log(msg):
        logf.write(f"{time.strftime('%H:%M:%S')} {msg}\n")

    rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rx.bind(("127.0.0.1", args.telemetry))
    rx.settimeout(0.5)
    tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    j = args.joint
    log(
        f"watchdog start: telemetry UDP {args.telemetry} / ctl UDP {args.ctl_port} / joint {j} / "
        f"sat {args.sat} / jump {args.jump}/frame / stuck {args.stuck} for {args.stuck_sec}s"
    )

    def stop_teleop(reason):
        log(f"TRIP: {reason} -> stop 要求を送信")
        for _ in range(3):
            tx.sendto(json.dumps({"stop": True}).encode(), ("127.0.0.1", args.ctl_port))
            time.sleep(0.01)
        if not still_flowing(1.5):
            log("teleop はテレメトリを止めました(clean stop)")
            return
        pid = None
        try:
            pid = int(args.pidfile.read_text().strip()) if args.pidfile else None
        except (OSError, ValueError):
            pid = None
        if pid:
            log(f"テレメトリが続くので launcher pid {pid} に SIGINT")
            try:
                os.kill(pid, signal.SIGINT)
            except OSError as e:
                log(f"SIGINT 失敗: {e}")
        if not still_flowing(4.0):
            log("teleop はテレメトリを止めました(SIGINT 後)")
            return
        out = subprocess.run(
            ["pgrep", "-f", "koch[.-]teleop"],
            capture_output=True,
            text=True,
            check=False,
        )
        for p in out.stdout.split():
            log(f"まだ続くので teleop pid {p} に直接 SIGINT")
            try:
                os.kill(int(p), signal.SIGINT)
            except (OSError, ValueError) as e:
                log(f"SIGINT 失敗: {e}")
        log(
            "停止を確認できません。電源を抜いてください"
            if still_flowing(4.0)
            else "teleop 停止"
        )

    def still_flowing(window):
        """True if a telemetry frame arrives during the last 0.5 s of `window`."""
        end = time.monotonic() + window
        last = None
        while time.monotonic() < end:
            try:
                rx.recvfrom(65535)
                last = time.monotonic()
            except (TimeoutError, OSError):
                pass
        return last is not None and end - last < 0.6

    t_start = time.monotonic()
    first = None
    last_rx = None
    prev = None  # (n, t, fpos_j)
    sat_f = sat_l = 0
    stuck_since = None
    win = new_window()
    next_summary = None
    frames = 0
    while True:
        now = time.monotonic()
        try:
            data, _ = rx.recvfrom(65535)
        except (TimeoutError, OSError):
            if first is None and now - t_start > args.first_timeout:
                log("テレメトリが一度も来ませんでした。終了")
                return 2
            if last_rx is not None and now - last_rx > args.idle_exit:
                if win["n"]:
                    log(summary(win))
                log(f"テレメトリ終了(計 {frames} フレーム)。watchdog 終了")
                return 0
            continue
        try:
            d = json.loads(data.decode())
        except ValueError:
            continue
        now = time.monotonic()
        frames += 1
        last_rx = now
        if first is None:
            first = now
            next_summary = now + args.summary_sec
            log(f"最初のフレーム受信: mode={d.get('mode')} params={d.get('params')}")
        pos, fpos = d.get("pos") or {}, d.get("fpos") or {}
        n, t = d.get("n"), d.get("t")
        accumulate(win, d, now)
        reason = None
        if j in fpos:
            f = float(fpos[j])
            sat_f = sat_f + 1 if abs(f) >= args.sat else 0
            if sat_f >= 2:
                reason = f"follower {j} が正規化の端に張り付き: {f}"
            if prev is not None and isinstance(n, int) and isinstance(t, (int, float)):
                pn, pt, pf = prev
                dt = float(t) - pt
                if n - pn >= 1 and 0 < dt < 0.5:
                    allowed = args.jump * max(1.0, dt * 30.0)
                    if abs(f - pf) > allowed:
                        reason = f"follower {j} が 1 フレームで {pf} -> {f} (dt={dt * 1000:.0f}ms, 許容 {allowed:.0f})"
            if isinstance(n, int) and isinstance(t, (int, float)):
                prev = (n, float(t), f)
            if j in pos:
                gap = abs(float(pos[j]) - f)
                if gap > args.stuck:
                    stuck_since = stuck_since or now
                    if now - stuck_since > args.stuck_sec:
                        reason = (
                            reason
                            or f"{j} の L-F 差 {gap:.1f} が {args.stuck_sec}s 以上続いた(追従していない)"
                        )
                else:
                    stuck_since = None
        if j in pos:
            sat_l = sat_l + 1 if abs(float(pos[j])) >= args.sat else 0
            if sat_l >= 3:
                reason = reason or f"leader {j} が正規化の端に張り付き: {pos[j]}"
        if reason:
            stop_teleop(reason)
            log(summary(win))
            return 1
        if next_summary is not None and now >= next_summary:
            log(summary(win))
            win = new_window()
            next_summary = now + args.summary_sec


def new_window():
    return {
        "n": 0,
        "t0": None,
        "t1": None,
        "err": {},
        "cur": {},
        "temp": 0,
        "rec": 0,
        "alerts": set(),
        "mode": None,
        "ff": 0,
        "max_rel": None,
        "pan": None,
        "hw": None,
    }


def accumulate(win, d, now):
    win["n"] += 1
    win["t0"] = win["t0"] or now
    win["t1"] = now
    pos, fpos, cur = d.get("pos") or {}, d.get("fpos") or {}, d.get("cur") or {}
    for m in JOINTS:
        if m in pos and m in fpos:
            win["err"][m] = max(
                win["err"].get(m, 0.0), abs(float(pos[m]) - float(fpos[m]))
            )
        if m in cur:
            win["cur"][m] = max(win["cur"].get(m, 0.0), abs(float(cur[m])))
    win["temp"] = max(win["temp"], int(d.get("temp") or 0))
    win["rec"] = int(d.get("rec") or 0)
    win["mode"] = d.get("mode")
    win["ff"] = max(win["ff"], abs(int(d.get("ff") or 0)))
    win["max_rel"] = (d.get("params") or {}).get("max_rel")
    win["hw"] = d.get("hw")
    if "shoulder_pan" in pos and "shoulder_pan" in fpos:
        win["pan"] = (pos["shoulder_pan"], fpos["shoulder_pan"])
    for a in d.get("alerts") or []:
        win["alerts"].add(str(a))


def summary(win):
    span = (win["t1"] - win["t0"]) if win["n"] > 1 else 0.0
    fps = (win["n"] - 1) / span if span > 0 else 0.0
    err = " ".join(f"{m.split('_')[-1]}={v:.1f}" for m, v in win["err"].items())
    cur = " ".join(f"{m.split('_')[-1]}={v:.0f}" for m, v in win["cur"].items())
    alerts = (" alerts=" + " | ".join(sorted(win["alerts"]))) if win["alerts"] else ""
    return (
        f"frames={win['n']} fps={fps:.1f} mode={win['mode']} max_rel={win['max_rel']} rec={win['rec']} hw={win['hw']} "
        f"pan(L,F)={win['pan']} max|L-F|[{err}] max|I|[{cur}] ff_max={win['ff']}mA temp={win['temp']}C{alerts}"
    )


if __name__ == "__main__":
    raise SystemExit(main())
