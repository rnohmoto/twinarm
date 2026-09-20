"""ハード無しで通るテスト。pytest でも `python tests/test_offline.py` でも動く。"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from calibrate import Pair, PixelToRobot
from camera import FileCamera
from config import AppConfig, CameraConfig, RobotConfig
from detect import detect_colors, make_synthetic_frame
from llm_agent import RuleAgent, build_tools
from planner import TaskExecutor
from robot_dobot import DryRunRobot, OutOfWorkspace, PickPlaceController
from rule_parser import parse


def _synthetic_p2r(w=1280, h=720):
    # 画像中央(640,360)→(220,0) mm、1px=0.25mm、画像x→ロボット-y、画像y→ロボット-x
    pairs = [Pair(u, v, 220 - (v - 360) * 0.25, -(u - 640) * 0.25) for u, v in [(200, 100), (1000, 100), (1000, 600), (200, 600)]]
    return PixelToRobot.fit(pairs, (w, h)), pairs


def test_detect_colors():
    cfg = AppConfig.default()
    frame = make_synthetic_frame(1280, 720, [("red", 400, 300, 70), ("green", 700, 350, 70), ("blue", 900, 250, 70)])
    dets = detect_colors(frame, cfg.colors, cfg.min_area_px, cfg.max_area_px)
    got = {d.color: (d.cx, d.cy) for d in dets}
    assert set(got) == {"red", "green", "blue"}, got
    for color, (cx, cy) in {"red": (400, 300), "green": (700, 350), "blue": (900, 250)}.items():
        assert abs(got[color][0] - cx) < 2 and abs(got[color][1] - cy) < 2, (color, got[color])
    # 70x70=4900。ブラー＋クロージングで輪郭が数px太るので ±15% を許容
    assert all(4900 * 0.85 < d.area < 4900 * 1.15 for d in dets), [d.area for d in dets]


def test_homography_roundtrip():
    p2r, pairs = _synthetic_p2r()
    assert p2r.rms_error_mm(pairs) < 1e-6
    x, y = p2r.to_robot(640, 360)
    assert abs(x - 220) < 1e-6 and abs(y) < 1e-6
    x, y = p2r.to_robot(640 + 100, 360)       # 右へ100px → y = -25mm
    assert abs(x - 220) < 1e-6 and abs(y + 25) < 1e-6
    u, v = p2r.to_pixel(220, -25)
    assert abs(u - 740) < 1e-6 and abs(v - 360) < 1e-6
    assert abs(p2r.mm_per_px(640, 360) - 0.25) < 1e-6


def test_homography_save_load(tmp_path=None):
    import tempfile
    p2r, pairs = _synthetic_p2r()
    d = Path(tmp_path) if tmp_path else Path(tempfile.mkdtemp())
    p2r.save(d / "h.json", pairs)
    q = PixelToRobot.load(d / "h.json")
    assert np.allclose(q.H, p2r.H)


def test_rule_parser():
    cfg = AppConfig.default()
    cases = {
        "赤いブロックを右のトレイに置いて": ("pick_and_place", "red", "tray_right", 1, "any"),
        "みどり拾って左": ("pick_and_place", "green", "tray_left", 1, "any"),
        "青を奥に移して": ("pick_and_place", "blue", "box_back", 1, "any"),
        "全部手前に入れて": ("pick_and_place", None, "box_front", -1, "any"),
        "一番大きい赤を右": ("pick_and_place", "red", "tray_right", 1, "largest"),
        "黄色いのを取って": ("pick_and_place", "yellow", None, 1, "any"),
        "ホームに戻って": ("home", None, None, 1, "any"),
        "止まって": ("stop", None, None, 1, "any"),
        "何がある？": ("list", None, None, 1, "any"),
        "こんにちは": ("unknown", None, None, 1, "any"),
    }
    for text, (action, color, zone, count, hint) in cases.items():
        it = parse(text, cfg)
        assert (it.action, it.color, it.zone, it.count, it.hint) == (action, color, zone, count, hint), (text, it)


def test_workspace_guard_and_sequence():
    rc = RobotConfig(backend="dry")
    rb = DryRunRobot(rc)
    rb.connect()
    ctrl = PickPlaceController(rb)
    ctrl.pick_and_place(220, 40, 230, -110)
    ops = [l.split("(")[0] for l in rb.log]
    assert ops == ["move_to", "move_to", "ee_on", "move_to", "move_to", "move_to", "ee_off", "move_to"], ops
    assert rb.ee_state is False
    try:
        ctrl.pick_and_place(50, 0, 230, -110)   # リーチ内側（根元）
        raise AssertionError("expected OutOfWorkspace")
    except OutOfWorkspace:
        pass
    n = len(rb.log)
    try:
        ctrl.pick_and_place(220, 40, 400, 0)    # 置き場がリーチ外 → 拾う前に拒否
        raise AssertionError("expected OutOfWorkspace")
    except OutOfWorkspace:
        pass
    assert len(rb.log) == n, "must not move when the place target is out of workspace"
    rb.stop()
    try:
        rb.move_to(220, 0, 40)
        raise AssertionError("expected EmergencyStop")
    except Exception as e:
        assert type(e).__name__ == "EmergencyStop"


def test_executor_end_to_end_dry():
    cfg = AppConfig.default()
    frame = make_synthetic_frame(1280, 720, [("red", 400, 300, 70), ("green", 700, 350, 70), ("red", 900, 250, 40)])
    cam = FileCamera(CameraConfig(backend="file"), frame)
    cam.open()
    rb = DryRunRobot(cfg.robot)
    rb.connect()
    p2r, _ = _synthetic_p2r()
    ex = TaskExecutor(cfg, cam, rb, p2r)
    r = ex.list_objects()
    assert r.ok and len(r.data["objects"]) == 3, r
    # 「一番大きい赤」= 70px の方（画像 (400,300) → ロボット (235, 60)）
    r = ex.pick_and_place("red", "tray_right", "largest")
    assert r.ok, r
    first_move = rb.log[1]  # home の後の move_to
    assert first_move.startswith("move_to(235.0,60.0,"), rb.log
    assert "ee_on" in rb.log and "ee_off" in rb.log
    assert rb.log[-1].startswith("move_to(230.0,-110.0,") and rb.log[-2] == "ee_off", rb.log  # 置き場 tray_right
    r = ex.pick_and_place("yellow", "tray_left")
    assert not r.ok and "見つかりません" in r.message, r
    r = ex.pick_and_place("green", None)
    assert not r.ok and r.data.get("need") == "zone", r
    agent = RuleAgent(cfg, ex)
    reply = agent.handle("緑を左に置いて")
    assert "緑" in reply and "左" in reply, reply


def test_llm_tools_schema():
    cfg = AppConfig.default()
    tools = build_tools(cfg)
    names = {t["name"] for t in tools}
    assert names == {"list_objects", "pick_and_place", "go_home", "stop"}
    pp = next(t for t in tools if t["name"] == "pick_and_place")
    assert pp["strict"] is True
    assert pp["input_schema"]["properties"]["color"]["enum"] == cfg.color_names()
    assert pp["input_schema"]["properties"]["zone"]["enum"] == cfg.zone_names()
    assert pp["input_schema"]["additionalProperties"] is False
    # 座標を受け取る引数が無いこと（LLM に座標を作らせない設計の機械担保）
    for t in tools:
        for k in t["input_schema"]["properties"]:
            assert k not in ("x", "y", "z", "x_mm", "y_mm"), t


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            import traceback
            print(f"FAIL {fn.__name__}: {e!r}")
            traceback.print_exc()
    print(f"\n{len(fns)-failed}/{len(fns)} passed")
    raise SystemExit(1 if failed else 0)
