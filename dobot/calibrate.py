"""eye-to-hand キャリブレーション（画像ピクセル → ロボット XY [mm]）。

原理: 卓上の物体は 1 平面（テーブル面）に載っているので、俯瞰カメラの画像座標と
ロボットの XY 平面はホモグラフィ H（3x3）で対応づく。4 点以上の対応（ピクセル, mm）から
`cv2.findHomography` で H を求め、以後 (u,v) → (x,y) を 1 行で変換する。Z は平面なので定数
（物体の高さ分だけ RobotConfig.z_pick で吸盤高さを決める）。深度カメラ無しで成立する。

対応点の取り方（2 通り・実機で 10 分）
  A) ArUco: 4x4_50 の ID 0..3 を印刷して机の四隅付近に貼る。各マーカー中心へロボットを
     ジョグ（DobotStudio/手で動かして pose 読み）し、その XY を入力 → 自動でピクセル中心と対応。
  B) 手動クリック: パネル画像上でクリックした点に吸盤先端を合わせ、pose() を読む。

出力: assets/homography.json（H, 対応点, 画像サイズ, 再投影 RMS[mm], 日時）。
精度目安: 3cm 角ブロックの吸着なら RMS ≦ 2mm で十分（吸盤φ20mm）。
"""
from __future__ import annotations

import json
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np


@dataclass
class Pair:
    u: float
    v: float
    x: float
    y: float
    label: str = ""


class PixelToRobot:
    """H を持ち、ピクセル→mm / mm→ピクセル を変換する。"""

    def __init__(self, H: np.ndarray, image_size: tuple[int, int] | None = None):
        self.H = np.asarray(H, dtype=np.float64).reshape(3, 3)
        self.Hinv = np.linalg.inv(self.H)
        self.image_size = image_size

    @staticmethod
    def fit(pairs: Sequence[Pair], image_size: tuple[int, int] | None = None) -> PixelToRobot:
        if len(pairs) < 4:
            raise ValueError("need >= 4 pairs")
        src = np.array([[p.u, p.v] for p in pairs], dtype=np.float64)
        dst = np.array([[p.x, p.y] for p in pairs], dtype=np.float64)
        try:
            import cv2
            H, _ = cv2.findHomography(src, dst, 0)  # 4〜数点なので最小二乗（RANSAC不要）
            if H is None:
                raise RuntimeError("findHomography failed")
        except ImportError:  # OpenCV 無し環境用の DLT 実装（テスト用）
            H = _dlt_homography(src, dst)
        return PixelToRobot(H, image_size)

    def to_robot(self, u: float, v: float) -> tuple[float, float]:
        p = self.H @ np.array([u, v, 1.0])
        return float(p[0] / p[2]), float(p[1] / p[2])

    def to_pixel(self, x: float, y: float) -> tuple[float, float]:
        p = self.Hinv @ np.array([x, y, 1.0])
        return float(p[0] / p[2]), float(p[1] / p[2])

    def rms_error_mm(self, pairs: Iterable[Pair]) -> float:
        errs = []
        for p in pairs:
            x, y = self.to_robot(p.u, p.v)
            errs.append((x - p.x) ** 2 + (y - p.y) ** 2)
        return float(np.sqrt(np.mean(errs))) if errs else float("nan")

    def mm_per_px(self, u: float, v: float) -> float:
        x0, y0 = self.to_robot(u, v)
        x1, y1 = self.to_robot(u + 1, v)
        return float(np.hypot(x1 - x0, y1 - y0))

    # ------------------------------------------------------------------ I/O
    def save(self, path: str | Path, pairs: Sequence[Pair] = ()) -> None:
        d = {
            "H": self.H.tolist(),
            "image_size": list(self.image_size) if self.image_size else None,
            "pairs": [p.__dict__ for p in pairs],
            "rms_error_mm": self.rms_error_mm(pairs) if pairs else None,
            "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def load(path: str | Path) -> PixelToRobot:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        size = tuple(d["image_size"]) if d.get("image_size") else None
        return PixelToRobot(np.array(d["H"]), size)


def _dlt_homography(src: np.ndarray, dst: np.ndarray) -> np.ndarray:
    """正規化 DLT（OpenCV 不在時のフォールバック）。"""
    def normalize(pts):
        m = pts.mean(axis=0)
        s = np.sqrt(2) / np.mean(np.linalg.norm(pts - m, axis=1))
        T = np.array([[s, 0, -s * m[0]], [0, s, -s * m[1]], [0, 0, 1]])
        ph = np.c_[pts, np.ones(len(pts))] @ T.T
        return ph, T
    s_n, Ts = normalize(src)
    d_n, Td = normalize(dst)
    A = []
    for (u, v, _), (x, y, _) in zip(s_n, d_n):
        A.append([-u, -v, -1, 0, 0, 0, x * u, x * v, x])
        A.append([0, 0, 0, -u, -v, -1, y * u, y * v, y])
    _, _, Vt = np.linalg.svd(np.array(A))
    Hn = Vt[-1].reshape(3, 3)
    H = np.linalg.inv(Td) @ Hn @ Ts
    return H / H[2, 2]


# ---------------------------------------------------------------- ArUco 検出
def detect_aruco_centers(frame_bgr: np.ndarray, dictionary: str = "DICT_4X4_50") -> dict[int, tuple[float, float]]:
    """画像中の ArUco マーカー ID → 中心ピクセル。OpenCV 4.7+ の ArucoDetector API。"""
    import cv2
    aruco = cv2.aruco
    d = aruco.getPredefinedDictionary(getattr(aruco, dictionary))
    params = aruco.DetectorParameters()
    det = aruco.ArucoDetector(d, params)
    corners, ids, _ = det.detectMarkers(frame_bgr)
    out: dict[int, tuple[float, float]] = {}
    if ids is None:
        return out
    for c, i in zip(corners, ids.flatten()):
        pts = c.reshape(-1, 2)
        out[int(i)] = (float(pts[:, 0].mean()), float(pts[:, 1].mean()))
    return out


def make_aruco_sheet(path: str | Path, ids: Sequence[int] = (0, 1, 2, 3), px: int = 300) -> None:
    """印刷用マーカー画像（各 ID を 1 枚ずつ PNG）。"""
    import cv2
    aruco = cv2.aruco
    d = aruco.getPredefinedDictionary(aruco.DICT_4X4_50)
    Path(path).mkdir(parents=True, exist_ok=True)
    for i in ids:
        img = aruco.generateImageMarker(d, int(i), px)
        cv2.imwrite(str(Path(path) / f"aruco_4x4_50_id{i}.png"), img)


# ---------------------------------------------------------- 対話キャリブ CLI
def interactive_calibration(camera, robot, out_path: str | Path, use_aruco: bool = True) -> PixelToRobot:
    """実機用。camera.read() の画像に対し、ArUco 中心（または クリック点）へロボットを合わせ pose を読む。

    robot は robot_dobot.RobotBase（pose() が (x,y,z,r)）。robot=None なら XY を手入力。
    キー: [space]=現在の pose を対応点として記録 / [c]=クリック点モードでクリック / [q]=終了して保存
    """
    import cv2
    pairs: list[Pair] = []
    click_pt: list[tuple[float, float]] = []

    def on_mouse(event, x, y, flags, param):
        if event == cv2.EVENT_LBUTTONDOWN:
            click_pt.clear()
            click_pt.append((float(x), float(y)))

    win = "calibrate (space=record, q=quit)"
    cv2.namedWindow(win)
    cv2.setMouseCallback(win, on_mouse)
    current_target: tuple[float, float] | None = None
    marker_ids: list[int] = []
    while True:
        frame = camera.read()
        vis = frame.copy()
        if use_aruco:
            centers = detect_aruco_centers(frame)
            marker_ids = sorted(centers)
            for i, (u, v) in centers.items():
                cv2.circle(vis, (int(u), int(v)), 6, (0, 255, 255), 2)
                cv2.putText(vis, f"id{i}", (int(u) + 8, int(v) - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
            done_ids = {int(p.label[2:]) for p in pairs if p.label.startswith("id")}
            todo = [i for i in marker_ids if i not in done_ids]
            current_target = centers[todo[0]] if todo else None
            if current_target:
                cv2.putText(vis, f"move tool tip to id{todo[0]} then press SPACE", (10, 30),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 200, 0), 2)
        if click_pt:
            current_target = click_pt[0]
            cv2.circle(vis, (int(click_pt[0][0]), int(click_pt[0][1])), 6, (255, 0, 255), 2)
        for p in pairs:
            cv2.circle(vis, (int(p.u), int(p.v)), 5, (0, 0, 255), -1)
        cv2.putText(vis, f"pairs={len(pairs)}", (10, vis.shape[0] - 12), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
        cv2.imshow(win, vis)
        k = cv2.waitKey(30) & 0xFF
        if k == ord("q"):
            break
        if k == ord(" ") and current_target is not None:
            if robot is not None:
                x, y, *_ = robot.pose()
            else:
                x, y = map(float, input("robot X,Y [mm]: ").split(","))
            label = ""
            if use_aruco and not click_pt:
                done_ids = {int(p.label[2:]) for p in pairs if p.label.startswith("id")}
                todo = [i for i in marker_ids if i not in done_ids]
                label = f"id{todo[0]}" if todo else "pt"
            pairs.append(Pair(current_target[0], current_target[1], x, y, label or f"pt{len(pairs)}"))
            click_pt.clear()
            print("recorded", pairs[-1])
    cv2.destroyWindow(win)
    if len(pairs) < 4:
        raise RuntimeError(f"only {len(pairs)} pairs; need >= 4")
    h, w = frame.shape[:2]
    p2r = PixelToRobot.fit(pairs, (w, h))
    p2r.save(out_path, pairs)
    print(f"saved {out_path}  RMS={p2r.rms_error_mm(pairs):.2f} mm  scale={p2r.mm_per_px(w/2, h/2):.3f} mm/px")
    return p2r


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="eye-to-hand calibration")
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--out", default=None)
    ap.add_argument("--manual", action="store_true", help="ArUco を使わずクリック点で対応を取る")
    ap.add_argument("--no-robot", action="store_true", help="ロボット未接続: XY を手入力")
    ap.add_argument("--make-markers", default=None, help="印刷用 ArUco PNG を出力するフォルダ")
    args = ap.parse_args()
    if args.make_markers:
        make_aruco_sheet(args.make_markers)
        print("markers written to", args.make_markers)
        raise SystemExit(0)
    from camera import make_camera
    from config import AppConfig
    cfg = AppConfig.load(args.config) if Path(args.config).exists() else AppConfig.default()
    robot = None
    if not args.no_robot:
        from robot_dobot import make_robot
        robot = make_robot(cfg.robot)
        robot.connect()
    with make_camera(cfg.camera) as cam:
        interactive_calibration(cam, robot, args.out or cfg.homography_path, use_aruco=not args.manual)
