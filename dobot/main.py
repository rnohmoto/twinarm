"""magician_pnp エントリポイント。

  発話（PTT/キーボード） → 意図（Claude tool use ／ ルール解析） → 検出（HSV・キャリブ済み座標）
  → Dobot Magician が拾って置く → 返事（TTS）＋パネル（検出結果の絵）＋ JSONL ログ

使い方（段階的に実機へ近づける）
  1) 完全ドライラン（カメラ=合成画像・ロボット=記録のみ・LLM=ルール解析）
       python main.py --dry-run --no-llm --text "赤いブロックを右のトレイに置いて"
  2) カメラだけ実物（検出のチューニング。'q' で終了）
       python main.py --tune --camera-index 0
  3) キャリブ（別コマンド）: python calibrate.py --config config.json   ← ArUco 4枚を机に貼って実行
  4) ロボットだけ実物（LLM 無し）: python main.py --config config.json --robot pydobot --no-llm
  5) 本番: python main.py --config config.json --robot pydobot
     （ANTHROPIC_API_KEY を環境変数に。無ければ自動でルール解析にフォールバック）

キー（パネルウィンドウ）: space=話す（PTT） / h=ホーム / e=非常停止 / r=停止解除 / q=終了
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
for _s in (sys.stdout, sys.stderr):  # Windows コンソール/リダイレクトでの日本語化け対策
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

from config import AppConfig


def build(args) -> tuple:
    from calibrate import PixelToRobot
    from camera import make_camera
    from detect import make_synthetic_frame
    from llm_agent import RuleAgent, make_agent
    from planner import JsonlLogger, TaskExecutor
    from robot_dobot import make_robot

    cfg = AppConfig.load(args.config) if args.config and Path(args.config).exists() else AppConfig.default()
    if args.dry_run:
        cfg.camera.backend = "file"
        cfg.robot.backend = "dry"
    if args.robot:
        cfg.robot.backend = args.robot
    if args.port:
        cfg.robot.port = args.port
    if args.camera_index is not None:
        cfg.camera.backend = "opencv"
        cfg.camera.index = args.camera_index
    if args.no_llm:
        cfg.llm.enabled = False
    if args.text is not None or args.keyboard:
        cfg.asr.engine = "text"

    logger = JsonlLogger(HERE / cfg.log_dir)
    frame = None
    if cfg.camera.backend == "file" and not (isinstance(cfg.camera.index, str) and Path(cfg.camera.index).exists()):
        # 合成フレーム: 赤・緑・青のブロック（テストと同じ配置）
        frame = make_synthetic_frame(cfg.camera.width, cfg.camera.height,
                                     [("red", 400, 300, 70), ("green", 700, 350, 70), ("blue", 900, 250, 70)],
                                     balls=[("orange", 550, 520, 40)])
    camera = make_camera(cfg.camera, frame)
    camera.open()
    robot = make_robot(cfg.robot)
    robot.connect()

    p2r = None
    hp = (HERE / cfg.homography_path) if not Path(cfg.homography_path).is_absolute() else Path(cfg.homography_path)
    if hp.exists():
        p2r = PixelToRobot.load(hp)
    elif args.dry_run:
        # ドライラン用の仮ホモグラフィ: 画像中央(640,360)→(220,0)、1px=0.25mm、画像 x→ロボット -y、画像 y→ロボット -x
        from calibrate import Pair
        pairs = [Pair(u, v, 220 - (v - 360) * 0.25, -(u - 640) * 0.25) for u, v in [(200, 100), (1000, 100), (1000, 600), (200, 600)]]
        p2r = PixelToRobot.fit(pairs, (cfg.camera.width, cfg.camera.height))
        print("[dry-run] using synthetic homography (RMS %.3f mm)" % p2r.rms_error_mm(pairs))
    else:
        print(f"[warn] homography not found at {hp}; run calibrate.py first (pick_and_place will refuse)")

    executor = TaskExecutor(cfg, camera, robot, p2r, logger)
    agent = make_agent(cfg, executor, logger)
    print(f"[agent] {type(agent).__name__}  model={cfg.llm.model if not isinstance(agent, RuleAgent) else '-'}")
    return cfg, camera, robot, executor, agent, logger


def tune_loop(cfg: AppConfig, camera) -> None:
    """検出しきい値のチューニング表示（数値は config.json を編集して再起動）。"""
    import cv2

    from detect import annotate, detect_objects
    print("tune: 'q' で終了 / 's' でフレーム保存 (logs/frame_*.png)")
    while True:
        frame = camera.read()
        dets = detect_objects(frame, cfg.objects, cfg.min_area_px, cfg.max_area_px)
        vis = annotate(frame, dets, f"dets={len(dets)}  " + " ".join(f"{d.name}:{int(d.area)}" for d in dets[:6]))
        cv2.imshow("tune", vis)
        k = cv2.waitKey(30) & 0xFF
        if k == ord("q"):
            break
        if k == ord("s"):
            p = HERE / cfg.log_dir / time.strftime("frame_%H%M%S.png")
            p.parent.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(p), frame)
            print("saved", p)
    cv2.destroyAllWindows()


def run(args) -> int:
    cfg, camera, robot, executor, agent, logger = build(args)
    if args.tune:
        tune_loop(cfg, camera)
        return 0
    from asr import make_asr
    from tts import TTS
    asr = make_asr(cfg.asr)
    tts = TTS(cfg.tts)

    def one_turn(utterance: str) -> str:
        t0 = time.time()
        logger({"ev": "utterance", "text": utterance})
        reply = agent.handle(utterance)
        logger({"ev": "reply", "text": reply, "ms": int((time.time() - t0) * 1000)})
        print(f"[reply {time.time()-t0:.1f}s] {reply}")
        tts.speak(reply)
        return reply

    try:
        robot.home()
        if args.text is not None:                       # 1 発話だけ処理して終了（テスト用）
            one_turn(args.text)
            print("\n".join(robot.log))
            return 0
        if not cfg.panel_window or args.no_panel:      # コンソール運用
            while True:
                utt = asr.listen()
                if not utt:
                    continue
                if utt in ("q", "quit", "exit"):
                    break
                one_turn(utt)
            return 0
        import cv2

        from detect import annotate
        status = "space: hanasu / h: home / e: E-STOP / r: resume / q: quit"
        while True:
            dets = executor.observe()
            vis = annotate(executor.last_frame, dets, status)
            cv2.imshow("magician_pnp", vis)
            k = cv2.waitKey(30) & 0xFF
            if k == ord("q"):
                break
            if k == ord("h"):
                print(executor.go_home().message)
            elif k == ord("e"):
                print(executor.stop().message)
            elif k == ord("r"):
                print(executor.resume().message)
            elif k == ord(" "):
                utt = asr.listen()
                if utt:
                    status = one_turn(utt)[:60]
        cv2.destroyAllWindows()
        return 0
    finally:
        try:
            robot.home()
        except Exception:
            pass
        robot.disconnect()
        camera.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Dobot Magician voice/LLM pick-and-place")
    ap.add_argument("--config", default=str(HERE / "config.json"))
    ap.add_argument("--dry-run", action="store_true", help="カメラ=合成画像・ロボット=記録のみ")
    ap.add_argument("--no-llm", action="store_true", help="ルールベース解析のみ（LLM を呼ばない）")
    ap.add_argument("--text", default=None, help="この 1 発話だけ処理して終了")
    ap.add_argument("--keyboard", action="store_true", help="マイクの代わりにキーボード入力")
    ap.add_argument("--robot", choices=["dry", "pydobot"], default=None)
    ap.add_argument("--port", default=None)
    ap.add_argument("--camera-index", default=None)
    ap.add_argument("--tune", action="store_true", help="検出チューニング表示のみ")
    ap.add_argument("--no-panel", action="store_true")
    args = ap.parse_args(argv)
    if args.camera_index is not None and str(args.camera_index).isdigit():
        args.camera_index = int(args.camera_index)
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
