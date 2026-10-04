#!/usr/bin/env python3
r"""Follower host: drive one Koch follower from joint targets received over UDP.

握手の場に置くコンピュータで動かす。Raspberry Pi 4 でも、手持ちの Windows／Mac／Linux の PC でも
よい（lerobot と CH343 ボードの USB があれば同じ）。Mac の `koch4_teleop.py --follower-port
udp://<このホストの IP>:9101` から 30fps で目標角を受け取り、lerobot の KochFollower で動かし、
電流・位置・温度・HW エラーを返す。lerobot 公式 LeKiwi の Pi 側 host と同じ役割で、向きも同じ
（リーダーは Mac に有線、フォロワーが無線）。

Risk class: moves motors（起動時にフォロワーへ接続し、目標角を受けたら動く）。
安全:
  - 目標角が 0.5 秒来なければ新しい目標を書かない＝**位置保持**（トルクは抜かない＝腕が落ちない）
  - 3 秒以上途絶えたあと再開したら 1.5 秒の合流ランプで滑らかに追従へ戻す（跳ね防止）
  - `max_relative_target`（1 ステップの移動量上限）は host 側の KochFollower に効く。Mac から cfg で更新可
  - `--grip-ma` でフォロワー gripper の Goal_Current 上限（過負荷停止対策）。Mac の --follower-grip-ma も cfg で届く
  - Ctrl+C で終了。lerobot の既定どおり切断時にトルクが抜けるので、腕を畳んでから止める

使い方（握手の場の PC・descovery で `uv run`）:
  uv run python koch4/koch4_follower_host.py --port /dev/tty.usbmodemXXXX --id koch_follower_A --listen 9101
  uv run python koch4/koch4_follower_host.py --sim --listen 9101          # ハード無し（配線・遅延の確認）
  Windows:  python koch4\\koch4_follower_host.py --port COM5 --id koch_follower_A --listen 9101
  Mac 側:   koch4_dual_launch.py の config に "follower_host": "udp://192.168.x.x:9101" を書く

プロトコル（UDP・JSON 1 メッセージ 1 データグラム。ポートはペア A=9101 / B=9102）:
  Mac → host  {"type":"hello"}
              {"type":"action","seq":N,"t":送信時刻,"action":{"shoulder_pan.pos":…,"gripper.pos":…}}
              {"type":"cfg","max_rel":20,"grip_ma":500}
  host → Mac  {"type":"state","n":host の連番,"seq":最後に適用した action の seq,"echo_t":その送信時刻,
               "t":host 時刻,"cur":{生 16bit},"fpos":{正規化},"temp":{"gripper":°C},"hw":{motor:bits},
               "grip_ma":上限,"health":"ok|alert|hold|stale","age_ms":最後の目標からの経過}
"""

import argparse
import json
import math
import socket
import sys
import time
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace

_reconfigure = getattr(sys.stdout, "reconfigure", None)
if callable(_reconfigure):  # Windows cp932 console: never crash on symbols
    _reconfigure(errors="replace")

HERE = Path(__file__).resolve().parent
DEFAULT_CONFIG_DIR = HERE / "config"
MOTORS = [
    "shoulder_pan",
    "shoulder_lift",
    "elbow_flex",
    "wrist_flex",
    "wrist_roll",
    "gripper",
]
RAMP_SEC = 1.5
SLOW_EVERY = 60  # frames between temperature / hardware-error reads (2 s at 30 fps)
STATUS_EVERY = 60


# ---- mirror of twinarm/src/twinarm/domain/link_watchdog.py (keep in sync) ----


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
    dropped: int = 0
    missed: int = 0


def accept_packet(state, seq, now):
    """Register a packet; only a newer sequence number is accepted."""
    if seq <= state.last_seq:
        return replace(state, dropped=state.dropped + 1), False
    gap = seq - state.last_seq - 1 if state.last_seq >= 0 else 0
    return (
        LinkState(seq, now, state.received + 1, state.dropped, state.missed + gap),
        True,
    )


def link_health(state, now, policy=DEFAULT_LINK_POLICY):
    """Grade the link by the age of the last accepted packet."""
    age = now - state.last_rx
    if state.received == 0 or age >= policy.stale_after_s:
        return "stale"
    if age >= policy.hold_after_s:
        return "hold"
    if age >= policy.alert_after_s:
        return "alert"
    return "ok"


# ---------------------------------------------------------------- sim follower


class SimBus:
    """Register reads/writes of the simulated follower."""

    def __init__(self, follower):
        self.follower = follower
        self.motors = list(MOTORS)

    def sync_read(self, data_name, motors=None, *, normalize=True, num_retry=0):
        """Positions follow the goals with a lag; currents grow with the error."""
        f = self.follower
        if data_name == "Present_Position":
            return dict(f.pos)
        if data_name == "Present_Current":
            return {m: int(abs(f.goal[m] - f.pos[m]) * 20) & 0xFFFF for m in MOTORS}
        if data_name == "Hardware_Error_Status":
            return dict.fromkeys(MOTORS, 0)
        raise KeyError(data_name)

    def read(self, data_name, motor, *, normalize=True, num_retry=0):
        """Temperature, gripper cap and error bits."""
        if data_name == "Present_Temperature":
            return 36
        if data_name == "Goal_Current":
            return self.follower.grip_ma
        if data_name == "Hardware_Error_Status":
            return 0
        raise KeyError(data_name)

    def write(self, data_name, motor, value, *, normalize=True, num_retry=0):
        """Only the gripper current cap is modelled."""
        if data_name == "Goal_Current" and motor == "gripper":
            self.follower.grip_ma = int(value)


class SimFollower:
    """A stand-in for lerobot's KochFollower (no hardware)."""

    def __init__(self, max_rel):
        self.pos = dict.fromkeys(MOTORS, 0.0)
        self.pos["gripper"] = 50.0
        self.goal = dict(self.pos)
        self.grip_ma = 0
        self.config = SimpleNamespace(max_relative_target=max_rel)
        self.bus = SimBus(self)
        self.is_connected = False

    def connect(self):
        """Pretend to open the bus."""
        self.is_connected = True

    def disconnect(self):
        """Pretend to close the bus."""
        self.is_connected = False

    def send_action(self, action):
        """Accept normalized goals (clamped like lerobot would)."""
        limit = self.config.max_relative_target
        for key, value in action.items():
            m = key.replace(".pos", "")
            if m in self.goal:
                target = float(value)
                if limit:
                    target = max(min(target, self.pos[m] + limit), self.pos[m] - limit)
                self.goal[m] = target
        return action

    def step(self, dt):
        """First-order lag toward the goals."""
        k = min(1.0, dt * 8.0)
        for m in MOTORS:
            self.pos[m] += (self.goal[m] - self.pos[m]) * k


# ------------------------------------------------------------------ real follower


def open_real_follower(args):
    """Create lerobot's KochFollower with the koch4 calibration folder."""
    from lerobot.robots.koch_follower import KochFollower, KochFollowerConfig

    cal = (
        None
        if args.lerobot_cache
        else args.config_dir / "calibration" / "koch_follower"
    )
    return KochFollower(
        KochFollowerConfig(
            port=args.port,
            id=args.id,
            calibration_dir=cal,
            max_relative_target=float(args.max_rel) if args.max_rel > 0 else None,
        )
    )


def to_signed16(v):
    """Interpret a 16-bit register value as signed."""
    return v - 65536 if v > 32767 else v


def build_parser():
    """Define the command line."""
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--port",
        default=None,
        help="フォロワーのシリアルポート(ユーザーが渡す。推測しない)",
    )
    ap.add_argument("--sim", action="store_true", help="ハード無しの模擬フォロワー")
    ap.add_argument("--id", default="koch_follower_A", help="較正ファイル名(<id>.json)")
    ap.add_argument("--config-dir", type=Path, default=DEFAULT_CONFIG_DIR)
    ap.add_argument(
        "--lerobot-cache", action="store_true", help="較正を lerobot 既定の場所から読む"
    )
    ap.add_argument(
        "--listen", type=int, default=9101, help="待ち受け UDP ポート(A=9101 / B=9102)"
    )
    ap.add_argument(
        "--bind",
        default="0.0.0.0",
        help="待ち受けアドレス(既定 0.0.0.0=LAN から受ける)",
    )
    ap.add_argument("--fps", type=float, default=30)
    ap.add_argument(
        "--max-rel", type=float, default=20, help="max_relative_target(安全リミッタ)"
    )
    ap.add_argument(
        "--grip-ma",
        type=int,
        default=None,
        help="フォロワー gripper の Goal_Current 上限[mA]",
    )
    return ap


def main():
    """Serve one follower over UDP until Ctrl+C."""
    args = build_parser().parse_args()
    if not args.sim and not args.port:
        sys.exit("--port を指定するか --sim を付けてください(ポートは推測しません)")
    follower = (
        SimFollower(float(args.max_rel) if args.max_rel > 0 else None)
        if args.sim
        else open_real_follower(args)
    )
    follower.connect()
    if args.grip_ma is not None:
        follower.bus.write(
            "Goal_Current", "gripper", int(args.grip_ma), normalize=False
        )
    grip_ma = args.grip_ma

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((args.bind, args.listen))
    sock.setblocking(False)
    print(
        f"[host] {'SIM' if args.sim else args.port} を UDP {args.bind}:{args.listen} で待ち受け"
        f"（Mac 側: --follower-port udp://<この PC の IP>:{args.listen}）"
    )

    policy = LinkPolicy()
    link = LinkState()
    peer = None
    latest = None  # (seq, t_sent, action)
    applied_seq = -1
    echo_t = None
    ramp_from = {}  # start pose for the ramp after a gap (empty = no ramp)
    ramp_t0 = 0.0
    was_holding = True
    temp = {}
    hw = {}
    n = 0
    t_prev = time.perf_counter()

    def send_state(cur, fpos, health, age_ms):
        if peer is None:
            return
        msg = {
            "type": "state",
            "n": n,
            "seq": applied_seq,
            "echo_t": echo_t,
            "t": round(time.monotonic(), 4),
            "cur": {m: int(v) for m, v in cur.items()},
            "fpos": {m: round(float(v), 2) for m, v in fpos.items()},
            "temp": temp,
            "hw": hw,
            "grip_ma": grip_ma,
            "health": health,
            "age_ms": age_ms,
        }
        try:
            sock.sendto(json.dumps(msg).encode(), peer)
        except OSError:
            pass

    try:
        while True:
            t_frame = time.perf_counter()
            now = time.monotonic()
            # ---- drain the socket
            while True:
                try:
                    data, addr = sock.recvfrom(65535)
                except (BlockingIOError, OSError):
                    break
                try:
                    msg = json.loads(data.decode())
                except ValueError:
                    continue
                kind = msg.get("type")
                if kind == "hello":
                    peer = addr
                    print(f"[host] Mac から接続: {addr[0]}:{addr[1]}")
                elif kind == "action":
                    peer = addr
                    link, ok = accept_packet(link, int(msg.get("seq", -1)), now)
                    if ok:
                        latest = (int(msg["seq"]), msg.get("t"), msg.get("action", {}))
                elif kind == "cfg":
                    if "max_rel" in msg:
                        follower.config.max_relative_target = (
                            float(msg["max_rel"]) or None
                        )
                    if "grip_ma" in msg and msg["grip_ma"] is not None:
                        grip_ma = int(msg["grip_ma"])
                        follower.bus.write(
                            "Goal_Current", "gripper", grip_ma, normalize=False
                        )
                        print(f"[host] gripper Goal_Current 上限 = {grip_ma}mA")
            # ---- follow (hold the pose when the link is quiet)
            health = link_health(link, now, policy)
            if (
                health in ("ok", "alert")
                and latest is not None
                and latest[0] > applied_seq
            ):
                seq, t_sent, action = latest
                if was_holding:  # 途絶からの復帰: 現在位置から 1.5 秒で合流
                    ramp_from = follower.bus.sync_read("Present_Position")
                    ramp_t0 = now
                    was_holding = False
                    print("\n[host] 目標の受信を再開 → 合流ランプ")
                a = min(1.0, (now - ramp_t0) / RAMP_SEC) if ramp_from else 1.0
                blended = (
                    {
                        k: (1 - a) * float(ramp_from.get(k.replace(".pos", ""), v))
                        + a * float(v)
                        for k, v in action.items()
                    }
                    if a < 1.0
                    else action
                )
                follower.send_action(blended)
                applied_seq, echo_t = seq, t_sent
            elif health in ("hold", "stale"):
                if not was_holding:
                    print(
                        f"\n[host] 目標が {policy.hold_after_s}s 来ない → 位置保持(トルクは維持)"
                    )
                was_holding = True
            if args.sim:
                follower.step(time.perf_counter() - t_prev)
            t_prev = time.perf_counter()
            # ---- read back
            cur = follower.bus.sync_read("Present_Current", normalize=False)
            fpos = follower.bus.sync_read("Present_Position")
            if n % SLOW_EVERY == 0:
                temp = {
                    "gripper": int(
                        follower.bus.read(
                            "Present_Temperature", "gripper", normalize=False
                        )
                    )
                }
                try:
                    hw = {
                        m: int(v)
                        for m, v in follower.bus.sync_read(
                            "Hardware_Error_Status", normalize=False
                        ).items()
                    }
                except Exception:  # noqa: BLE001 - diagnostics only
                    hw = {}
            age_ms = int((now - link.last_rx) * 1000) if link.received else -1
            send_state(cur, fpos, health, age_ms)
            if n % STATUS_EVERY == 0:
                grip_cur = to_signed16(int(cur.get("gripper", 0)))
                print(
                    f"\r[host] link={health:5s} age={age_ms:5d}ms seq={applied_seq} "
                    f"grip={grip_cur:4d}mA temp={temp.get('gripper', '-')}C   ",
                    end="",
                    flush=True,
                )
            n += 1
            time.sleep(max(0.0, 1.0 / args.fps - (time.perf_counter() - t_frame)))
    except KeyboardInterrupt:
        print("\n終了処理中…")
    finally:
        try:
            follower.disconnect()
        except Exception:  # noqa: BLE001, S110 - the bus may already be gone
            pass
        sock.close()
        print("完了。使用後は電源を抜くこと(過熱防止)")


if __name__ == "__main__":
    main()
