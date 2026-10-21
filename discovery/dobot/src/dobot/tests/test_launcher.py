"""ランチャー（素人向けの起動・停止・調整の画面）のオフラインテスト。子プロセスは偽物で。"""
from __future__ import annotations

import json
import urllib.request

from dobot.config import AppConfig
from dobot.launcher import Launcher, LauncherServer, apply_settings, read_settings


class FakeProc:
    def __init__(self, args):
        self.args = args
        self.pid = 4242
        self.returncode: int | None = None

    def poll(self):
        return self.returncode

    def terminate(self):
        self.returncode = -15


def _launcher(tmp_path, quit_ok=True):
    cfg = AppConfig.default()
    cfg.log_dir = str(tmp_path / "logs")
    cfg.save(tmp_path / "config.json")
    spawned: list[FakeProc] = []
    quits: list[int] = []

    def spawn(args, log_path):
        p = FakeProc(args)
        spawned.append(p)
        return p

    def quit_demo(port):
        quits.append(port)
        if quit_ok and spawned:
            spawned[-1].returncode = 0
        return quit_ok

    return Launcher(tmp_path / "config.json", spawn=spawn, quit_demo=quit_demo, wait_s=0.01), spawned, quits


def test_apply_settings_validates_and_returns_a_new_config():
    cfg = AppConfig.default()
    new, errors = apply_settings(cfg, {"robot.speed_pct": "80", "robot.z_safe": "30", "robot.travel_mode": "movl",
                                       "demo.panel_mirror": "on", "llm.enabled": "", "demo.stream_fps": "12"})
    assert not errors
    assert (new.robot.speed_pct, new.robot.z_safe, new.robot.travel_mode) == (80.0, 30.0, "movl")
    assert new.demo.panel_mirror is True and new.llm.enabled is False and new.demo.stream_fps == 12
    assert cfg.robot.speed_pct == 30.0 and cfg.demo.panel_mirror is False          # 元は変えない
    _, errors = apply_settings(cfg, {"robot.speed_pct": "0", "robot.travel_mode": "jump", "robot.z_safe": "abc"})
    assert len(errors) == 3 and any("speed_pct" in e for e in errors)
    assert read_settings(new)["robot.speed_pct"] == 80.0


def test_launcher_starts_stops_and_restarts_the_demo_once(tmp_path):
    lc, spawned, quits = _launcher(tmp_path)
    assert lc.status()["running"] is False
    lc.start()
    assert len(spawned) == 1 and "dobot.demo" in " ".join(spawned[0].args) and lc.status()["running"] is True
    lc.start()                                                          # 二重起動しない
    assert len(spawned) == 1
    lc.stop()
    assert quits == [8790] and lc.status()["running"] is False and spawned[0].returncode == 0
    lc.restart()
    assert len(spawned) == 2 and lc.status()["running"] is True


def test_launcher_terminates_when_quit_is_not_answered(tmp_path):
    lc, spawned, _ = _launcher(tmp_path, quit_ok=False)
    lc.start()
    lc.stop()
    assert spawned[0].returncode == -15 and lc.status()["running"] is False


def test_launcher_http_page_actions_and_settings(tmp_path):
    lc, spawned, _quits = _launcher(tmp_path)
    srv = LauncherServer(lc, port=0)
    port = srv.start()
    try:
        html = urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=3).read().decode("utf-8")
        assert "実演を起動" in html and "較正" in html and "speed_pct" in html
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/action", data=json.dumps({"type": "start"}).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        assert json.loads(urllib.request.urlopen(req, timeout=3).read())["ok"] and len(spawned) == 1
        st = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=3).read())
        assert st["running"] is True and st["settings"]["robot.speed_pct"] == 30.0
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/settings",
                                     data=json.dumps({"robot.speed_pct": "70", "robot.travel_mode": "bad"}).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        r = json.loads(urllib.request.urlopen(req, timeout=3).read())
        assert not r["ok"] and r["errors"] and AppConfig.load(tmp_path / "config.json").robot.speed_pct == 30.0   # 誤りがあれば書かない
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/settings", data=json.dumps({"robot.speed_pct": "70"}).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        r = json.loads(urllib.request.urlopen(req, timeout=3).read())
        assert r["ok"] and AppConfig.load(tmp_path / "config.json").robot.speed_pct == 70.0
        assert list(tmp_path.glob("config.bak_*.json"))                                                     # バックアップ
    finally:
        srv.stop()
