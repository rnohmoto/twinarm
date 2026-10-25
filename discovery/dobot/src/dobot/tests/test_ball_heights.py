"""ボール（ゴルフボール・卓球ボール）の検出と高さ: 凸包で測る円形度と、物ごとの z_place（2026-10-26）。"""
from __future__ import annotations

import numpy as np

from dobot.calibrate import Pair, PixelToRobot
from dobot.config import AppConfig
from dobot.detect import detect_objects, make_synthetic_frame
from dobot.planner import TaskExecutor
from dobot.robot_dobot import DryRunRobot


def _dimpled_ball(cx: int, cy: int, r: int) -> np.ndarray:
    """白いボール: 縁の下側に影の切れ込み、縁に沿った小さな欠け（ディンプルの陰）。輪郭は生だとぎざぎざ。"""
    import cv2
    img = make_synthetic_frame(balls=[("white", cx, cy, r)])
    cv2.ellipse(img, (cx, cy), (r, r), 0, 35, 145, (60, 60, 60), 7)          # 下側の縁の影（V が低い）
    rng = np.random.default_rng(0)
    for ang in rng.uniform(0, 2 * np.pi, 40):                                 # 縁の欠け
        x = int(cx + (r - 2) * np.cos(ang))
        y = int(cy + (r - 2) * np.sin(ang))
        cv2.circle(img, (x, y), 3, (90, 90, 90), -1)
    return img


def _raw_circularity(img: np.ndarray, spec) -> float:
    import cv2

    from dobot.detect import _mask_for
    hsv = cv2.cvtColor(cv2.GaussianBlur(img, (5, 5), 0), cv2.COLOR_BGR2HSV)
    mask = _mask_for(hsv, spec.ranges)
    k = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    mask = cv2.morphologyEx(cv2.morphologyEx(mask, cv2.MORPH_OPEN, k), cv2.MORPH_CLOSE, k)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    c = max(contours, key=cv2.contourArea)
    a, p = cv2.contourArea(c), cv2.arcLength(c, True)
    return 4 * np.pi * a / (p * p)


def test_dimpled_shaded_ball_is_still_a_golf_ball_by_hull_circularity():
    cfg = AppConfig.default()
    golf = next(o for o in cfg.objects if o.name == "golf_ball")
    img = _dimpled_ball(700, 300, 55)
    raw = _raw_circularity(img, golf)
    dets = detect_objects(img, cfg.objects, cfg.min_area_px, cfg.max_area_px)
    names = [d.name for d in dets]
    assert "golf_ball" in names, [(d.name, round(d.circularity, 2), round(d.aspect, 2)) for d in dets]
    g = next(d for d in dets if d.name == "golf_ball")
    assert g.circularity >= golf.min_circularity and g.circularity > raw, (g.circularity, raw)   # 凸包の円形度は生の輪郭より高い
    assert abs(g.cx - 700) < 4 and abs(g.cy - 300) < 6
    # 白い正方形（ArUco の白地）は今も拾わない
    sq = make_synthetic_frame(blocks=[("white", 640, 360, 60)])
    assert [d.name for d in detect_objects(sq, cfg.objects, cfg.min_area_px, cfg.max_area_px)] == []


def _p2r():
    pairs = [Pair(u, v, 220 - (v - 360) * 0.25, -(u - 640) * 0.25) for u, v in [(200, 100), (1000, 100), (1000, 600), (200, 600)]]
    return PixelToRobot.fit(pairs, (1280, 720))


class Cam:
    def __init__(self, frame):
        self.frame = frame

    def read(self):
        return self.frame


def test_place_height_comes_from_the_object_not_the_block_default():
    cfg = AppConfig.default()
    cfg.robot.z_pick, cfg.robot.z_place, cfg.robot.z_min = -57.1, -54.1, -80.0   # ベンチ: ブロック用の既定
    golf = next(o for o in cfg.objects if o.name == "golf_ball")
    golf.z_pick, golf.z_place = -30.5, -27.5                                    # ゴルフボール（上面 −29.5）
    red = next(o for o in cfg.objects if o.name == "red_cube")
    red.z_pick = -57.1                                                          # z_place は未設定 → robot.z_place
    rb = DryRunRobot(cfg.robot)
    rb.connect()
    ex = TaskExecutor(cfg, Cam(make_synthetic_frame(balls=[("white", 640, 360, 50)])), rb, _p2r())
    r = ex.pick_and_place("golf_ball", "tray_right")
    assert r.ok, r.message
    zs = [float(m[len("move_to("):-1].split(",")[2]) for m in rb.log if m.startswith("move_to(")]
    assert -27.5 in zs and -30.5 in zs and -54.1 not in zs                     # ボールをブロックの高さまで押し付けない
    rb2 = DryRunRobot(cfg.robot)
    rb2.connect()
    ex2 = TaskExecutor(cfg, Cam(make_synthetic_frame(blocks=[("red", 640, 360, 60)])), rb2, _p2r())
    assert ex2.pick_and_place("red_cube", "tray_right").ok
    zs2 = [float(m[len("move_to("):-1].split(",")[2]) for m in rb2.log if m.startswith("move_to(")]
    assert -54.1 in zs2 and -57.1 in zs2
    zone = cfg.zone_by_name("tray_right")
    zone.z_place = -40.0                                                        # 置き場の高さがあれば物より優先
    assert ex._z_place(zone, "golf_ball") == -40.0 and ex._z_place(cfg.zone_by_name("tray_left"), "golf_ball") == -27.5
    assert ex._z_place(cfg.zone_by_name("tray_left"), "red_cube") == -54.1


def test_config_round_trip_keeps_z_place(tmp_path):
    cfg = AppConfig.default()
    next(o for o in cfg.objects if o.name == "ball").z_place = -30.2
    cfg.save(tmp_path / "c.json")
    back = AppConfig.load(tmp_path / "c.json")
    assert next(o for o in back.objects if o.name == "ball").z_place == -30.2
    assert next(o for o in back.objects if o.name == "red_cube").z_place is None
