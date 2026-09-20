"""カメラの疎通と「画質が勝手に変わっていないか」の確認（読み取りのみ・ロボットには触れない）。

使い方（このフォルダで）:
  uv run python check_camera.py --list                 # 見えているカメラを列挙
  uv run python check_camera.py --index 0              # 開いて 3 秒読む → 露出/WB の実効値と明るさの揺れを報告
  uv run python check_camera.py --index 0 --seconds 10 --width 1920 --height 1080

報告の読み方:
  - backend: OpenCV が選んだバックエンド（Windows=MSMF/DSHOW・Linux=V4L2・macOS=AVFOUNDATION）
  - auto_exposure(applied): 手動露出が効いた値。None なら手動化に失敗＝自動露出のまま
    （macOS の AVFOUNDATION は OpenCV から露出を触れない → Logi Tune 等で固定する）
  - brightness std / drift: フレーム間の明るさの標準偏差と最初→最後の差。固定できていれば 1〜2 以内で落ち着く。
    照明が一定なのに揺れる・上昇/下降するなら自動露出か自動 WB が生きている
  - スナップショットを logs/camera_check_<時刻>.jpg に保存する（マニュアルの写真にも使える）
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np

from camera import OpenCVCamera, list_opencv_cameras
from config import CameraConfig

HERE = Path(__file__).resolve().parent


def brightness_stats(frames: list[np.ndarray]) -> dict:
    """各フレームの平均明るさ（グレースケール 0〜255）から、揺れ（std）と傾き（drift）を返す。"""
    if not frames:
        return {"n": 0, "mean": 0.0, "std": 0.0, "drift": 0.0}
    means = [float(f.mean()) for f in frames]
    return {
        "n": len(means),
        "mean": float(np.mean(means)),
        "std": float(np.std(means)),
        "drift": float(means[-1] - means[0]),
    }


def judge(stats: dict, props: dict, applied) -> list[str]:
    """人が読む判定文。しきい値は経験値（照明一定・静止シーン前提）。"""
    notes = []
    if applied is None:
        notes.append("手動露出が効いていません（auto_exposure の書き込みが読み戻せない）。"
                     "macOS ならメーカーツールで固定、Windows なら別の値を試す")
    else:
        notes.append(f"手動露出の設定値 {applied} が効いています（読み戻し一致）")
    if stats["n"] >= 5:
        if stats["std"] > 3.0 or abs(stats["drift"]) > 5.0:
            notes.append(f"明るさが揺れています（std={stats['std']:.1f} drift={stats['drift']:+.1f}）"
                         "→ 自動露出/自動 WB が生きているか、照明がちらついています")
        else:
            notes.append(f"明るさは安定しています（std={stats['std']:.1f} drift={stats['drift']:+.1f}）")
    w, h = props.get("width"), props.get("height")
    notes.append(f"実効解像度 {int(w or 0)}x{int(h or 0)} @ {props.get('fps')} fps・backend={props.get('backend') or '?'}")
    return notes


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="camera check (read-only)")
    ap.add_argument("--list", action="store_true", help="カメラを列挙して終了")
    ap.add_argument("--index", default="0", help="OpenCV のカメラ番号かデバイスパス")
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--height", type=int, default=720)
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--seconds", type=float, default=3.0, help="明るさの揺れを見る秒数")
    ap.add_argument("--auto", action="store_true", help="自動露出のまま開く（比較用）")
    ap.add_argument("--exposure", type=float, default=-6.0, help="手動露出値（Windows: 2^n 秒。-6=1/64 秒）")
    ap.add_argument("--no-snapshot", action="store_true")
    args = ap.parse_args(argv)

    if args.list:
        cams = list_opencv_cameras()
        if not cams:
            print("カメラが見つかりません（USB・権限・他アプリの占有を確認）")
            return 1
        for c in cams:
            print(f"index {c['index']}: readable={c['readable']} {int(c['width'])}x{int(c['height'])}")
        return 0

    cfg = CameraConfig(index=args.index, width=args.width, height=args.height, fps=args.fps,
                       auto_exposure=args.auto, exposure=args.exposure)
    cam = OpenCVCamera(cfg)
    cam.open()
    try:
        frames = []
        t_end = time.time() + args.seconds
        while time.time() < t_end:
            frames.append(cam.read())
        props = cam.actual_props()
        stats = brightness_stats([f.mean(axis=2) for f in frames])
        print(f"frames={stats['n']} in {args.seconds}s  mean brightness={stats['mean']:.1f}")
        print("props:", {k: (round(v, 3) if isinstance(v, float) else v) for k, v in props.items()})
        for line in judge(stats, props, cam.auto_exposure_applied):
            print("-", line)
        if not args.no_snapshot and frames:
            import cv2
            out = HERE / "logs" / time.strftime("camera_check_%Y%m%d_%H%M%S.jpg")
            out.parent.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(out), frames[-1])
            print("snapshot:", out)
    finally:
        cam.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
