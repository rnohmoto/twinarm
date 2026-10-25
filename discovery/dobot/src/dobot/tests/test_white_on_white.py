"""白い机の白いゴルフボール（detector="sphere"・2026-10-26）: 色では見つからないので、円のあたり＋縁の影で見つける。

合成画像: 机 228。球は中心 236（ハイライト）→ 縁 166 の陰影＋ディンプルの粒＋片側に落ちる影（190）。
白いブロック（上面 232・平ら）と赤いブロックも同じ机に置き、球だけが golf_ball になることを見る。
"""
from __future__ import annotations

import numpy as np

from dobot.calibrate import Pair, PixelToRobot
from dobot.config import AppConfig
from dobot.detect import detect_objects, make_synthetic_frame, sphere_radius_px
from dobot.planner import TaskExecutor
from dobot.robot_dobot import DryRunRobot

TABLE = (228, 228, 228)


def draw_white_ball(img: np.ndarray, cx: int, cy: int, r: int, shadow: bool = True, seed: int = 0) -> None:
    import cv2
    if shadow:   # 右下に落ちる影（机より暗い・平ら）
        cv2.ellipse(img, (int(cx + 0.45 * r), int(cy + 0.55 * r)), (int(0.95 * r), int(0.6 * r)), 0, 0, 360, (190, 190, 190), -1)
    h, w = img.shape[:2]
    y0, y1 = max(0, cy - r - 1), min(h, cy + r + 2)
    x0, x1 = max(0, cx - r - 1), min(w, cx + r + 2)
    yy, xx = np.mgrid[y0:y1, x0:x1]
    d = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
    inside = d <= r
    shade = 236.0 - 70.0 * (d / r) ** 2                       # 中心が明るく縁が暗い（球の陰影）
    rng = np.random.default_rng(seed)
    shade = shade + rng.normal(0.0, 4.0, shade.shape)         # ディンプルの粒
    patch = img[y0:y1, x0:x1]
    val = np.clip(shade, 0, 255).astype(np.uint8)
    for c in range(3):
        ch = patch[:, :, c]
        ch[inside] = val[inside]
    cv2.circle(img, (int(cx - 0.3 * r), int(cy - 0.3 * r)), max(2, r // 6), (250, 250, 250), -1)   # ハイライト


def white_scene(ball=(640, 360, 74), white_block=(300, 500, 70), red_block=(1000, 250, 70), shadow=True):
    import cv2
    img = make_synthetic_frame(bg=TABLE)
    if red_block:
        x, y, s = red_block
        cv2.rectangle(img, (x - s // 2, y - s // 2), (x + s // 2, y + s // 2), (0, 0, 220), -1)
    if white_block:
        x, y, s = white_block
        cv2.rectangle(img, (x - s // 2, y - s // 2), (x + s // 2, y + s // 2), (232, 232, 232), -1)
    if ball:
        draw_white_ball(img, *ball, shadow=shadow)
    return img


def _p2r(mm_per_px: float = 0.3) -> PixelToRobot:
    k = mm_per_px
    pairs = [Pair(u, v, 220 - (v - 360) * k, -(u - 640) * k) for u, v in [(200, 100), (1000, 100), (1000, 600), (200, 600)]]
    return PixelToRobot.fit(pairs, (1280, 720))


def test_golf_ball_default_uses_the_sphere_detector():
    golf = next(o for o in AppConfig.default().objects if o.name == "golf_ball")
    assert golf.detector == "sphere" and golf.diameter_mm == 42.67
    rmin, rmax = sphere_radius_px(golf, 0.3, 400, 60000)
    assert rmin < 42.67 / 2 / 0.3 < rmax                      # 想定半径 71 px を挟む
    assert sphere_radius_px(golf, None, 400, 60000) == (11, 139)   # 較正が無ければ面積の範囲から


def test_white_ball_on_white_table_is_found_and_white_block_is_not():
    cfg = AppConfig.default()
    img = white_scene()
    dets = detect_objects(img, cfg.objects, cfg.min_area_px, cfg.max_area_px, mm_per_px=0.3)
    by = {d.name: d for d in dets}
    assert "golf_ball" in by and "red_cube" in by, [(d.name, d.cx, d.cy) for d in dets]
    g = by["golf_ball"]
    assert abs(g.cx - 640) <= 3 and abs(g.cy - 360) <= 3, (g.cx, g.cy)          # 影に引っ張られない
    assert abs(g.extra["radius_px"] - 74) <= 8 and g.extra["rim_contrast"] >= 10
    assert sum(d.name == "golf_ball" for d in dets) == 1                            # 白いブロック・影は球と言わない
    assert 300 - 40 < by["red_cube"].cx < 300 + 40 or abs(by["red_cube"].cx - 1000) < 5
    assert abs(by["red_cube"].cx - 1000) < 5 and abs(by["red_cube"].cy - 250) < 5


def test_white_ball_is_found_without_calibration_scale_and_without_shadow():
    cfg = AppConfig.default()
    dets = detect_objects(white_scene(shadow=False), cfg.objects, cfg.min_area_px, cfg.max_area_px)
    g = [d for d in dets if d.name == "golf_ball"]
    assert len(g) == 1 and abs(g[0].cx - 640) <= 3 and abs(g[0].cy - 360) <= 3


def test_empty_white_table_and_flat_white_things_give_no_ball():
    cfg = AppConfig.default()
    assert detect_objects(make_synthetic_frame(bg=TABLE), cfg.objects, cfg.min_area_px, cfg.max_area_px, mm_per_px=0.3) == []
    flat = white_scene(ball=None)                                                     # 白いブロックと赤いブロックだけ
    names = [d.name for d in detect_objects(flat, cfg.objects, cfg.min_area_px, cfg.max_area_px, mm_per_px=0.3)]
    assert names == ["red_cube"], names
    import cv2
    paper = make_synthetic_frame(bg=TABLE)                                            # 机より暗い平らな丸（影だけ・紙の丸）
    cv2.circle(paper, (640, 360), 74, (190, 190, 190), -1)
    assert [d.name for d in detect_objects(paper, cfg.objects, cfg.min_area_px, cfg.max_area_px, mm_per_px=0.3)] == []


def test_dark_mat_scenes_still_work_with_the_sphere_detector_enabled():
    cfg = AppConfig.default()
    img = make_synthetic_frame(blocks=[("red", 400, 300, 70), ("blue", 900, 250, 70)], balls=[("orange", 700, 300, 40)])
    names = sorted(d.name for d in detect_objects(img, cfg.objects, cfg.min_area_px, cfg.max_area_px, mm_per_px=0.3))
    assert names == ["ball", "blue_cube", "red_cube"], names


class Cam:
    def __init__(self, frame):
        self.frame = frame

    def read(self):
        return self.frame


def test_executor_picks_the_white_ball_with_its_own_heights():
    cfg = AppConfig.default()
    cfg.robot.z_pick, cfg.robot.z_place, cfg.robot.z_min = -57.1, -54.1, -80.0
    golf = next(o for o in cfg.objects if o.name == "golf_ball")
    golf.z_pick, golf.z_place = -30.5, -27.5
    rb = DryRunRobot(cfg.robot)
    rb.connect()
    ex = TaskExecutor(cfg, Cam(white_scene(white_block=None, red_block=None)), rb, _p2r(0.3))
    dets = ex.observe()
    assert [d.name for d in dets] == ["golf_ball"] and dets[0].size_mm is not None
    assert 38 <= dets[0].size_mm <= 48                                              # 直径 ≈ 2r × 0.3 mm/px
    r = ex.pick_and_place("golf_ball", "tray_right")
    assert r.ok, r.message
    zs = [float(m[len("move_to("):-1].split(",")[2]) for m in rb.log if m.startswith("move_to(")]
    assert -30.5 in zs and -27.5 in zs and -54.1 not in zs


def test_config_round_trip_keeps_sphere_fields(tmp_path):
    cfg = AppConfig.default()
    cfg.save(tmp_path / "c.json")
    back = AppConfig.load(tmp_path / "c.json")
    golf = next(o for o in back.objects if o.name == "golf_ball")
    assert golf.detector == "sphere" and golf.diameter_mm == 42.67 and golf.rim_contrast == 10.0
    assert next(o for o in back.objects if o.name == "red_cube").detector == "hsv"
