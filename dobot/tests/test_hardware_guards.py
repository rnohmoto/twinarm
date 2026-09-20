"""ハード無しで通る安全ガードのテスト（twinarm の実機規則: ポートは推測しない・既定では動かない）。"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from check_camera import brightness_stats, judge
from check_robot import load_robot_config
from config import RobotConfig
from robot_dobot import PydobotRobot


def test_connect_refuses_to_guess_port():
    rb = PydobotRobot(RobotConfig(backend="pydobot", port=None))
    with pytest.raises(RuntimeError) as e:
        rb.connect()
    assert "ポート" in str(e.value)
    assert "推測しません" in str(e.value)


def test_find_port_returns_none_when_ambiguous(monkeypatch):
    monkeypatch.setattr(PydobotRobot, "list_candidate_ports", staticmethod(lambda: ["COM3", "COM4"]))
    assert PydobotRobot.find_port() is None
    monkeypatch.setattr(PydobotRobot, "list_candidate_ports", staticmethod(lambda: ["COM3"]))
    assert PydobotRobot.find_port() == "COM3"


def test_load_robot_config_defaults_and_overrides(tmp_path):
    cfg = load_robot_config(tmp_path / "missing.json", port="COM7", dry=False)
    assert cfg.backend == "pydobot" and cfg.port == "COM7"
    cfg2 = load_robot_config(tmp_path / "missing.json", port=None, dry=True)
    assert cfg2.backend == "dry"


def test_brightness_stats_constant_frames_have_no_jitter():
    frames = [np.full((8, 8), 100, dtype=np.uint8) for _ in range(6)]
    s = brightness_stats(frames)
    assert s["n"] == 6 and s["mean"] == 100.0 and s["std"] == 0.0 and s["drift"] == 0.0


def test_brightness_stats_detects_drift():
    frames = [np.full((8, 8), v, dtype=np.uint8) for v in (100, 104, 108, 112, 116, 120)]
    s = brightness_stats(frames)
    assert s["drift"] == 20.0 and s["std"] > 3.0


def test_judge_flags_unlocked_exposure_and_jitter():
    props = {"width": 1280.0, "height": 720.0, "fps": 30.0, "backend": "AVFOUNDATION"}
    notes = judge({"n": 10, "mean": 110.0, "std": 6.0, "drift": 12.0}, props, applied=None)
    assert any("効いていません" in n for n in notes)
    assert any("揺れています" in n for n in notes)
    ok = judge({"n": 10, "mean": 110.0, "std": 0.5, "drift": 0.2}, props, applied=0.25)
    assert any("安定" in n for n in ok)
