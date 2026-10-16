"""zones_view（置き場をカメラ画像の上で決める）と setup_wizard.place_zone のオフラインテスト。"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from calibrate import Pair, PixelToRobot
from config import AppConfig
from setup_wizard import place_zone
from zones_view import click_to_zone, render, workspace_outline, zone_overlay


def _p2r() -> PixelToRobot:
    # main.py の dry-run と同じ合成ホモグラフィ: 画像中央 (640,360) がロボット (220,0)、0.25 mm/px、画像の右が -y
    pairs = [Pair(u, v, 220 - (v - 360) * 0.25, -(u - 640) * 0.25)
             for u, v in [(200, 100), (1000, 100), (1000, 600), (200, 600)]]
    return PixelToRobot.fit(pairs, (1280, 720))


def _zone(cfg: AppConfig, name: str):
    return next(z for z in cfg.zones if z.name == name)


def test_place_zone_moves_centre_and_rebuilds_slots_without_mutating():
    cfg = AppConfig.default()
    start, tray, box = _zone(cfg, "start"), _zone(cfg, "tray_right"), _zone(cfg, "box_front")
    s2 = place_zone(start, 240.04, 10.06)
    assert (s2.x, s2.y) == (240.0, 10.1) and len(s2.slots) == 6 and [217.5, -29.9] in s2.slots   # 2×3、45×40
    assert (start.x, start.y) == (235.0, 0.0) and len(start.slots) == 6                           # 元は変わらない
    t2 = place_zone(tray, 200, -100)
    assert len(t2.slots) == 4 and [185.0, -115.0] in t2.slots and t2.aliases == tray.aliases     # 2×2、30×30
    assert place_zone(box, 180, 5).slots == []                                                    # スロット無しは中心だけ


def test_zone_overlay_and_workspace_outline_follow_the_homography():
    p2r, cfg = _p2r(), AppConfig.default()
    (u, v), r_px, slots = zone_overlay(_zone(cfg, "start"), p2r)      # (235,0) → v = 360 - 15/0.25
    assert (u, v) == (640, 300) and r_px == 300 and len(slots) == 6    # 半径 75 mm / 0.25 mm/px
    pts = workspace_outline(cfg.robot, p2r)                            # x 150..300, y -150..150 の四隅
    assert pts == [(1240, 640), (1240, 40), (40, 40), (40, 640)]


def test_click_to_zone_sets_centre_inside_workspace_and_refuses_outside():
    p2r, cfg = _p2r(), AppConfig.default()
    zones = list(cfg.zones)
    i = [z.name for z in zones].index("tray_left")
    new, ok, msg = click_to_zone(zones, i, 240, 320, p2r, cfg.robot)   # u=240 → y=100、v=320 → x=230
    assert ok and (new[i].x, new[i].y) == (230.0, 100.0) and len(new[i].slots) == 4 and "tray_left" in msg
    assert zones[i].y == 110.0 and new[i - 1] is zones[i - 1]          # 元のリストと他の置き場はそのまま
    same, ok2, msg2 = click_to_zone(zones, i, 640, 700, p2r, cfg.robot)  # v=700 → x=135 < x_min=150
    assert not ok2 and same == zones and "外" in msg2


def test_render_draws_on_a_copy():
    p2r, cfg = _p2r(), AppConfig.default()
    frame = np.zeros((720, 1280, 3), np.uint8)
    vis = render(frame, cfg.zones, 0, p2r, cfg.robot, dirty=True)
    assert vis.shape == frame.shape and vis.any() and not frame.any()
