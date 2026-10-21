"""calibrate.py --drive（ロボット自身が動いて較正点を取る）のオフラインテスト。DryRunRobot と合成画像で。"""
from __future__ import annotations

import numpy as np

from dobot.calibrate import (
    Pair,
    PixelToRobot,
    drive_bias,
    drive_calibration,
    drive_grid,
    load_pairs,
    pairs_sidecar,
    park_pose,
)
from dobot.config import AppConfig
from dobot.detect import make_synthetic_frame
from dobot.robot_dobot import DryRunRobot


def _true_p2r() -> PixelToRobot:
    # 0.25 mm/px、画像中央 (640,360) = ロボット (220,0)、画像の右が -y
    pairs = [Pair(u, v, 220 - (v - 360) * 0.25, -(u - 640) * 0.25)
             for u, v in [(200, 100), (1000, 100), (1000, 600), (200, 600)]]
    return PixelToRobot.fit(pairs, (1280, 720))


class TrackingRobot(DryRunRobot):
    def __init__(self, cfg):
        super().__init__(cfg)
        self.moves: list[tuple[float, float, float]] = []

    def _move(self, x, y, z, r, wait, linear=True):
        super()._move(x, y, z, r, wait, linear)
        self.moves.append((x, y, z))


class CamUnderCup:
    """読むたびに「最後に z_hover で止まった (x,y) の真下に青ブロックを置いた」画像を返す（人が置いた体）。"""

    def __init__(self, robot: TrackingRobot, z_hover: float, truth: PixelToRobot):
        self.robot, self.z_hover, self.truth = robot, z_hover, truth

    def read(self) -> np.ndarray:
        x, y = next((x, y) for x, y, z in reversed(self.robot.moves) if abs(z - self.z_hover) < 1e-6)
        u, v = self.truth.to_pixel(x, y)
        return make_synthetic_frame(blocks=[("blue", round(u), round(v), 40)])


def test_drive_grid_stays_inside_the_workspace():
    cfg = AppConfig.default()
    pts = drive_grid(cfg.robot)
    assert len(pts) == 9 and (175.0, -125.0) in pts and (275.0, 125.0) in pts and (225.0, 0.0) in pts
    assert all(cfg.robot.x_min < x < cfg.robot.x_max and cfg.robot.y_min < y < cfg.robot.y_max for x, y in pts)
    assert all(cfg.robot.reach_min <= np.hypot(x, y) <= cfg.robot.reach_max for x, y in pts)
    assert park_pose(cfg.robot, 50.0) == (170.0, -120.0) and park_pose(cfg.robot, -50.0) == (170.0, 120.0)


def test_drive_calibration_fits_the_commanded_positions_and_parks_the_arm_before_reading(tmp_path):
    cfg = AppConfig.default()
    spec = next(o for o in cfg.objects if o.name == "blue_cube")
    truth = _true_p2r()
    robot = TrackingRobot(cfg.robot)
    robot.connect()
    z_hover = cfg.robot.z_pick + 8.0
    cam = CamUnderCup(robot, z_hover, truth)
    out = tmp_path / "homography.json"
    prompts: list[str] = []
    p2r = drive_calibration(cam, robot, cfg, spec, out, ask=lambda q: (prompts.append(q), "")[1], say=lambda *a: None)
    assert out.exists() and len(load_pairs(pairs_sidecar(out))) == 9 and len(prompts) == 9
    for x, y in drive_grid(cfg.robot):                                   # 合成の真値と 1 mm 以内
        u, v = truth.to_pixel(x, y)
        px, py = p2r.to_robot(u, v)
        assert abs(px - x) < 1.0 and abs(py - y) < 1.0
    assert tuple(robot.pose()[:3]) == tuple(cfg.robot.home_xyzr[:3])     # 終わったらホーム
    assert (170.0, -120.0, cfg.robot.z_safe) in robot.moves and (170.0, 120.0, cfg.robot.z_safe) in robot.moves  # 読む前に退避
    assert all(z >= cfg.robot.z_min for _, _, z in robot.moves)


def test_drive_calibration_skip_and_quit(tmp_path):
    cfg = AppConfig.default()
    spec = next(o for o in cfg.objects if o.name == "blue_cube")
    truth = _true_p2r()
    robot = TrackingRobot(cfg.robot)
    cam = CamUnderCup(robot, cfg.robot.z_pick + 8.0, truth)
    answers = iter(["", "s", "", "", "", "q"])                           # 4 点記録して q
    p2r = drive_calibration(cam, robot, cfg, spec, tmp_path / "h.json", ask=lambda q: next(answers), say=lambda *a: None)
    assert len(load_pairs(pairs_sidecar(tmp_path / "h.json"))) == 4 and p2r.rms_error_mm(load_pairs(pairs_sidecar(tmp_path / "h.json"))) < 0.5


def test_drive_bias_reports_how_far_out_the_old_calibration_aimed():
    truth = _true_p2r()
    pts = [(175.0, -100.0), (175.0, 100.0), (275.0, -100.0), (275.0, 100.0), (225.0, 0.0)]
    pairs = [Pair(*truth.to_pixel(x, y), x, y, f"pt{i}") for i, (x, y) in enumerate(pts)]
    old = PixelToRobot.fit([Pair(p.u, p.v, p.x * 1.04, p.y * 1.04) for p in pairs])   # 旧較正は基部から 4% 外側を狙う
    b = drive_bias(old, pairs)
    assert b["mean_radial"] > 8.0 and b["rms"] > 8.0 and b["max"] >= b["rms"] and abs(b["mean_dy"]) < 1.0
    assert drive_bias(None, pairs) is None
