"""置くときに、すでにある物の上に乗せない（画像で見えている物を避けて空きを探す）テスト。"""
from __future__ import annotations

import math

from dobot.calibrate import Pair, PixelToRobot
from dobot.camera import FileCamera
from dobot.config import AppConfig, CameraConfig, RobotConfig, Zone
from dobot.detect import Detection, make_synthetic_frame
from dobot.planner import PLACE_MARGIN_MM, TaskExecutor, choose_place
from dobot.robot_dobot import DryRunRobot, WorkspaceGuard


def _det(x: float, y: float, size: float = 44.0, name: str = "red_cube") -> Detection:
    return Detection(name, 0, 0, 1000, (0, 0, 10, 10), x_mm=x, y_mm=y, size_mm=size)


def _tray() -> Zone:
    return Zone("tray", ["トレイ"], 230.0, -110.0, radius_mm=60.0,
                slots=[[215.0, -125.0], [245.0, -125.0], [215.0, -95.0], [245.0, -95.0]])


def test_choose_place_takes_the_first_free_slot_when_the_zone_is_empty():
    assert choose_place(_tray(), [], 44.0) == (215.0, -125.0)


def test_choose_place_skips_slots_that_a_block_would_overlap_and_finds_a_clear_spot():
    # 2026-10-19: スロットは 30 mm 間隔だが木片は 40 mm 超。以前の判定（25 mm 以内に物が無ければ可）だと隣のスロットに
    # 重ねて置いた。大きさで判定し、スロットも中心もふさがっていれば半径内の空きへ。
    zone = _tray()
    block = _det(215.0, -125.0)                       # スロット 0 にブロック
    p = choose_place(zone, [block], 44.0)
    assert p is not None and p not in [tuple(s) for s in zone.slots]
    need = 22.0 + 22.0 + PLACE_MARGIN_MM
    assert math.hypot(p[0] - block.x_mm, p[1] - block.y_mm) >= need          # 重ならない
    assert math.hypot(p[0] - zone.x, p[1] - zone.y) <= zone.radius_mm         # 置き場の中


def test_choose_place_for_a_zone_without_slots_avoids_the_occupied_centre():
    box = Zone("box", ["箱"], 170.0, 0.0, radius_mm=60.0)
    p = choose_place(box, [_det(170.0, 0.0, 40.0)], 40.0)
    assert p is not None and math.hypot(p[0] - 170.0, p[1]) >= 40.0 + PLACE_MARGIN_MM and math.hypot(p[0] - 170.0, p[1]) <= 60.0


def test_choose_place_returns_none_when_the_zone_is_full_and_respects_the_workspace_guard():
    small = Zone("cup", ["皿"], 230.0, 0.0, radius_mm=20.0)
    assert choose_place(small, [_det(230.0, 0.0, 60.0)], 60.0) is None
    edge = Zone("edge", ["端"], 160.0, 0.0, radius_mm=60.0)                   # x_min=150 のすぐ内側
    guard = WorkspaceGuard(RobotConfig())
    p = choose_place(edge, [_det(160.0, 0.0, 40.0)], 40.0, guard=guard, z=-25.0)
    assert p is not None and p[0] >= 150.0 and guard.inside(p[0], p[1], -25.0)


def _executor(blocks):
    cfg = AppConfig.default()
    frame = make_synthetic_frame(blocks=blocks)
    cam = FileCamera(CameraConfig(backend="file"), frame)
    cam.open()
    robot = DryRunRobot(cfg.robot)
    robot.connect()
    pairs = [Pair(u, v, 220 - (v - 360) * 0.25, -(u - 640) * 0.25)
             for u, v in [(200, 100), (1000, 100), (1000, 600), (200, 600)]]
    p2r = PixelToRobot.fit(pairs, (1280, 720))
    return cfg, robot, TaskExecutor(cfg, cam, robot, p2r)


def _place_target(robot: DryRunRobot) -> tuple[float, float]:
    moves = [m for m in robot.log if m.startswith("move_to(")]
    i = robot.log.index("ee_off")
    last = [m for m in robot.log[:i] if m.startswith("move_to(")][-1]
    x, y, *_ = (float(v) for v in last[len("move_to("):-1].split(","))
    assert moves
    return x, y


def test_executor_places_beside_a_block_already_in_the_zone_not_on_top():
    # 合成ホモグラフィ: 画像 (640,560) = ロボット (170,0) = box_front の中心。そこに緑が置いてある
    _cfg, robot, ex = _executor([("red", 400, 300, 160), ("green", 640, 560, 160)])   # 160 px = 40 mm
    r = ex.pick_and_place("red_cube", "box_front")
    assert r.ok, r.message
    x, y = _place_target(robot)
    assert math.hypot(x - 170.0, y - 0.0) >= 40.0 + PLACE_MARGIN_MM          # 緑の上ではない
    assert math.hypot(x - 170.0, y - 0.0) <= 60.0                              # 置き場の中
    sz = next(d.size_mm for d in ex.last_dets if d.name == "green_cube")
    assert sz is not None and abs(sz - 40.0) < 3.0                            # 大きさは画像から mm で


def test_executor_refuses_when_the_zone_has_no_room():
    _cfg, robot, ex = _executor([("red", 400, 300, 160), ("green", 640, 560, 160)])
    ex.cfg.zone_by_name("box_front").radius_mm = 20.0                          # 緑 1 個でいっぱいの小さな置き場
    r = ex.pick_and_place("red_cube", "box_front")
    assert not r.ok and "空き" in r.message and "ee_on" not in robot.log       # 拾いに行かない
