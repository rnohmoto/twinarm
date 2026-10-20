"""動きを速くする設定のテスト: 移動は関節補間（MOVJ）、ピック・プレースの降下だけ直線（MOVL）。"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from config import AppConfig, RobotConfig
from robot_dobot import DryRunRobot, PickPlaceController


def test_robot_config_travel_mode_defaults_to_joint_moves_and_round_trips(tmp_path):
    assert RobotConfig().travel_mode == "movj"
    cfg = AppConfig.default()
    cfg.robot.travel_mode = "movl"
    cfg.save(tmp_path / "c.json")
    assert AppConfig.load(tmp_path / "c.json").robot.travel_mode == "movl"
    # 古い config.json（項目なし）も読める
    (tmp_path / "old.json").write_text('{"robot": {"backend": "dry"}}', encoding="utf-8")
    assert AppConfig.load(tmp_path / "old.json").robot.travel_mode == "movj"


def test_move_to_linear_flag_falls_back_to_travel_mode():
    rb = DryRunRobot(RobotConfig(travel_mode="movj"))
    rb.move_to(220, 0, 40)                       # 既定 = travel_mode
    rb.move_to(220, 0, 0, linear=True)           # 降下は直線
    rb.move_to(220, 0, 40, linear=False)
    assert rb.modes == ["J", "L", "J"]
    rb2 = DryRunRobot(RobotConfig(travel_mode="movl"))
    rb2.move_to(220, 0, 40)
    assert rb2.modes == ["L"]


def test_pick_place_travels_in_joint_mode_and_descends_in_a_straight_line():
    # 2026-10-19: 1 回のピック&プレースに 20 s かかっていた（速度比 30 %・全区間 MOVL・z_safe 40）。
    # 退避・横移動・持ち上げは MOVJ、物へ下りる 2 区間だけ MOVL。
    rb = DryRunRobot(RobotConfig(travel_mode="movj", z_safe=20.0, z_pick=-57.1, z_place=-54.1))
    PickPlaceController(rb).pick_and_place(220, -50, 215, 95)
    assert rb.modes == ["J", "L", "J", "J", "L", "J"]
    assert rb.log.count("ee_on") == 1 and rb.log.count("ee_off") == 1
