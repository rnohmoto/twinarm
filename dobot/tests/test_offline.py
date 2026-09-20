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
from detect import detect_objects, make_synthetic_frame
from llm_agent import RuleAgent, build_tools
from planner import TaskExecutor
from robot_dobot import DryRunRobot, OutOfWorkspace, PickPlaceController
from rule_parser import parse


def _synthetic_p2r(w=1280, h=720):
    # 画像中央(640,360)→(220,0) mm、1px=0.25mm、画像x→ロボット-y、画像y→ロボット-x
    pairs = [Pair(u, v, 220 - (v - 360) * 0.25, -(u - 640) * 0.25) for u, v in [(200, 100), (1000, 100), (1000, 600), (200, 600)]]
    return PixelToRobot.fit(pairs, (w, h)), pairs


def test_detect_objects_cubes():
    cfg = AppConfig.default()
    frame = make_synthetic_frame(1280, 720, [("red", 400, 300, 70), ("green", 700, 350, 70), ("blue", 900, 250, 70)])
    dets = detect_objects(frame, cfg.objects, cfg.min_area_px, cfg.max_area_px)
    got = {d.name: (d.cx, d.cy) for d in dets}
    assert set(got) == {"red_cube", "green_cube", "blue_cube"}, got
    for name, (cx, cy) in {"red_cube": (400, 300), "green_cube": (700, 350), "blue_cube": (900, 250)}.items():
        assert abs(got[name][0] - cx) < 2 and abs(got[name][1] - cy) < 2, (name, got[name])
    # 70x70=4900。ブラー＋クロージングで輪郭が数px太るので ±15% を許容
    assert all(4900 * 0.85 < d.area < 4900 * 1.15 for d in dets), [d.area for d in dets]


def test_detect_objects_shapes():
    """円形度と縦横比で ボール／消しゴム／青い立方体 を分ける。"""
    cfg = AppConfig.default()
    frame = make_synthetic_frame(1280, 720,
                                 blocks=[("blue", 300, 300, 70)],
                                 balls=[("orange", 700, 300, 40), ("white", 1000, 300, 40)],
                                 bars=[("blue", 700, 560, 140, 22)])
    dets = detect_objects(frame, cfg.objects, cfg.min_area_px, cfg.max_area_px)
    names = sorted(d.name for d in dets)
    assert names == ["ball", "blue_cube", "eraser", "golf_ball"], [(d.name, round(d.circularity, 2), round(d.aspect, 2)) for d in dets]
    ball = next(d for d in dets if d.name == "ball")
    assert ball.circularity > 0.82 and abs(ball.cx - 700) < 2 and abs(ball.cy - 300) < 2
    # 白い正方形（ArUco の白地に相当）はゴルフボールとして拾わない
    frame2 = make_synthetic_frame(1280, 720, blocks=[("white", 640, 360, 60)])
    assert [d.name for d in detect_objects(frame2, cfg.objects, cfg.min_area_px, cfg.max_area_px)] == []


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
        "赤いブロックを右のトレイに置いて": ("pick_and_place", "red_cube", "tray_right", 1, "any"),
        "みどり拾って左": ("pick_and_place", "green_cube", "tray_left", 1, "any"),
        "ボールを奥に移して": ("pick_and_place", "ball", "box_back", 1, "any"),
        "全部手前に入れて": ("pick_and_place", None, "box_front", -1, "any"),
        "一番大きい赤を右": ("pick_and_place", "red_cube", "tray_right", 1, "largest"),
        "黄色いのを取って": ("pick_and_place", "yellow_cube", None, 1, "any"),
        "消しゴムを左に置いて": ("pick_and_place", "eraser", "tray_left", 1, "any"),
        "ホームに戻って": ("home", None, None, 1, "any"),
        "片付けて": ("tidy", None, None, 1, "any"),
        "元に戻して": ("tidy", None, None, 1, "any"),
        "止まって": ("stop", None, None, 1, "any"),
        "何がある？": ("list", None, None, 1, "any"),
        "こんにちは": ("unknown", None, None, 1, "any"),
    }
    for text, (action, obj, zone, count, hint) in cases.items():
        it = parse(text, cfg)
        assert (it.action, it.object, it.zone, it.count, it.hint) == (action, obj, zone, count, hint), (text, it)


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
    # 物ごとの z_pick が pick の下降に使われる
    rb.log.clear()
    ctrl.pick_and_place(220, 40, 230, -110, z_pick=-12.0)
    assert rb.log[1] == "move_to(220.0,40.0,-12.0,0.0)", rb.log
    rb.stop()
    try:
        rb.move_to(220, 0, 40)
        raise AssertionError("expected EmergencyStop")
    except Exception as e:
        assert type(e).__name__ == "EmergencyStop"


def _executor_with(frame, cfg=None):
    cfg = cfg or AppConfig.default()
    cam = FileCamera(CameraConfig(backend="file"), frame)
    cam.open()
    rb = DryRunRobot(cfg.robot)
    rb.connect()
    p2r, _ = _synthetic_p2r()
    return cfg, rb, TaskExecutor(cfg, cam, rb, p2r)


def test_executor_end_to_end_dry():
    frame = make_synthetic_frame(1280, 720, [("red", 400, 300, 70), ("green", 700, 350, 70), ("red", 900, 250, 40)])
    cfg, rb, ex = _executor_with(frame)
    r = ex.list_objects()
    assert r.ok and len(r.data["objects"]) == 3, r
    assert "赤いブロックが2個" in r.message and "緑のブロックが1個" in r.message, r.message
    # 「一番大きい赤」= 70px の方（画像 (400,300) → ロボット (235, 60)）
    r = ex.pick_and_place("red_cube", "tray_right", "largest")
    assert r.ok, r
    first_move = rb.log[1]  # home の後の move_to
    assert first_move.startswith("move_to(235.0,60.0,"), rb.log
    assert "ee_on" in rb.log and "ee_off" in rb.log
    # 置き場は tray_right の最初の空きスロット (215,-125)
    assert rb.log[-1].startswith("move_to(215.0,-125.0,") and rb.log[-2] == "ee_off", rb.log
    r = ex.pick_and_place("yellow_cube", "tray_left")
    assert not r.ok and "見つかりません" in r.message, r
    r = ex.pick_and_place("green_cube", None)
    assert not r.ok and r.data.get("need") == "zone", r
    agent = RuleAgent(cfg, ex)
    reply = agent.handle("緑を左に置いて")
    assert "緑" in reply and "左" in reply, reply
    assert ex.stats["picks_ok"] == 2


def test_executor_skips_objects_already_in_zone():
    """置き場（半径内）にある物は「全部を右へ」でも動かさず、片付けは start へ戻す。"""
    cfg = AppConfig.default()
    # ロボット座標 (215,-125) は tray_right のスロット。逆変換で画像座標を作る
    p2r, _ = _synthetic_p2r()
    u, v = p2r.to_pixel(215, -125)
    frame = make_synthetic_frame(1280, 720, [("red", int(u), int(v), 70), ("blue", 400, 300, 70)])
    cfg, rb, ex = _executor_with(frame, cfg)
    r = ex.pick_and_place(None, "tray_right", "any", count=-1)
    assert r.ok and len(r.data["moved"]) == 1 and r.data["moved"][0]["name"] == "blue_cube", r
    rb.log.clear()
    r = ex.pick_and_place("red_cube", "tray_right")
    assert r.ok and "もう" in r.message and rb.log == [], r  # 動かない
    r = ex.tidy_up()
    assert r.ok and "片付け" in r.message and ex.stats["tidy"] == 1, r


def test_llm_tools_schema():
    cfg = AppConfig.default()
    tools = build_tools(cfg)
    names = {t["name"] for t in tools}
    assert names == {"list_objects", "pick_and_place", "tidy_up", "run_loop", "go_home", "stop"}
    rl = next(t for t in tools if t["name"] == "run_loop")
    assert rl["input_schema"]["properties"]["cycles"]["enum"] == [1, 2, 3, 5]
    pp = next(t for t in tools if t["name"] == "pick_and_place")
    assert pp["strict"] is True
    assert pp["input_schema"]["properties"]["object"]["enum"] == cfg.object_names()
    assert pp["input_schema"]["properties"]["zone"]["enum"] == cfg.zone_names()
    assert pp["input_schema"]["additionalProperties"] is False
    # 座標を受け取る引数が無いこと（LLM に座標を作らせない設計の機械担保）
    for t in tools:
        for k in t["input_schema"]["properties"]:
            assert k not in ("x", "y", "z", "x_mm", "y_mm"), t


def test_config_roundtrip_and_v1_compat(tmp_path):
    cfg = AppConfig.default()
    cfg.save(tmp_path / "c.json")
    back = AppConfig.load(tmp_path / "c.json")
    assert back.object_names() == cfg.object_names() and back.demo.panel_port == cfg.demo.panel_port
    assert back.zone_by_name("start").slots == cfg.zone_by_name("start").slots
    v1 = {"colors": [{"name": "red", "aliases": ["赤"], "ranges": [[[0, 120, 70], [8, 255, 255]]]}]}
    (tmp_path / "v1.json").write_text(__import__("json").dumps(v1), encoding="utf-8")
    old = AppConfig.load(tmp_path / "v1.json")
    assert old.object_names() == ["red"] and old.label("red") == "赤"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            import inspect
            fn(Path(__import__("tempfile").mkdtemp())) if "tmp_path" in inspect.signature(fn).parameters else fn()
            print(f"PASS {fn.__name__}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            import traceback
            print(f"FAIL {fn.__name__}: {e!r}")
            traceback.print_exc()
    print(f"\n{len(fns)-failed}/{len(fns)} passed")
    raise SystemExit(1 if failed else 0)
