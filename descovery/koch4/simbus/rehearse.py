#!/usr/bin/env python3
"""Rehearse the koch4 launch paths with simulated servos (no arms, no ports opened).

Runs the real launcher, teleop, bridge, panel and follower host against the virtual
Dynamixel bus in this folder (``dynamixel_sdk/``) and checks what they did: frame rate,
feedback commands, register write order, refused EEPROM writes, torque at exit.

  uv run python koch4/simbus/rehearse.py            # every scenario
  uv run python koch4/simbus/rehearse.py vr2        # one: handshake | vr | vr2 | wireless | mismatch | dropout | fest
  uv run python koch4/simbus/rehearse.py --list

仮想のサーボで握手・VR・2 人・無線・較正不一致・通信断を通し、起動から終了までのソフトの
経路を確かめる。**実機の挙動は分からない**（力・摩擦・発熱・実時間は模擬していない）。
固定ポート（8765〜8773・8444・8780・9101）を使うので、実機のセッションと同時には動かさない。
Risk class: none (network on localhost only; the fake ports are plain files under work/).
"""

import argparse
import glob
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

_reconfigure = getattr(sys.stdout, "reconfigure", None)
if callable(_reconfigure):  # Windows cp932 console: never crash on symbols
    _reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
KOCH4 = HERE.parent
ROOT = KOCH4 / "work" / "rehearsal"
CAL = KOCH4 / "config" / "calibration"
IRON = {
    "name": "iron",
    "width": 60,
    "p_gain": 1200,
    "cap_ma": 400,
    "release": 2,
    "mass_g": 500,
}
PORTS_IN_USE = (8765, 8766, 8767, 8768, 8771, 8772)


class Rehearsal:
    """One scenario: its folder, the simulated arms, the children and the checks."""

    def __init__(self, name):
        self.name = name
        self.dir = ROOT / name
        if self.dir.exists():
            shutil.rmtree(self.dir)
        (self.dir / "ports").mkdir(parents=True)
        (self.dir / "cfg").mkdir()
        shutil.copytree(CAL, self.dir / "cfg" / "calibration")
        shutil.copy(KOCH4 / "config" / "koch4_twin.json", self.dir / "cfg")
        self.trace = self.dir / "trace.jsonl"
        self.children = []
        self.main = None  # the launcher: finish() waits for it
        self.results = []
        self.arms = {}
        for pair in "AB":
            self.arm(f"L{pair}", "leader", f"koch_leader_{pair}", hand=True)
            self.arm(f"F{pair}", "follower", f"koch_follower_{pair}", object=0.5)

    def port(self, tag):
        """Path of the fake port file of one arm."""
        return str(self.dir / "ports" / tag)

    def arm(self, tag, kind, arm_id, **options):
        """Declare (or redefine) a simulated arm."""
        Path(self.port(tag)).touch()
        folder = "koch_leader" if kind == "leader" else "koch_follower"
        self.arms[self.port(tag)] = {
            "kind": kind,
            "tag": tag,
            "calibration": str(
                self.dir / "cfg" / "calibration" / folder / f"{arm_id}.json"
            ),
            **options,
        }

    def change(self, tag, **options):
        """Change options of an arm already declared."""
        self.arms[self.port(tag)].update(options)

    def write(self, follower_host=None):
        """Write the scenario and the launcher config."""
        scenario = {"ports": self.arms, "trace": str(self.trace)}
        (self.dir / "scenario.json").write_text(
            json.dumps(scenario, indent=1), encoding="utf-8"
        )
        pairs = {
            pair: {
                "leader_port": self.port(f"L{pair}"),
                "follower_port": self.port(f"F{pair}"),
                "leader_serial": None,
                "follower_serial": None,
                "leader_id": f"koch_leader_{pair}",
                "follower_id": f"koch_follower_{pair}",
                "follower_host": (follower_host or {}).get(pair),
                "cams": "",
            }
            for pair in "AB"
        }
        config = json.dumps({"pairs": pairs}, indent=1)
        (self.dir / "cfg" / "koch4_config.json").write_text(config, encoding="utf-8")

    def start(self, script, *args, log):
        """Start one koch4 script with the simulated bus on its path."""
        env = dict(os.environ)
        env["PYTHONPATH"] = str(HERE) + os.pathsep + env.get("PYTHONPATH", "")
        env["KOCH4_SIMBUS"] = str(self.dir / "scenario.json")
        env["PYTHONIOENCODING"] = "utf-8"
        out = open(self.dir / log, "w", encoding="utf-8")  # noqa: SIM115 - closed in finish()
        proc = subprocess.Popen(
            [sys.executable, str(KOCH4 / script), *args],
            stdout=out,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            env=env,
        )
        self.children.append((proc, out))
        return proc

    def launch(self, *args):
        """Start the launcher with this scenario's config and work folders."""
        self.main = self.start(
            "koch4_dual_launch.py",
            *args,
            "--config-dir",
            str(self.dir / "cfg"),
            "--work-dir",
            str(self.dir / "work"),
            log="launcher.log",
        )
        return self.main

    def check(self, label, ok, detail=""):
        """Record one check."""
        self.results.append((label, bool(ok), str(detail)))

    def log(self, relative):
        """Text of a log under the scenario folder ('' when missing)."""
        try:
            return (self.dir / relative).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    def records(self):
        """Every trace record written by the simulated bus (all processes)."""
        out = []
        for path in sorted(glob.glob(str(self.trace) + ".*")):
            with open(path, encoding="utf-8") as f:
                for line in f:
                    try:
                        out.append(json.loads(line))
                    except ValueError:
                        continue
        return out

    def finish(self, ctl_ports, timeout=12.0):
        """Ask the teleops to stop, wait for every child, then kill what is left."""
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            for port in ctl_ports:
                s.sendto(b'{"stop": true}', ("127.0.0.1", port))
        deadline = time.monotonic() + timeout
        first = self.main
        while (
            first is not None and first.poll() is None and time.monotonic() < deadline
        ):
            time.sleep(0.2)
        for proc, out in self.children:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
            out.close()
        return first.returncode if first is not None else None

    def bus_checks(self, expect_arms):
        """Checks every scenario shares: refused writes and the state the servos were left in."""
        recs = self.records()
        rejects = [r for r in recs if "reject" in r]
        self.check("EEPROM への拒否された書込みなし", not rejects, rejects[:2])
        last = {}
        for r in recs:
            if "dump" in r:
                last[r["port"]] = r["servos"]
        missing = [a for a in expect_arms if a not in last]
        self.check("使った腕がすべて切断まで進んだ", not missing, missing)
        on = [
            f"{arm}:{m}"
            for arm, servos in last.items()
            for m, v in servos.items()
            if v["torque"]
        ]
        self.check("終了時に全軸トルク OFF", not on, on)
        return recs, last


def listen(ports, seconds):
    """Collect telemetry frames per UDP port for a while."""
    socks = {}
    for port in ports:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.bind(("127.0.0.1", port))
        s.setblocking(False)
        socks[port] = s
    frames = {port: [] for port in ports}
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        idle = True
        for port, s in socks.items():
            try:
                frames[port].append(json.loads(s.recvfrom(65535)[0].decode()))
                idle = False
            except (BlockingIOError, OSError, ValueError):
                continue
        if idle:
            time.sleep(0.005)
    for s in socks.values():
        s.close()
    return frames


def http_json(port, route, body=None, timeout=3.0):
    """GET (or POST a JSON body to) the bridge / panel on localhost."""
    url = f"http://127.0.0.1:{port}{route}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        text = resp.read().decode()
    try:
        return json.loads(text)
    except ValueError:
        return text


def wait_http(port, route, seconds=25.0):
    """Wait until a local server answers."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        try:
            http_json(port, route, timeout=1.0)
            return True
        except Exception:  # noqa: BLE001 - not up yet
            time.sleep(0.4)
    return False


def fps(frames, seconds):
    """Frames per second, rounded."""
    return round(len(frames) / seconds, 1)


def grip_range(frames, key):
    """(min, max) of the gripper value under key ('pos', 'fpos', 'cur') in telemetry."""
    values = [f[key]["gripper"] for f in frames if f.get(key) and "gripper" in f[key]]
    return (min(values), max(values)) if values else (None, None)


def squeeze(port, arm, seconds):
    """Post the iron block as the contact of one arm, like the page does while touching."""
    seen = []
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        http_json(port, "/contact", {"arm": arm, "vwall": IRON})
        state = (http_json(port, "/state").get("arms") or {}).get(arm, {})
        seen.append(state)
        time.sleep(0.25)
    return seen


# ------------------------------------------------------------------ scenarios


def handshake(r):
    """Two pairs, wired, with the panel: the handshake demo."""
    r.write()
    r.launch("--ff", "gripper", "--grip-ma", "500", "--no-browser")
    up = wait_http(8780, "/")
    r.check("パネルが応答", up)
    time.sleep(9.0)
    pairs = {}
    try:
        with urllib.request.urlopen("http://127.0.0.1:8780/stream", timeout=5) as resp:
            end = time.monotonic() + 7.0
            while time.monotonic() < end:
                line = resp.readline().decode("utf-8", errors="replace")
                if not line.startswith("data:"):
                    continue
                event = json.loads(line[5:])
                for label, frame in zip(
                    event.get("labels", []), event.get("pairs", [])
                ):
                    if frame:
                        pairs.setdefault(label, []).append(frame)
    except Exception as e:  # noqa: BLE001 - reported as a failed check
        r.check("パネルの配信を読めた", False, e)
    for label in "AB":
        frames = pairs.get(label, [])
        ff = [f.get("ff", 0) for f in frames]
        r.check(
            f"ペア {label} のテレメトリがパネルに届く", len(frames) >= 5, len(frames)
        )
        r.check(
            f"ペア {label} の握り返しが床より強くなった",
            bool(ff) and max(ff) > 100,
            max(ff, default=0),
        )
        r.check(
            f"ペア {label} の握り返しが上限 450 mA 以内",
            bool(ff) and max(ff) <= 450,
            max(ff, default=0),
        )
    code = r.finish([8766, 8768])
    r.check("ランチャが正常終了", code == 0, code)
    recs, _ = r.bus_checks(["LA", "FA", "LB", "FB"])
    caps = [
        x
        for x in recs
        if x.get("reg") == "Goal_Current"
        and x.get("motor") == "gripper"
        and x.get("value") == 500
    ]
    r.check(
        "フォロワー握力の上限 500 mA を両ペアに書いた",
        {x["port"] for x in caps} >= {"FA", "FB"},
        len(caps),
    )
    gains = [
        x for x in recs if x.get("reg") == "Position_P_Gain" and x.get("value") == 800
    ]
    r.check(
        "リーダー gripper の P ゲイン 800 をモード書込みの後に書いた",
        {x["port"] for x in gains} >= {"LA", "LB"},
    )


def vr(r):
    """One player: leader B only (the follower is not used and need not be connected)."""
    Path(r.port("FB")).unlink()  # the follower is not plugged in
    r.write()
    r.launch(
        "--pair",
        "B",
        "--vr",
        "B",
        "--vw",
        "--vr-http",
        "--no-panel",
        "--follower",
        "none",
    )
    up = wait_http(8444, "/state")
    r.check(
        "ブリッジが応答（フォロワー未接続でも起動）",
        up,
        r.log("launcher.log")[-300:] if not up else "",
    )
    if not up:
        r.finish([8768])
        return
    page = http_json(8444, "/index.html")
    r.check("ページを配信", isinstance(page, str) and "three.module.js" in page)
    time.sleep(4.0)
    seen = squeeze(8444, "B", 8.0)
    engaged = [s for s in seen if (s.get("vwall") or {}).get("engaged")]
    r.check("握ると壁に接触（engaged）", bool(engaged), len(engaged))
    vw = [abs(v) for s in engaged for v in (s.get("vw") or {}).values()]
    r.check(
        "握っている間、肩と肘に重さの電流",
        bool(vw) and max(vw) > 20,
        max(vw, default=0),
    )
    r.check(
        "重さの電流が上限 120 mA 以内", bool(vw) and max(vw) <= 120, max(vw, default=0)
    )
    units = (seen[-1].get("vw_unit") or {}) if seen else {}
    r.check(
        "リーダー機の重さは肩も肘も電流（mA）", set(units.values()) == {"mA"}, units
    )
    code = r.finish([8768])
    r.check("ランチャが正常終了", code == 0, code)
    recs, _ = r.bus_checks(["LB"])
    wall = [
        x
        for x in recs
        if x["port"] == "LB"
        and x.get("motor") == "gripper"
        and x.get("reg") == "Goal_Current"
    ]
    r.check(
        "壁の電流上限 400 mA を書いた",
        any(x["value"] == 400 for x in wall),
        sorted({x["value"] for x in wall}),
    )
    r.check(
        "フォロワーには何も書いていない", not [x for x in recs if x.get("port") == "FB"]
    )


def vr2(r):
    """Two players: leader B plus follower B moved by hand (shoulder weight by PWM)."""
    r.change("FB", hand=True, object=None)
    r.write()
    r.launch("--pair", "B", "--vr", "B", "--vw", "--vr-http", "--no-panel", "--vr2")
    up = wait_http(8444, "/state")
    r.check("ブリッジが応答", up)
    if not up:
        r.finish([8768, 8772])
        return
    time.sleep(5.0)
    arms = http_json(8444, "/state").get("arms") or {}
    r.check("分身が 2 体（B と F）", set(arms) == {"B", "F"}, sorted(arms))
    seen = squeeze(8444, "F", 9.0)
    engaged = [s for s in seen if (s.get("vwall") or {}).get("engaged")]
    r.check("2 人目も握ると壁に接触", bool(engaged), len(engaged))
    units = (seen[-1].get("vw_unit") or {}) if seen else {}
    r.check(
        "2 人目の肩は PWM・肘は電流",
        units == {"shoulder_lift": "PWM", "elbow_flex": "mA"},
        units,
    )
    code = r.finish([8768, 8772])
    r.check("ランチャが正常終了", code == 0, code)
    recs, last = r.bus_checks(["LB", "FB"])
    sh = [
        (x["reg"], x["value"])
        for x in recs
        if x["port"] == "FB" and x.get("motor") == "shoulder_lift" and "reg" in x
    ]
    names = [n for n, _ in sh]
    setup = (
        names.index("Operating_Mode", names.index("Operating_Mode") + 1)
        if names.count("Operating_Mode") > 1
        else -1
    )
    ordered = (
        setup >= 0
        and sh[setup] == ("Operating_Mode", 16)
        and sh[setup + 1] == ("Goal_PWM", 0)
    )
    r.check(
        "肩: PWM モードにした直後に Goal_PWM=0",
        ordered,
        sh[setup : setup + 3] if setup >= 0 else sh[:6],
    )
    ons = [i for i, x in enumerate(sh) if x == ("Torque_Enable", 1) and i > setup]
    zero_first = all(sh[i - 1] == ("Goal_PWM", 0) for i in ons)
    r.check("肩: トルク ON の直前は必ず Goal_PWM=0", bool(ons) and zero_first, len(ons))
    duty = [abs(v) for n, v in sh[setup:] if n == "Goal_PWM" and v != 885]
    r.check(
        "肩: PWM が上限 150 以内で出た",
        bool(duty) and 0 < max(duty) <= 150,
        max(duty, default=0),
    )
    wall = [
        x["value"]
        for x in recs
        if x["port"] == "FB"
        and x.get("motor") == "gripper"
        and x.get("reg") == "Goal_Current"
    ]
    r.check(
        "2 人目の壁の電流は 0.45 倍（400 → 180 mA）",
        180 in wall and 400 not in wall,
        sorted(set(wall)),
    )
    shoulder = (last.get("FB") or {}).get("shoulder_lift", {})
    restored = shoulder.get("mode") == 4 and shoulder.get("goal_pwm") == 885
    r.check("肩: 終了時に位置モードと PWM 上限 885 へ復帰", restored, shoulder)


def wireless(r):
    """Leader on this PC, follower behind koch4_follower_host.py (UDP)."""
    r.write(follower_host={"A": "udp://127.0.0.1:9101"})
    host = r.start(
        "koch4_follower_host.py",
        "--port",
        r.port("FA"),
        "--id",
        "koch_follower_A",
        "--listen",
        "9101",
        "--grip-ma",
        "500",
        "--config-dir",
        str(r.dir / "cfg"),
        log="host.log",
    )
    time.sleep(5.0)
    r.check(
        "フォロワー host が動いている", host.poll() is None, r.log("host.log")[-300:]
    )
    r.launch("--pair", "A", "--ff", "gripper", "--grip-ma", "500", "--no-panel")
    time.sleep(8.0)
    frames = listen([8765], 5.0)[8765]
    r.check("無線フォロワーで 25 fps 以上", fps(frames, 5.0) >= 25, fps(frames, 5.0))
    link = [f.get("link") for f in frames if f.get("link")]
    r.check(
        "無線の状態（遅延・欠落）がテレメトリに載る",
        bool(link),
        link[-1] if link else None,
    )
    low, _ = grip_range(frames, "fpos")
    r.check(
        "フォロワー gripper が物に当たって止まる（50 付近）",
        low is not None and 45 <= low <= 55,
        low,
    )
    ff = [f.get("ff", 0) for f in frames]
    r.check(
        "握り返しが床より強くなった", bool(ff) and max(ff) > 100, max(ff, default=0)
    )
    code = r.finish([8766])
    r.check("ランチャが正常終了", code == 0, code)
    host_log = r.log("host.log")
    r.check(
        "host のログに毎フレームのクランプ警告が出ない",
        "clamped to be safe" not in host_log,
    )
    r.check("目標が止まると host は位置を保持", "位置保持" in host_log)
    recs = r.records()
    r.check("EEPROM への拒否された書込みなし", not [x for x in recs if "reject" in x])


def mismatch(r):
    """The leader's servos hold another calibration than the file: refuse to start."""
    r.change("LA", eeprom_mismatch=True)
    r.write()
    proc = r.launch("--pair", "A", "--ff", "gripper", "--no-panel")
    end = time.monotonic() + 30
    while proc.poll() is None and time.monotonic() < end:
        time.sleep(0.3)
    r.check("起動が止まって終わる（待ち続けない）", proc.poll() is not None)
    code = r.finish([8766])
    out = r.log("launcher.log")
    r.check(
        "ランチャの画面に理由が出る",
        "[calib]" in out and "一致しません" in out,
        out[-200:],
    )
    r.check("ランチャは異常終了を返す", code not in (0, None), code)
    recs = r.records()
    eeprom = [
        x
        for x in recs
        if x.get("reg") in ("Homing_Offset", "Min_Position_Limit", "Max_Position_Limit")
    ]
    r.check("サーボの EEPROM（較正値）には書いていない", not eeprom, eeprom[:2])
    last = {x["port"]: x["servos"] for x in recs if "dump" in x}
    on = [
        f"{arm}:{m}"
        for arm, servos in last.items()
        for m, v in servos.items()
        if v["torque"]
    ]
    r.check("止めた後に全軸トルク OFF", not on, on)


def dropout(r):
    """The follower bus drops for 1.5 s, then the leader gripper latches an overload."""
    r.change("FA", drop_at_s=9.0, drop_for_s=1.5)
    r.change("LA", hw_error_at_s=20.0)
    r.write()
    proc = r.launch("--pair", "A", "--ff", "gripper", "--grip-ma", "500", "--no-panel")
    time.sleep(22.0)
    frames = listen([8765], 4.0)[8765]
    r.check(
        "通信断の後もテレオペが続いている",
        proc.poll() is None and fps(frames, 4.0) >= 25,
        fps(frames, 4.0),
    )
    rec = max((f.get("rec", 0) for f in frames), default=0)
    r.check("再接続が 1 回記録された", rec == 1, rec)
    alerts = " ".join(a for f in frames for a in f.get("alerts", []))
    r.check("過負荷エラーの警告がテレメトリに載る", "エラー停止" in alerts, alerts[:80])
    code = r.finish([8766])
    r.check("ランチャが正常終了", code == 0, code)
    log = r.log("work/logs/dual_A_teleop.log")
    r.check("再接続で握り返しの基準を引き継いだ", "再接続で引き継ぎ" in log)
    r.bus_checks(["LA", "FA"])


def fest(r):
    """Handshake on pair A and VR on pair B at the same time, from two launchers."""
    Path(r.port("FB")).unlink()  # VR for one player: follower B is not plugged in
    r.write()
    hs = r.launch("--pair", "A", "--ff", "gripper", "--grip-ma", "500", "--no-browser")
    vr_args = [
        "--pair",
        "B",
        "--vr",
        "B",
        "--vw",
        "--vr-http",
        "--no-panel",
        "--follower",
        "none",
    ]
    dirs = ["--config-dir", str(r.dir / "cfg"), "--work-dir", str(r.dir / "work")]
    vr_launcher = r.start(
        "koch4_dual_launch.py", *vr_args, *dirs, log="launcher_vr.log"
    )
    r.check("握手（A）のパネルが応答", wait_http(8780, "/"))
    r.check("VR（B）のブリッジが応答", wait_http(8444, "/state"))
    time.sleep(6.0)
    seen = squeeze(8444, "B", 5.0)
    r.check(
        "握手が動いている間も VR で壁に接触",
        any((s.get("vwall") or {}).get("engaged") for s in seen),
    )
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:  # stop the VR side only
        s.sendto(b'{"stop": true}', ("127.0.0.1", 8768))
    end = time.monotonic() + 12
    while vr_launcher.poll() is None and time.monotonic() < end:
        time.sleep(0.3)
    r.check(
        "VR 側のランチャだけが終わる",
        vr_launcher.poll() == 0 and hs.poll() is None,
        vr_launcher.poll(),
    )
    frames = []
    try:
        with urllib.request.urlopen("http://127.0.0.1:8780/stream", timeout=5) as resp:
            end = time.monotonic() + 4.0
            while time.monotonic() < end:
                line = resp.readline().decode("utf-8", errors="replace")
                if line.startswith("data:"):
                    frames += [f for f in json.loads(line[5:]).get("pairs", []) if f]
    except Exception as e:  # noqa: BLE001 - reported as a failed check
        r.check("パネルの配信を読めた", False, e)
    steps = {f.get("n") for f in frames}
    r.check("VR を止めた後も握手（A）は続いている", len(steps) >= 5, len(steps))
    code = r.finish([8766])
    r.check("握手側のランチャが正常終了", code == 0, code)
    r.bus_checks(["LA", "FA", "LB"])


SCENARIOS = {
    "handshake": handshake,
    "vr": vr,
    "vr2": vr2,
    "wireless": wireless,
    "mismatch": mismatch,
    "dropout": dropout,
    "fest": fest,
}


def ports_busy():
    """UDP ports of a running session that the rehearsal would collide with."""
    busy = []
    for port in PORTS_IN_USE:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.bind(("127.0.0.1", port))
        except OSError:
            busy.append(port)
        finally:
            s.close()
    return busy


def main():
    """Run the chosen scenarios and print every check."""
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("scenario", nargs="*", help="既定は全部")
    ap.add_argument("--list", action="store_true", help="シナリオ名を表示")
    args = ap.parse_args()
    if args.list:
        for name, fn in SCENARIOS.items():
            print(f"{name:<10} {(fn.__doc__ or '').strip().splitlines()[0]}")
        return
    names = args.scenario or list(SCENARIOS)
    unknown = [n for n in names if n not in SCENARIOS]
    if unknown:
        sys.exit(f"知らないシナリオ: {unknown}（--list で一覧）")
    if not (KOCH4 / "webxr" / "three.module.js").exists():
        sys.exit(
            "three.module.js がありません → python koch4/webxr/setup_assets.py を一度実行"
        )
    busy = ports_busy()
    if busy:
        sys.exit(
            f"ポート {busy} が使用中です。実機のセッションを止めてから実行してください"
        )
    print("仮想のサーボで通します（腕には触れません・実機の挙動は分かりません）")
    failed = 0
    for name in names:
        r = Rehearsal(name)
        t0 = time.monotonic()
        try:
            SCENARIOS[name](r)
        except Exception as e:  # noqa: BLE001 - a scenario must never leave children behind
            r.check("シナリオが最後まで走った", False, f"{type(e).__name__}: {e}")
            r.finish([8766, 8768, 8772], timeout=3.0)
        bad = [x for x in r.results if not x[1]]
        failed += len(bad)
        print(
            f"\n[{name}] {len(r.results) - len(bad)}/{len(r.results)} ({time.monotonic() - t0:.0f} 秒)  ログ: {r.dir}"
        )
        for label, ok, detail in r.results:
            print(
                f"  {'✓' if ok else '✗'} {label}"
                + (f"  → {detail}" if not ok and detail else "")
            )
    print(
        f"\n{'すべて通りました' if not failed else f'{failed} 件が通っていません'}（仮想のサーボでの結果）"
    )
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
