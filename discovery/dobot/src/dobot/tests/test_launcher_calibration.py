"""ランチャーの較正まわり（2026-10-26）: 既定は --carry、確認は --verify、画面に較正の日時・RMS・点数を出す。"""
from __future__ import annotations

import json

from dobot.calibrate import Pair, PixelToRobot
from dobot.config import AppConfig
from dobot.launcher import Launcher, page_html


class FakeProc:
    def __init__(self, args):
        self.args = args
        self.pid = 4242
        self.returncode: int | None = None

    def poll(self):
        return self.returncode

    def terminate(self):
        self.returncode = -15


def _launcher(tmp_path):
    cfg = AppConfig.default()
    cfg.log_dir = str(tmp_path / "logs")
    cfg.homography_path = str(tmp_path / "assets" / "homography.json")
    cfg.save(tmp_path / "config.json")
    spawned: list[FakeProc] = []

    def spawn(args, log_path):
        p = FakeProc(args)
        spawned.append(p)
        return p

    return Launcher(tmp_path / "config.json", spawn=spawn, quit_demo=lambda port: True, wait_s=0.01), spawned


def test_calibration_commands_default_to_carry_and_verify(tmp_path):
    lc, _ = _launcher(tmp_path)
    assert lc.calibrate_command().endswith("--object blue_cube --carry")
    assert lc.verify_command().endswith("--object blue_cube --verify")
    assert lc.calibrate_command("drive").endswith("--drive")
    assert "dobot-calibrate" in lc.calibrate_command()


def test_status_reports_the_calibration_file_when_present(tmp_path):
    lc, _ = _launcher(tmp_path)
    assert lc.status()["calibration"] is None and lc.status()["calibrated"] is False
    pairs = [Pair(u, v, 220 - (v - 360) * 0.25, -(u - 640) * 0.25) for u, v in [(200, 100), (1000, 100), (1000, 600), (200, 600), (640, 360)]]
    p2r = PixelToRobot.fit(pairs, (1280, 720))
    p2r.save(tmp_path / "assets" / "homography.json", pairs)
    s = lc.status()
    assert s["calibrated"] is True and s["calibration"]["points"] == 5
    assert s["calibration"]["rms_mm"] is not None and s["calibration"]["rms_mm"] < 0.1
    assert s["calibration"]["created"] == json.loads((tmp_path / "assets" / "homography.json").read_text(encoding="utf-8"))["created"]


def test_page_has_the_verify_button_and_the_calibration_line(tmp_path):
    html = page_html(AppConfig.default())
    assert "act('verify')" in html and "act('calibrate')" in html and 'id="calinfo"' in html
    assert "ブロック 1 個" in html and "30 秒" in html
