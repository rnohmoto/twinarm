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
import math
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from dobot.paths import CONFIG_PATH, from_root


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
def pick_target(centers: Sequence[tuple[float, float]],
                click: tuple[float, float] | None) -> tuple[float, float] | None:
    """物を目印にするときの目標点: クリックがあれば最も近い検出、なければ最初（面積の大きい順）の検出。"""
    if not centers:
        return None
    if click is None:
        return centers[0]
    return min(centers, key=lambda c: (c[0] - click[0]) ** 2 + (c[1] - click[1]) ** 2)


RECORD_MIN_GAP_S = 1.0  # ロックから記録まで最低この秒数は空ける（長押しで両方が連続発火するのを防ぐ）


def space_transition(locked: tuple[float, float] | None,
                     target: tuple[float, float] | None,
                     since_lock_s: float | None = None,
                     min_gap_s: float = RECORD_MIN_GAP_S) -> tuple[str, tuple[float, float] | None]:
    """物モードの space の状態遷移。1 回目=ピクセルをロック、2 回目=pose と対にして記録。

    腕を物の真上に合わせた時点では物が吸盤の影に隠れて検出が消えるため、検出が生きている
    うちにピクセルを確定し、ロボット姿勢はその後で読む。ロック済みなら target は見ない。
    ロックから since_lock_s 秒しか経っていない記録は "wait" で弾く: 2026-10-16 のベンチで Space の
    長押しがロック→記録と連続発火し、腕を動かす前の姿勢が 2 点記録された（RMS 54 mm）。
    """
    if locked is not None:
        if since_lock_s is not None and since_lock_s < min_gap_s:
            return ("wait", locked)
        return ("record", locked)
    if target is not None:
        return ("lock", target)
    return ("noop", None)


# ---------------------------------------------------- 対応点の逐次保存（途中クラッシュ対策）
def pairs_sidecar(out_path: str | Path) -> Path:
    """記録途中の対応点を置くファイル。assets/homography.json → assets/homography.pairs.json。

    2026-10-15 のベンチで 4 点記録後に calibrate が落ち、q までメモリ上にしかなかった点が全部消えた。
    1 点ごとにここへ書き、次回起動時に読み戻して続きから記録する。
    """
    out = Path(out_path)
    return out.with_name(out.stem + ".pairs.json")


def load_pairs(path: str | Path) -> list[Pair]:
    p = Path(path)
    if not p.exists():
        return []
    return [Pair(**d) for d in json.loads(p.read_text(encoding="utf-8"))]


def save_pairs(path: str | Path, pairs: Sequence[Pair]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps([q.__dict__ for q in pairs], ensure_ascii=False, indent=2), encoding="utf-8")


def record_pair(pairs: Sequence[Pair], pt: tuple[float, float], xy: tuple[float, float],
                sidecar: str | Path, label: str = "") -> list[Pair]:
    """1 点追加した新しいリストを返し、同時にサイドカーへ書く（元のリストは変更しない）。"""
    new = [*pairs, Pair(pt[0], pt[1], xy[0], xy[1], label or f"pt{len(pairs)}")]
    save_pairs(sidecar, new)
    return new


def drop_last_pair(pairs: Sequence[Pair], sidecar: str | Path) -> list[Pair]:
    """直前の 1 点を捨てた新しいリストを返し、サイドカーも更新する。空なら何もしない。"""
    if not pairs:
        return list(pairs)
    new = list(pairs[:-1])
    save_pairs(sidecar, new)
    return new


# ------------------------------------------- ロボット自身が動いて取る較正（calibrate.py --drive）
DRIVE_HOVER_GAP_MM = 8.0     # 物の上面と吸盤の隙間: 物を真下へ滑り込ませられて、目で中心を合わせやすい高さ
DRIVE_PARK_INSET_MM = 20.0   # 退避点: 基部側の端から内側へ
DRIVE_PARK_SIDE_MM = 30.0    # 退避点: 左右の端から内側へ
SETTLE_FRAMES = 5            # 腕が退避してからカメラを読むまでに捨てるフレーム数


def drive_grid(robot, nx: int = 3, ny: int = 3, margin_mm: float = 25.0) -> list[tuple[float, float]]:
    """ロボット自身が回る較正点: 作業域の内側に margin を取った nx×ny の格子のうち、届くもの。"""
    from dobot.robot_dobot import WorkspaceGuard
    guard = WorkspaceGuard(robot)
    x_min, x_max, y_min, y_max = robot.bounds()   # 机の縁・壁（right_limit_mm など）があれば、その内側だけ回る
    xs = np.linspace(x_min + margin_mm, x_max - margin_mm, nx)
    ys = np.linspace(y_min + margin_mm, y_max - margin_mm, ny)
    pts = [(round(float(x), 1), round(float(y), 1)) for x in xs for y in ys]
    return [p for p in pts if guard.inside(p[0], p[1], robot.z_safe)]


def park_pose(robot, y: float) -> tuple[float, float]:
    """カメラを読む前の退避点: 基部側の端で、目標と反対側。俯瞰カメラで腕が物を隠さないように。"""
    x_min, _x_max, y_min, y_max = robot.bounds()
    side = y_min + DRIVE_PARK_SIDE_MM if y >= 0 else y_max - DRIVE_PARK_SIDE_MM
    return (x_min + DRIVE_PARK_INSET_MM, side)


def drive_bias(old: PixelToRobot | None, pairs: Sequence[Pair]) -> dict | None:
    """前の較正が命令座標からどれだけずれて狙っていたか。old(pixel) − 命令 の平均・基部から見た半径方向・RMS・最大。"""
    if old is None or not pairs:
        return None
    rows = []
    for p in pairs:
        x, y = old.to_robot(p.u, p.v)
        ex, ey = x - p.x, y - p.y
        r = float(np.hypot(p.x, p.y))
        rows.append((ex, ey, (ex * p.x + ey * p.y) / r if r else 0.0, float(np.hypot(ex, ey))))
    a = np.array(rows)
    return {"mean_dx": float(a[:, 0].mean()), "mean_dy": float(a[:, 1].mean()), "mean_radial": float(a[:, 2].mean()),
            "rms": float(np.sqrt(np.mean(a[:, 3] ** 2))), "max": float(a[:, 3].max())}


def read_settled(camera, frames: int = SETTLE_FRAMES) -> np.ndarray:
    frame = None
    for _ in range(frames):
        frame = camera.read()
    return frame


def drive_calibration(camera, robot, cfg, object_spec, out_path: str | Path, points=None,
                      hover_gap_mm: float = DRIVE_HOVER_GAP_MM, ask=input, say=print) -> PixelToRobot:
    """ロボット自身が各目標へ動き、その真下に置いてもらった物をカメラで読んで対応点にする。**腕が動く。**

    手で腕を押し当てて pose を読む方式は、押したときのたわみやバックラッシュで数 mm 偏る
    （2026-10-16: ピック位置が外側に偏り、ブロックを基部側へ寄せないと吸えなかった）。
    ここでは「命令した座標 → 吸盤が実際に行く場所」をそのまま対応点にするので、ピック時と同じ経路で合う。
    各点: (x,y,z_safe) → (x,y,z_hover) で止まる → 物を吸盤の真下に置いて Enter → z_safe → 退避点 → 撮って検出。
    Enter=記録 / s=この点を飛ばす / q=中断（ホームへ戻り、4 点以上あれば保存）。Ctrl+C はその場で止める（ホームへ戻さない）。
    """
    from dobot.detect import detect_objects
    from dobot.robot_dobot import EmergencyStop
    rc = cfg.robot
    points = list(points or drive_grid(rc))
    z_pick = object_spec.z_pick if object_spec.z_pick is not None else rc.z_pick
    z_hover = z_pick + hover_gap_mm
    out_path = Path(out_path)
    old = PixelToRobot.load(out_path) if out_path.exists() else None
    sidecar = pairs_sidecar(out_path)
    pairs: list[Pair] = []
    frame = None
    say(f"{len(points)} 点を回ります。各点で吸盤が物の上面 +{hover_gap_mm:g} mm (z={z_hover:.1f}) で止まるので、"
        f"{object_spec.name} を真下（中心）に置いて Enter。腕が動きます: 手を退けてください")
    try:
        robot.home()
        for i, (x, y) in enumerate(points):
            robot.move_to(x, y, rc.z_safe)
            robot.move_to(x, y, z_hover)
            answer = ""
            while True:
                answer = ask(f"[{i + 1}/{len(points)}] ({x:.0f},{y:.0f}) {object_spec.name} を吸盤の真下（中心）に置いて "
                             "Enter / s=飛ばす / q=中断: ").strip().lower()
                if answer in ("s", "q"):
                    break
                robot.move_to(x, y, rc.z_safe)
                px, py = park_pose(rc, y)
                robot.move_to(px, py, rc.z_safe)
                frame = read_settled(camera)
                dets = detect_objects(frame, [object_spec], cfg.min_area_px, cfg.max_area_px)
                target = pick_target([(d.cx, d.cy) for d in dets], old.to_pixel(x, y) if old else None)
                if target is None:
                    say(f"  {object_spec.name} が見えません。吸盤を戻すので置き直してください")
                    robot.move_to(x, y, rc.z_safe)
                    robot.move_to(x, y, z_hover)
                    continue
                pairs = record_pair(pairs, target, (x, y), sidecar)
                msg = f"  recorded pixel=({target[0]:.0f},{target[1]:.0f}) -> commanded ({x:.0f},{y:.0f})"
                if old is not None:
                    ox, oy = old.to_robot(*target)
                    msg += f"  (前の較正なら ({ox:.0f},{oy:.0f}) と答えていた)"
                say(msg)
                break
            if answer == "q":
                break
        robot.home()
    except (KeyboardInterrupt, EmergencyStop):
        say(f"中断: 腕はその場で止めます（ホームへは戻しません）。記録済み {len(pairs)} 点は {sidecar} にあります")
        raise
    if len(pairs) < 4:
        raise RuntimeError(f"only {len(pairs)} pairs; need >= 4 (they are kept in {sidecar})")
    h, w = frame.shape[:2]
    p2r = PixelToRobot.fit(pairs, (w, h))
    if old is not None:
        bak = from_root(cfg.log_dir) / f"homography_before_drive_{time.strftime('%Y%m%d_%H%M%S')}.json"
        bak.parent.mkdir(parents=True, exist_ok=True)
        bak.write_text(out_path.read_text(encoding="utf-8"), encoding="utf-8")
        say(f"前の較正を {bak} に退避しました")
    p2r.save(out_path, pairs)
    save_pairs(sidecar, pairs)
    say(f"saved {out_path}  RMS={p2r.rms_error_mm(pairs):.2f} mm  scale={p2r.mm_per_px(w / 2, h / 2):.3f} mm/px")
    b = drive_bias(old, pairs)
    if b is not None:
        side = "外側" if b["mean_radial"] > 0 else "内側"
        say(f"前の較正の狙いのずれ: 平均 dx={b['mean_dx']:+.1f} dy={b['mean_dy']:+.1f} mm、基部から見て{side}に "
            f"{abs(b['mean_radial']):.1f} mm、RMS {b['rms']:.1f} mm、最大 {b['max']:.1f} mm")
    return p2r


# --------------------------- ブロック 1 個をロボットが持ち運ぶ較正（--carry）と、今の較正の確認（--verify）
CARRY_PLACE_GAP_MM = 3.0      # 置くときの吸盤高さ = z_pick + 3（物の底が面の 2 mm 上。wizard 5 番・HANDOFF 4.3 の z_place と同じ規則）
CARRY_MOVED_MIN_PX = 12.0     # 置いたあとの検出が前の点の画素からこれ未満しか動いていなければ「拾えていなかった」
CARRY_RESIDUAL_MAX_MM = 8.0   # 4 点そろった後、新しい点の残差がこれを超えたら「途中で落ちた・別の物を見た」として人に置き直してもらう
VERIFY_OK_MM = 3.0            # 確認モードの合格: 置いた位置の読みと命令座標の差
CARRY_HUMAN_TRIES = 3         # 同じ点で人に置き直してもらう回数の上限。超えたらその点を飛ばす（無限に聞かない）
GRID_PRESETS = ("quick", "full")


def grid_center(points: Sequence[tuple[float, float]]) -> tuple[float, float]:
    """点の重心に最も近い格子点（3×3 なら真ん中）。--verify の置き先。"""
    mx = sum(p[0] for p in points) / len(points)
    my = sum(p[1] for p in points) / len(points)
    return min(points, key=lambda p: (p[0] - mx) ** 2 + (p[1] - my) ** 2)


def carry_points(robot, preset: str = "full") -> list[tuple[float, float]]:
    """--carry で回る点とその順番: 四隅（回る向きに）→ 中心 → 辺の中点。quick=四隅＋中心の 5 点、full=3×3 の 9 点。

    ホモグラフィは 4 点で決まるので 5 点あれば足り、9 点にすると余った点で RMS（当てはまりの悪さ）が分かる。
    四隅を先にするのは、最初の 4 点が一直線を含まないようにするため（中心と向かい合う隅 2 つは対角線で一直線。
    同じ辺の 3 点＋中心でも当てはめが壊れ、5 点目の残差が 46 mm になった＝2026-10-26 の合成テスト）。
    点は drive_grid（作業域の内側 25 mm・届く所だけ）と同じ。
    """
    pts = drive_grid(robot)
    if not pts:
        return []
    center = grid_center(pts)
    ring = [p for p in pts if p != center]
    xs = {p[0] for p in ring}
    ys = {p[1] for p in ring}
    corners = [p for p in ring if p[0] in (min(xs), max(xs)) and p[1] in (min(ys), max(ys))]
    mids = [p for p in ring if p not in corners]

    def angle(p):
        return math.atan2(p[1] - center[1], p[0] - center[0])

    order = [*sorted(corners, key=angle), center]
    if preset != "quick":
        order += sorted(mids, key=angle)
    return order


def well_conditioned(points: Sequence[tuple[float, float]], min_triangle_mm2: float = 300.0) -> bool:
    """ホモグラフィを当てはめてよい点の並びか: 「どの 3 点も一直線に近くない 4 点」が少なくとも 1 組ある。

    格子の 9 点には一直線の 3 点（行・列・対角）が当然あるが、四隅のように一般位置の 4 点があれば当てはめは決まる。
    壊れるのは、同じ辺の 3 点＋中心のように、どの 4 点を選んでも一直線の 3 点を含むとき。
    """
    from itertools import combinations
    pts = list(points)
    if len(pts) < 4:
        return False

    def area(a, b, c) -> float:
        return abs((b[0] - a[0]) * (c[1] - a[1]) - (c[0] - a[0]) * (b[1] - a[1])) / 2.0

    for quad in combinations(pts, 4):
        if all(area(*tri) >= min_triangle_mm2 for tri in combinations(quad, 3)):
            return True
    return False


def object_heights(rc, object_spec, hover_gap_mm: float = DRIVE_HOVER_GAP_MM) -> tuple[float, float, float]:
    """物の定義から (z_pick, z_hover, z_place)。z_place は物の定義に無ければ z_pick + 3（同じ面に置く前提）。"""
    z_pick = object_spec.z_pick if object_spec.z_pick is not None else rc.z_pick
    z_place = getattr(object_spec, "z_place", None)
    if z_place is None:
        z_place = z_pick + CARRY_PLACE_GAP_MM
    return z_pick, z_pick + hover_gap_mm, z_place


def fit_dropping_outliers(pairs: Sequence[Pair], image_size: tuple[int, int] | None = None,
                          max_mm: float = CARRY_RESIDUAL_MAX_MM, min_keep: int = 5
                          ) -> tuple[PixelToRobot, list[Pair], list[Pair]]:
    """全点で当てはめ、残差が max_mm を超える最悪の点を 1 つずつ外して当てはめ直す（min_keep 点までは外す）。"""
    kept = list(pairs)
    dropped: list[Pair] = []
    while True:
        p2r = PixelToRobot.fit(kept, image_size)
        worst, worst_pair = -1.0, None
        for p in kept:
            x, y = p2r.to_robot(p.u, p.v)
            e = math.hypot(x - p.x, y - p.y)
            if e > worst:
                worst, worst_pair = e, p
        if worst <= max_mm or len(kept) <= min_keep or worst_pair is None:
            return p2r, kept, dropped
        rest = [p for p in kept if p is not worst_pair]
        if not well_conditioned([(p.x, p.y) for p in rest]):     # 外すと一直線に近づくなら、外さずに終える
            return p2r, kept, dropped
        kept = rest
        dropped.append(worst_pair)


def residual_mm(pairs: Sequence[Pair], pixel: tuple[float, float], xy: tuple[float, float],
                image_size: tuple[int, int] | None = None) -> float:
    """すでに記録した点で当てはめたホモグラフィで pixel を読んだときの、命令座標 xy とのずれ。
    4 点未満、または点が一直線に近くて当てはめが信用できないときは 0（検査しない）。"""
    if len(pairs) < 4 or not well_conditioned([(p.x, p.y) for p in pairs]):
        return 0.0
    p2r = PixelToRobot.fit(pairs, image_size)
    x, y = p2r.to_robot(*pixel)
    return math.hypot(x - xy[0], y - xy[1])


class Carrier:
    """腕の基本動作（拾う・置く・退避して見る・物の上で止まる）。carry_calibration と verify_calibration が共有する。

    物へ下りる区間だけ直線（linear=True）、ほかは cfg.travel_mode（planner の PickPlaceController と同じ）。
    """

    def __init__(self, camera, robot, cfg, object_spec, z_pick: float, z_place: float):
        self.camera, self.robot, self.cfg, self.spec = camera, robot, cfg, object_spec
        self.rc = cfg.robot
        self.z_pick, self.z_place = z_pick, z_place

    def pick(self, x: float, y: float) -> None:
        self.robot.move_to(x, y, self.rc.z_safe)
        self.robot.move_to(x, y, self.z_pick, linear=True)
        self.robot.ee_on()
        self.robot.move_to(x, y, self.rc.z_safe)

    def place(self, x: float, y: float) -> None:
        self.robot.move_to(x, y, self.rc.z_safe)
        self.robot.move_to(x, y, self.z_place, linear=True)
        self.robot.ee_off()
        self.robot.move_to(x, y, self.rc.z_safe)

    def hover(self, x: float, y: float, z_hover: float) -> None:
        self.robot.move_to(x, y, self.rc.z_safe)
        self.robot.move_to(x, y, z_hover, linear=True)

    def look(self, y_side: float, expect: tuple[float, float] | None = None
             ) -> tuple[np.ndarray, tuple[float, float] | None]:
        """目標と反対側へ退避してから撮り、物の中心画素を返す（複数見えれば expect に近いもの）。"""
        from dobot.detect import detect_objects
        px, py = park_pose(self.rc, y_side)
        self.robot.move_to(px, py, self.rc.z_safe)
        frame = read_settled(self.camera)
        dets = detect_objects(frame, [self.spec], self.cfg.min_area_px, self.cfg.max_area_px)
        return frame, pick_target([(d.cx, d.cy) for d in dets], expect)


def _moved(target: tuple[float, float] | None, last: tuple[float, float] | None) -> bool:
    if target is None:
        return False
    if last is None:
        return True
    return math.hypot(target[0] - last[0], target[1] - last[1]) >= CARRY_MOVED_MIN_PX


def carry_calibration(camera, robot, cfg, object_spec, out_path: str | Path, points=None, preset: str = "full",
                      hover_gap_mm: float = DRIVE_HOVER_GAP_MM, ask=input, say=print) -> PixelToRobot:
    """ブロックを最初に 1 個置くだけ。あとはロボットが自分で持ち運び、各点で置いて退避し、カメラが読む。**腕が動く。**

    --drive（各点で人が置く）との違いは、人の手が要るのが最初の 1 回だけなこと。対応点の意味は同じで
    「命令した座標 → 吸盤が実際に置いた場所」。各点: 置く(z_place) → 退避 → 撮って検出 → 記録 → 拾う(z_pick) → 次へ。
    - 拾えていなかった（置いたはずの物が前の点の画素から動いていない）→ 前の点から拾い直して続ける（自動・1 回）
    - 見えない／4 点そろった後の残差が大きい（途中で落ちた・別の物を見た）→ その点の上で止まり、人に置き直してもらう
      （Enter。s=その点を飛ばす・q=中断＝ホームへ戻り、4 点以上あれば保存）
    終わりに、残差が CARRY_RESIDUAL_MAX_MM を超える点を外して当てはめ直す（5 点は残す）。最後の点に物を置いたまま home。
    Ctrl+C はその場で止める（ホームへ戻さない）。
    """
    from dobot.robot_dobot import EmergencyStop
    rc = cfg.robot
    points = list(points or carry_points(rc, preset))
    if len(points) < 4:
        raise RuntimeError(f"較正点が {len(points)} 点しかありません（作業域 x/y/reach の設定を確認）")
    z_pick, z_hover, z_place = object_heights(rc, object_spec, hover_gap_mm)
    out_path = Path(out_path)
    old = PixelToRobot.load(out_path) if out_path.exists() else None
    sidecar = pairs_sidecar(out_path)
    c = Carrier(camera, robot, cfg, object_spec, z_pick, z_place)
    pairs: list[Pair] = []
    frame = None
    last_pixel: tuple[float, float] | None = None
    prev_point: tuple[float, float] | None = None
    holding = False
    skipped: list[tuple[float, float]] = []
    n = len(points)
    say(f"{n} 点を回ります（{object_spec.name} は腕が持ち運びます。吸盤は物の上面 −1 mm の z={z_pick:.1f} で吸い、"
        f"z={z_place:.1f} で離します）。最初の点でだけ、吸盤の真下に置いて Enter。腕が動きます: 手を退けてください")
    try:
        robot.home()
        x0, y0 = points[0]
        c.hover(x0, y0, z_hover)
        first = ask(f"[1/{n}] ({x0:.0f},{y0:.0f}) {object_spec.name} を吸盤の真下（中心）に置いて Enter / q=中断: ")
        robot.move_to(x0, y0, rc.z_safe)
        if first.strip().lower() == "q":
            robot.home()
            raise RuntimeError("中断しました（記録なし）")
        aborted = False
        for i, (x, y) in enumerate(points):
            retried = False
            human_tries = 0
            while True:
                if holding:
                    c.place(x, y)
                    holding = False
                frame, target = c.look(y, old.to_pixel(x, y) if old is not None else None)
                size = (frame.shape[1], frame.shape[0])
                moved = _moved(target, last_pixel)
                res = residual_mm(pairs, target, (x, y), size) if moved else 0.0
                if moved and res <= CARRY_RESIDUAL_MAX_MM:
                    pairs = record_pair(pairs, target, (x, y), sidecar)
                    last_pixel = target
                    say(f"  [{i + 1}/{n}] pixel=({target[0]:.0f},{target[1]:.0f}) -> ({x:.0f},{y:.0f})"
                        + (f"  残差 {res:.1f} mm" if len(pairs) > 4 else ""))
                    break
                if target is not None and not moved and prev_point is not None and not retried:
                    say(f"  [{i + 1}/{n}] 前の点から動いていません（拾えていなかった）。前の点から拾い直します")
                    retried = True
                    c.pick(*prev_point)
                    holding = True
                    continue
                why = ("見えません" if target is None else
                       "前の点から動いていません" if not moved else
                       f"残差 {res:.1f} mm で合いません（途中で落ちた・別の物を見た？）")
                if human_tries >= CARRY_HUMAN_TRIES:
                    skipped.append((x, y))
                    say(f"  [{i + 1}/{n}] {why}。{CARRY_HUMAN_TRIES} 回置き直しても読めないので、この点は飛ばします")
                    break
                human_tries += 1
                c.hover(x, y, z_hover)
                a = ask(f"  [{i + 1}/{n}] {object_spec.name} が{why}。吸盤の真下（中心）に置いて Enter / "
                        "s=この点を飛ばす / q=中断: ").strip().lower()
                robot.move_to(x, y, rc.z_safe)
                if a == "q":
                    aborted = True
                    break
                if a == "s":
                    skipped.append((x, y))
                    say("  飛ばします（物は今の点に置かれている前提で次へ運びます）")
                    break
                retried = True       # 人が置いたので、次の「動いていない」は拾い直しでなく人に聞く
                last_pixel = None    # 置き直したので「動いた」判定の基準も捨てる
            if aborted:
                break
            if i + 1 < n:            # 次の点へ運ぶために拾う。最後の点には置いたままにする
                c.pick(x, y)
                holding = True
                prev_point = (x, y)
        robot.home()
    except (KeyboardInterrupt, EmergencyStop):
        say(f"中断: 腕はその場で止めます（ホームへは戻しません）。記録済み {len(pairs)} 点は {sidecar} にあります")
        raise
    if len(pairs) < 4:
        raise RuntimeError(f"only {len(pairs)} pairs; need >= 4 (they are kept in {sidecar})")
    if not well_conditioned([(p.x, p.y) for p in pairs]):
        raise RuntimeError(f"記録した {len(pairs)} 点が一直線に近く、較正できません（{sidecar} に残しました）。"
                           "飛ばした点を減らしてやり直してください")
    h, w = frame.shape[:2]
    p2r, kept, dropped = fit_dropping_outliers(pairs, (w, h))
    if old is not None:
        bak = from_root(cfg.log_dir) / f"homography_before_carry_{time.strftime('%Y%m%d_%H%M%S')}.json"
        bak.parent.mkdir(parents=True, exist_ok=True)
        bak.write_text(out_path.read_text(encoding="utf-8"), encoding="utf-8")
        say(f"前の較正を {bak} に退避しました")
    p2r.save(out_path, kept)
    save_pairs(sidecar, kept)
    rms = p2r.rms_error_mm(kept)
    say(f"saved {out_path}  {len(kept)} 点  RMS={rms:.2f} mm  scale={p2r.mm_per_px(w / 2, h / 2):.3f} mm/px"
        + ("  → 合格（3 mm 以下）" if rms <= VERIFY_OK_MM else "  → 3 mm を超えています: カメラの固定・物の置き方を見てやり直し"))
    if dropped:
        say(f"  残差 {CARRY_RESIDUAL_MAX_MM:g} mm 超で外した点: " + ", ".join(f"({p.x:.0f},{p.y:.0f})" for p in dropped))
    if skipped:
        say("  飛ばした点: " + ", ".join(f"({x:.0f},{y:.0f})" for x, y in skipped))
    b = drive_bias(old, kept)
    if b is not None:
        side = "外側" if b["mean_radial"] > 0 else "内側"
        say(f"前の較正の狙いのずれ: 平均 dx={b['mean_dx']:+.1f} dy={b['mean_dy']:+.1f} mm、基部から見て{side}に "
            f"{abs(b['mean_radial']):.1f} mm、RMS {b['rms']:.1f} mm、最大 {b['max']:.1f} mm")
    return p2r


def verify_calibration(camera, robot, cfg, object_spec, p2r: PixelToRobot, target_xy=None,
                       hover_gap_mm: float = DRIVE_HOVER_GAP_MM, say=print) -> dict:
    """今の較正の確認（--verify・約 30 秒）: 見えている物を今の較正で拾い、中心の点に置き、置いた位置の読みと
    命令座標の差を測る。**腕が動く。** 人は物を作業域のどこかに置いておくだけ。

    拾えなかった（物の画素が動かない）＝較正がずれているか吸着不良。
    戻り値: ok（VERIFY_OK_MM 以下）・error_mm・dx・dy・picked_at・placed_at・reason。
    """
    rc = cfg.robot
    z_pick, _, z_place = object_heights(rc, object_spec, hover_gap_mm)
    c = Carrier(camera, robot, cfg, object_spec, z_pick, z_place)
    tx, ty = target_xy or grid_center(drive_grid(rc))
    robot.home()
    frame, start = c.look(ty)
    if start is None:                                   # 退避した腕の陰かもしれないので反対側からもう一度
        frame, start = c.look(-1.0 if ty >= 0 else 1.0)
    if start is None:
        robot.home()
        return {"ok": False, "reason": f"{object_spec.name} が見えません（作業域に 1 個置いてください）"}
    x1, y1 = p2r.to_robot(*start)
    if not robot.guard.inside(x1, y1, z_pick):
        robot.home()
        return {"ok": False, "reason": f"見えている位置 ({x1:.0f},{y1:.0f}) は作業域の外です（ロボット側へ寄せてください）",
                "seen_at": [x1, y1]}
    say(f"  ({x1:.0f},{y1:.0f}) で拾って ({tx:.0f},{ty:.0f}) へ置きます")
    c.pick(x1, y1)
    c.place(tx, ty)
    frame, after = c.look(ty, p2r.to_pixel(tx, ty))
    robot.home()
    if not _moved(after, start):
        return {"ok": False, "reason": "拾えませんでした（較正のずれか吸着不良）。較正をやり直してください",
                "picked_at": [x1, y1], "placed_at": [tx, ty]}
    ex, ey = p2r.to_robot(*after)
    dx, dy = ex - tx, ey - ty
    err = math.hypot(dx, dy)
    ok = err <= VERIFY_OK_MM
    return {"ok": ok, "error_mm": err, "dx": dx, "dy": dy, "picked_at": [x1, y1], "placed_at": [tx, ty],
            "reason": "" if ok else f"ずれ {err:.1f} mm（合格は {VERIFY_OK_MM:g} mm 以下）。較正をやり直してください"}


def interactive_calibration(camera, robot, out_path: str | Path, use_aruco: bool = True,
                            object_spec=None, min_area: int = 400, max_area: int = 60000,
                            resume: bool = True) -> PixelToRobot:
    """実機用。camera.read() の画像に対し、ArUco 中心（または 目印の物・クリック点）へロボットを合わせ pose を読む。

    robot は robot_dobot.RobotBase（pose() が (x,y,z,r)）。robot=None なら XY を手入力。
    object_spec（config.ObjectSpec）を渡すと、マーカーの代わりにその物（例: 青ブロック）の中心を目標にする。
    俯瞰カメラでは腕を合わせた瞬間に物が隠れるので 2 段階: 腕をどけたまま space でピクセルをロック →
    吸盤の先端をその点へ手で合わせ、もう一度 space で pose を読んで対にする。場所を変えて 4 回以上。
    複数見えるときはクリックした物。
    記録した点は 1 点ごとに pairs_sidecar(out_path) へ書かれ、途中で落ちても保存後も残る。resume=True なら
    起動時にそれを読み戻して続きから記録する（点を足して精度を上げる用。CLI の --fresh で捨てる）。
    キー: [space]=ロック→記録 / [u]=ロック取り消し / [d]=直前の点を捨てる / クリック=その物を目標に /
          [q]=終了して保存
    """
    import cv2
    sidecar = pairs_sidecar(out_path)
    pairs: list[Pair] = load_pairs(sidecar) if resume else []
    if pairs:
        print(f"resumed {len(pairs)} pairs from {sidecar} (run with --fresh to discard them)")
    click_pt: list[tuple[float, float]] = []
    if object_spec is not None:
        use_aruco = False
        from dobot.detect import detect_objects

    def on_mouse(event, x, y, flags, param):
        if event == cv2.EVENT_LBUTTONDOWN:
            click_pt.clear()
            click_pt.append((float(x), float(y)))

    win = "calibrate (space=lock/record, u=cancel lock, d=drop last, q=quit)"
    cv2.namedWindow(win)
    cv2.setMouseCallback(win, on_mouse)
    current_target: tuple[float, float] | None = None
    locked_pt: tuple[float, float] | None = None
    locked_at = 0.0
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
        if object_spec is not None:
            dets = detect_objects(frame, [object_spec], min_area, max_area)
            centers = [(d.cx, d.cy) for d in dets]
            for u, v in centers:
                cv2.circle(vis, (int(u), int(v)), 6, (0, 255, 255), 2)
            current_target = pick_target(centers, click_pt[0] if click_pt else None)
            if locked_pt is not None:
                cv2.circle(vis, (int(locked_pt[0]), int(locked_pt[1])), 12, (255, 0, 255), 3)
                cv2.putText(vis, f"LOCKED: move tool tip to the magenta circle, SPACE records pose ([u]=cancel) ({len(pairs)}/4+)",
                            (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 0, 255), 2)
            elif current_target:
                cv2.circle(vis, (int(current_target[0]), int(current_target[1])), 10, (0, 200, 0), 2)
                cv2.putText(vis, f"{object_spec.name}: keep the arm away, SPACE locks this point ({len(pairs)}/4+)",
                            (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 200, 0), 2)
            else:
                cv2.putText(vis, f"{object_spec.name} not seen - place it in view", (10, 30),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
        elif click_pt:
            current_target = click_pt[0]
            cv2.circle(vis, (int(click_pt[0][0]), int(click_pt[0][1])), 6, (255, 0, 255), 2)
        for p in pairs:
            cv2.circle(vis, (int(p.u), int(p.v)), 5, (0, 0, 255), -1)
        cv2.putText(vis, f"pairs={len(pairs)}", (10, vis.shape[0] - 12), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
        cv2.imshow(win, vis)
        k = cv2.waitKey(30) & 0xFF
        if k == ord("q"):
            break
        if k == ord("u") and object_spec is not None:
            locked_pt = None
        if k == ord("d"):
            pairs = drop_last_pair(pairs, sidecar)
            print(f"dropped last pair, {len(pairs)} left")
        if k == ord(" ") and object_spec is not None:
            action, pt = space_transition(locked_pt, current_target, time.monotonic() - locked_at)
            if action == "lock":
                locked_pt = pt
                locked_at = time.monotonic()
                click_pt.clear()
                print("pixel locked", pt, "- now move the tool tip there, SPACE again")
            elif action == "wait":
                print("ignored: SPACE too soon after the lock - move the tool tip onto the block first")
            elif action == "record":
                if robot is not None:
                    x, y, *_ = robot.pose()
                else:
                    x, y = map(float, input("robot X,Y [mm]: ").split(","))
                pairs = record_pair(pairs, pt, (x, y), sidecar)
                locked_pt = None
                print("recorded", pairs[-1], f"-> {sidecar}")
        elif k == ord(" ") and current_target is not None:
            if robot is not None:
                x, y, *_ = robot.pose()
            else:
                x, y = map(float, input("robot X,Y [mm]: ").split(","))
            label = ""
            if use_aruco and not click_pt:
                done_ids = {int(p.label[2:]) for p in pairs if p.label.startswith("id")}
                todo = [i for i in marker_ids if i not in done_ids]
                label = f"id{todo[0]}" if todo else "pt"
            pairs = record_pair(pairs, current_target, (x, y), sidecar, label)
            click_pt.clear()
            print("recorded", pairs[-1], f"-> {sidecar}")
    cv2.destroyWindow(win)
    if len(pairs) < 4:
        raise RuntimeError(f"only {len(pairs)} pairs; need >= 4 (they are kept in {sidecar}; rerun to continue)")
    h, w = frame.shape[:2]
    p2r = PixelToRobot.fit(pairs, (w, h))
    p2r.save(out_path, pairs)
    save_pairs(sidecar, pairs)  # 残しておく: 次回起動で続きから点を足せる（やり直しは --fresh）
    print(f"saved {out_path}  RMS={p2r.rms_error_mm(pairs):.2f} mm  scale={p2r.mm_per_px(w/2, h/2):.3f} mm/px")
    return p2r


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="eye-to-hand calibration")
    ap.add_argument("--config", default=str(CONFIG_PATH))
    ap.add_argument("--out", default=None)
    ap.add_argument("--manual", action="store_true", help="ArUco を使わずクリック点で対応を取る")
    ap.add_argument("--object", default=None, help="マーカーの代わりに置く物の名前（例: eraser。config に無ければ既定の定義）")
    ap.add_argument("--no-robot", action="store_true", help="ロボット未接続: XY を手入力")
    ap.add_argument("--fresh", action="store_true", help="前回途中まで記録した対応点（*.pairs.json）を捨てて最初から")
    ap.add_argument("--drive", action="store_true",
                    help="ロボット自身が格子状に動き、各点でその真下に人が置いた --object の物をカメラで読んで較正する（腕が動く）")
    ap.add_argument("--carry", action="store_true",
                    help="推奨: --object の物を最初に 1 個置くだけ。ロボットが自分で持ち運んで格子を回り較正する（腕が動く）")
    ap.add_argument("--verify", action="store_true",
                    help="今の較正の確認（約 30 秒・腕が動く）: 見えている --object の物を拾って中心へ置き、ずれを測る")
    ap.add_argument("--points", choices=GRID_PRESETS, default="full",
                    help="--carry の点数: quick=5 点（中心＋四隅・約 40 秒）/ full=9 点（3×3・約 1 分）")
    ap.add_argument("--make-markers", default=None, help="印刷用 ArUco PNG を出力するフォルダ")
    args = ap.parse_args(argv)
    if (args.carry or args.verify) and not args.object:
        args.object = "blue_cube"
    if args.make_markers:
        make_aruco_sheet(args.make_markers)
        print("markers written to", args.make_markers)
        raise SystemExit(0)
    from dobot.camera import make_camera
    from dobot.config import AppConfig
    cfg = AppConfig.load(args.config) if Path(args.config).exists() else AppConfig.default()
    robot = None
    if not args.no_robot:
        from dobot.robot_dobot import make_robot
        robot = make_robot(cfg.robot)
        robot.connect()
    spec = None
    if args.object:
        spec = next((o for o in cfg.objects if o.name == args.object), None) or \
            next((o for o in AppConfig.default().objects if o.name == args.object), None)
        if spec is None:
            raise SystemExit(f"--object {args.object}: 物体定義がありません")
    out = args.out or cfg.homography_path
    if (args.drive or args.carry or args.verify) and (robot is None or spec is None):
        raise SystemExit("--drive / --carry / --verify には --object と実機接続（--no-robot 無し）が必要です")
    if args.verify and not Path(out).exists():
        raise SystemExit(f"較正ファイルがありません（{out}）。先に --carry で較正してください")
    # 落ちたときの手がかり: Python の例外も、OpenCV 側の致命シグナル（faulthandler）も logs/ に残す
    import faulthandler
    import sys
    import traceback
    crash_path = from_root(cfg.log_dir) / "calibrate_crash.log"
    crash_path.parent.mkdir(parents=True, exist_ok=True)
    crash_log = crash_path.open("a", encoding="utf-8")
    faulthandler.enable(file=crash_log)
    try:
        with make_camera(cfg.camera) as cam:
            if args.verify:
                r = verify_calibration(cam, robot, cfg, spec, PixelToRobot.load(out))
                if r["ok"]:
                    print(f"較正は使えます: ずれ {r['error_mm']:.1f} mm（dx {r['dx']:+.1f} / dy {r['dy']:+.1f}）。やり直しは不要です")
                    return 0
                print("較正の確認: " + r["reason"])
                return 1
            if args.carry:
                carry_calibration(cam, robot, cfg, spec, out, preset=args.points)
            elif args.drive:
                drive_calibration(cam, robot, cfg, spec, out)
            else:
                interactive_calibration(cam, robot, out, use_aruco=not args.manual, object_spec=spec,
                                        min_area=cfg.min_area_px, max_area=cfg.max_area_px, resume=not args.fresh)
    except Exception:
        crash_log.write(f"\n--- {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
        traceback.print_exc(file=crash_log)
        crash_log.flush()
        print(f"traceback written to {crash_path}; recorded pairs are kept in {pairs_sidecar(out)}", file=sys.stderr)
        raise
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
