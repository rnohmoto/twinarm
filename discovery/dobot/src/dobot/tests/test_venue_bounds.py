"""会場の机と壁（2026-10-26）: 基部から巻尺で測った 3 つの距離で、腕が行く箱を狭める。較正の格子・退避点・ジョグも同じ箱を使う。"""
from __future__ import annotations

import pytest

from dobot.calibrate import carry_points, drive_grid, park_pose
from dobot.config import AppConfig, RobotConfig
from dobot.jog import violation
from dobot.launcher import apply_settings, page_html, read_settings
from dobot.robot_dobot import OutOfWorkspace, WorkspaceGuard


def test_bounds_default_to_the_workspace_box_and_shrink_with_the_venue():
    rc = RobotConfig()
    assert rc.bounds() == (150.0, 300.0, -150.0, 150.0)
    rc.right_limit_mm = 160.0                       # 右の壁が基部の中心から 16 cm
    rc.front_edge_mm = 320.0                        # 机の手前の縁まで 32 cm
    assert rc.bounds() == (150.0, 280.0, -120.0, 150.0)
    rc.left_limit_mm = 2000.0                       # 遠い壁は効かない
    assert rc.bounds()[3] == 150.0


def test_guard_jog_grid_and_park_follow_the_venue_bounds():
    rc = RobotConfig(right_limit_mm=160.0)
    guard = WorkspaceGuard(rc)
    guard.check(220.0, -110.0, 40.0)
    with pytest.raises(OutOfWorkspace):
        guard.check(220.0, -130.0, 40.0)            # 右の壁の手前 40 mm より外
    assert violation(rc, 220.0, -130.0, 40.0) > 0 and violation(rc, 220.0, -110.0, 40.0) == 0
    pts = drive_grid(rc)
    assert pts and min(y for _, y in pts) >= -120.0 + 25.0 - 1e-6    # 格子も壁の内側 25 mm
    assert len(carry_points(rc)) == 9
    assert park_pose(rc, 50.0) == (170.0, -90.0)                     # 退避点も壁の内側（y_min + 30）
    assert park_pose(rc, -50.0) == (170.0, 120.0)


def test_homing_warning_when_a_wall_is_within_the_sweep():
    rc = RobotConfig(right_limit_mm=160.0)
    assert "右" in rc.homing_clearance_warning() and "ホーミング" in rc.homing_clearance_warning()
    assert RobotConfig(right_limit_mm=400.0).homing_clearance_warning() == ""
    assert RobotConfig().homing_clearance_warning() == ""


def test_launcher_settings_accept_empty_as_no_limit():
    cfg = AppConfig.default()
    new, errors = apply_settings(cfg, {"robot.right_limit_mm": "160", "robot.left_limit_mm": "", "robot.front_edge_mm": "520"})
    assert not errors and new.robot.right_limit_mm == 160.0 and new.robot.left_limit_mm is None and new.robot.front_edge_mm == 520.0
    assert read_settings(new)["robot.right_limit_mm"] == 160.0
    _, errors = apply_settings(cfg, {"robot.right_limit_mm": "50"})
    assert errors and "right_limit_mm" in errors[0]
    html = page_html(new)
    assert 'name="robot.right_limit_mm"' in html and 'id="bounds"' in html


def test_config_round_trip_keeps_venue_fields(tmp_path):
    cfg = AppConfig.default()
    cfg.robot.right_limit_mm = 160.0
    cfg.save(tmp_path / "c.json")
    back = AppConfig.load(tmp_path / "c.json")
    assert back.robot.right_limit_mm == 160.0 and back.robot.left_limit_mm is None and back.robot.bounds()[2] == -120.0
