"""物体検出（L0: HSV 色しきい値＋形の条件 / L1: 開放語彙検出器フック）。

L0 は決定論で速く（CPU 数 ms）、展示の主線。照明を固定し WB を固定すれば安定する。
v2（2026-09-20）: 色に加えて 円形度（4πA/P²）と 外接矩形の縦横比 で「立方体／ボール／消しゴム」を分ける。
L1（YOLO-World 等）は色と形で拾えない物の拡張用フック。未導入でも L0 は動く。
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field, replace

import numpy as np

from dobot.config import ObjectSpec


@dataclass
class Detection:
    name: str                  # 対象物名（ObjectSpec.name）または開放語彙のラベル
    cx: float                  # 画像中心 [px]
    cy: float
    area: float                # [px^2]
    bbox: tuple[int, int, int, int]   # x, y, w, h
    angle_deg: float = 0.0     # minAreaRect の角度（グリッパ回転用）
    x_mm: float | None = None      # ロボット座標（キャリブ後に planner が埋める）
    y_mm: float | None = None
    size_mm: float | None = None   # 外接矩形の長辺 [mm]（planner が埋める。置くときに他の物と重ねないための大きさ）
    score: float = 1.0
    circularity: float = 0.0
    aspect: float = 1.0        # 長辺/短辺
    extra: dict = field(default_factory=dict)

    def to_public(self) -> dict:
        """LLM に渡す最小表現（座標は丸める・内部情報は出さない）。"""
        d = {"id": self.extra.get("id"), "name": self.name, "area_px": int(self.area),
             "u": int(self.cx), "v": int(self.cy)}
        if self.x_mm is not None:
            d["x_mm"] = round(self.x_mm, 1)
            d["y_mm"] = round(self.y_mm, 1)
        if self.size_mm is not None:
            d["size_mm"] = round(self.size_mm, 1)
        return d


def _mask_for(hsv: np.ndarray, ranges: Sequence[Sequence[Sequence[int]]]) -> np.ndarray:
    import cv2
    mask = None
    for lo, hi in ranges:
        m = cv2.inRange(hsv, np.array(lo, dtype=np.uint8), np.array(hi, dtype=np.uint8))
        mask = m if mask is None else cv2.bitwise_or(mask, m)
    return mask


def shape_features(contour) -> tuple[float, float, float]:
    """(面積, 円形度, 長辺/短辺)。円形度は輪郭の凸包で測る 4πA/P²（円=1・正方形≈0.785）。

    凸包で測るのは、ゴルフボールのディンプルや縁の影で輪郭がぎざぎざになっても「丸い物」と分かるようにするため
    （生の輪郭だと周長が伸びて円形度が 0.85 を割る。2026-10-26）。面積は生の輪郭のまま（大きさの判定用）。
    """
    import cv2
    area = float(cv2.contourArea(contour))
    hull = cv2.convexHull(contour)
    hull_area = float(cv2.contourArea(hull))
    per = float(cv2.arcLength(hull, True))
    circ = (4.0 * math.pi * hull_area / (per * per)) if per > 0 else 0.0
    (_, _), (w, h), _ = cv2.minAreaRect(contour)
    lo, hi = (min(w, h), max(w, h))
    aspect = (hi / lo) if lo > 0 else 99.0
    return area, circ, aspect


# ------------------------------------------- 白い机の白い球（detector="sphere"）: 円のあたり＋縁の影
SPHERE_RADIUS_SLACK = (0.75, 1.35)   # 想定半径に対する探索範囲（球の赤道はカメラに近いぶん少し大きく写る）
DARK_TABLE_V = 150                   # 画面の明るさの中央値がこれ未満なら「暗いマット」: 白い物は色（HSV）でも探せる


def sphere_radius_px(spec: ObjectSpec, mm_per_px: float | None, lo_area: float, hi_area: float) -> tuple[int, int]:
    """sphere の探索半径 [px]。較正（mm_per_px）と直径があればその前後、無ければ面積の下限・上限から。"""
    if mm_per_px and spec.diameter_mm:
        r0 = spec.diameter_mm / 2.0 / mm_per_px
        return max(5, int(r0 * SPHERE_RADIUS_SLACK[0])), max(6, int(r0 * SPHERE_RADIUS_SLACK[1]) + 1)
    return max(5, int(math.sqrt(lo_area / math.pi))), max(6, int(math.sqrt(hi_area / math.pi)) + 1)


def sphere_candidates(gray: np.ndarray, rmin: int, rmax: int, param2: float = 28.0) -> list[tuple[float, float, float]]:
    """HoughCircles（勾配法）で円のあたりを取る。戻り値は (cx, cy, r) の列（票の多い順）。"""
    import cv2
    rmin, rmax = max(5, int(rmin)), max(int(rmin) + 1, int(rmax))
    circles = cv2.HoughCircles(gray, cv2.HOUGH_GRADIENT, dp=1.2, minDist=max(10.0, rmin * 1.5),
                               param1=100, param2=float(param2), minRadius=rmin, maxRadius=rmax)
    if circles is None:
        return []
    return [(float(x), float(y), float(r)) for x, y, r in circles[0]]


def sphere_profile(gray: np.ndarray, sat: np.ndarray, cx: float, cy: float, r: float) -> dict:
    """円の内側（0〜0.55r）・縁（0.78〜1.0r）・外側の机（1.25〜1.65r）の平均明るさと、内側の平均彩度。

    白い机の白い球は、中心は机と同じか明るく（ハイライト）、縁は陰で暗く、片側に落ちる影が付く。
    平らな白い物（紙・ブロックの上面）は中心と縁が同じ明るさなので球と区別できる。
    """
    h, w = gray.shape[:2]
    x0, x1 = max(0, int(cx - 1.7 * r)), min(w, int(cx + 1.7 * r) + 1)
    y0, y1 = max(0, int(cy - 1.7 * r)), min(h, int(cy + 1.7 * r) + 1)
    g = gray[y0:y1, x0:x1].astype(np.float32)
    s = sat[y0:y1, x0:x1].astype(np.float32)
    yy, xx = np.ogrid[y0:y1, x0:x1]
    d2 = (xx - cx) ** 2 + (yy - cy) ** 2
    inner = d2 <= (0.55 * r) ** 2
    rim = (d2 >= (0.78 * r) ** 2) & (d2 <= r ** 2)
    table = (d2 >= (1.25 * r) ** 2) & (d2 <= (1.65 * r) ** 2)

    def mean(arr, m):
        v = arr[m]
        return float(v.mean()) if v.size else float("nan")

    return {"center": mean(g, inner), "rim": mean(g, rim), "table": mean(g, table), "saturation": mean(s, inner)}


def sphere_ok(profile: dict, spec: ObjectSpec) -> bool:
    """球と認める条件: 縁が机と違う明るさ（rim_contrast・白い机なら暗く、黒いマットなら明るい）・縁は中心より暗い
    （球の陰影 min_limb_drop。平らな紙・落ちた影・ブロックの上面は中心と縁が同じなので落ちる）・内側が白っぽい（彩度）。"""
    p = profile
    if any(math.isnan(v) for v in p.values()):
        return False
    limb_drop = getattr(spec, "min_limb_drop", 6.0)
    return (abs(p["table"] - p["rim"]) >= spec.rim_contrast and p["center"] - p["rim"] >= limb_drop
            and p["saturation"] <= spec.max_saturation)


def _inside_any(cx: float, cy: float, dets: Sequence[Detection]) -> bool:
    for d in dets:
        x, y, w, h = d.bbox
        if x <= cx <= x + w and y <= cy <= y + h:
            return True
    return False


def detect_spheres(img_bgr: np.ndarray, hsv: np.ndarray, spec: ObjectSpec, lo_area: float, hi_area: float,
                   mm_per_px: float | None, exclude: Sequence[Detection] = ()) -> list[Detection]:
    """白い机の白い球を 1 種ぶん。色の検出（exclude）の中にある円は球と言わない。"""
    import cv2
    gray = cv2.medianBlur(cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY), 5)
    rmin, rmax = sphere_radius_px(spec, mm_per_px, lo_area, hi_area)
    # 円のあたりは半分の大きさの画像で取る（HoughCircles は半径の幅と画素数に比例して遅い。1280×720 で 150 ms → 約 40 ms）。
    # 中心と半径は 2 倍して戻し、明るさの検査は元の大きさで行う。精度は 1 px（0.3〜0.5 mm）落ちるだけ
    small = cv2.resize(gray, (gray.shape[1] // 2, gray.shape[0] // 2), interpolation=cv2.INTER_AREA)
    cands = [(2 * x, 2 * y, 2 * r) for x, y, r in sphere_candidates(small, rmin // 2, rmax // 2 + 1, spec.hough_param2)]
    out: list[Detection] = []
    for cx, cy, r in cands:
        area = math.pi * r * r
        if area < lo_area or area > hi_area or _inside_any(cx, cy, exclude):
            continue
        if any(math.hypot(d.cx - cx, d.cy - cy) < max(r, d.extra["radius_px"]) for d in out):
            continue   # 同じ球に半径違いの円が重なったら、票の多い最初の円だけ残す
        p = sphere_profile(gray, hsv[:, :, 1], cx, cy, r)
        if not sphere_ok(p, spec):
            continue
        contrast = abs(p["table"] - p["rim"])
        out.append(Detection(spec.name, cx, cy, area, (int(cx - r), int(cy - r), int(2 * r), int(2 * r)), 0.0,
                             score=float(contrast), circularity=1.0, aspect=1.0,
                             extra={"radius_px": round(r, 1), "rim_contrast": round(contrast, 1),
                                    "limb_drop": round(p["center"] - p["rim"], 1)}))
    return out


def _detect_hsv(img_hsv: np.ndarray, spec: ObjectSpec, kernel, lo_area: float, hi_area: float) -> list[Detection]:
    """色（ranges）＋形（面積・円形度・縦横比）で 1 種ぶん。座標は渡した画像のもの（ROI のずらしは呼び出し側）。"""
    import cv2
    out: list[Detection] = []
    mask = _mask_for(img_hsv, spec.ranges)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for c in contours:
        area, circ, aspect = shape_features(c)
        if area < lo_area or area > hi_area:
            continue
        if circ < spec.min_circularity or not (spec.aspect_min <= aspect <= spec.aspect_max):
            continue
        M = cv2.moments(c)
        if M["m00"] == 0:
            continue
        cx, cy = M["m10"] / M["m00"], M["m01"] / M["m00"]
        bx, by, bw, bh = cv2.boundingRect(c)
        (_, _), (_, _), ang = cv2.minAreaRect(c)
        out.append(Detection(spec.name, cx, cy, area, (bx, by, bw, bh), float(ang), circularity=circ, aspect=aspect))
    return out


def detect_objects(frame_bgr: np.ndarray, specs: Sequence[ObjectSpec], min_area: int = 400,
                   max_area: int = 60000, roi: tuple[int, int, int, int] | None = None,
                   blur_ksize: int = 5, morph_ksize: int = 5, mm_per_px: float | None = None) -> list[Detection]:
    """対象物ごとに HSV マスク → 開閉 → 輪郭 → 面積・円形度・縦横比で選別。面積の大きい順に返す。

    detector="sphere" の物（白い机の白い球）は色でなく円のあたり＋縁の影で探す（detect_spheres）。色の検出を先に済ませ、
    その枠の中にある円は球と言わない。mm_per_px（較正の縮尺）があれば直径から半径の目安を取る。
    球が見つからず、かつ画面が暗い（黒いマット: 明るさの中央値 < DARK_TABLE_V）ときだけ、従来どおり色（白）でも探す。
    白い机では色で白を探さない（机そのものが白いので意味が無い）。
    """
    import cv2
    img = frame_bgr
    ox = oy = 0
    if roi is not None:
        x, y, w, h = roi
        img = frame_bgr[y:y + h, x:x + w]
        ox, oy = x, y
    if blur_ksize and blur_ksize > 1:
        img = cv2.GaussianBlur(img, (blur_ksize, blur_ksize), 0)
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (morph_ksize, morph_ksize))
    dets: list[Detection] = []

    def shifted(found: list[Detection]) -> list[Detection]:
        for d in found:
            d.cx += ox
            d.cy += oy
            d.bbox = (d.bbox[0] + ox, d.bbox[1] + oy, d.bbox[2], d.bbox[3])
        return found

    sphere_specs = [s for s in specs if getattr(s, "detector", "hsv") == "sphere"]
    for spec in specs:
        if spec in sphere_specs:
            continue
        lo_area = spec.min_area_px if spec.min_area_px is not None else min_area
        hi_area = spec.max_area_px if spec.max_area_px is not None else max_area
        dets.extend(shifted(_detect_hsv(hsv, spec, kernel, lo_area, hi_area)))
    dark_table = None
    for spec in sphere_specs:
        lo_area = spec.min_area_px if spec.min_area_px is not None else min_area
        hi_area = spec.max_area_px if spec.max_area_px is not None else max_area
        found = detect_spheres(img, hsv, spec, lo_area, hi_area, mm_per_px, exclude=dets)
        if not found and spec.ranges:
            if dark_table is None:
                dark_table = float(np.median(hsv[:, :, 2])) < DARK_TABLE_V
            if dark_table:
                found = _detect_hsv(hsv, spec, kernel, lo_area, hi_area)
        dets.extend(shifted(found))
    dets.sort(key=lambda d: -d.area)
    for i, d in enumerate(dets):
        d.extra["id"] = i
    return dets


_PALETTE = {"red": (0, 0, 255), "green": (0, 200, 0), "blue": (255, 100, 0), "yellow": (0, 220, 255),
            "ball": (0, 140, 255), "eraser": (255, 0, 200), "golf": (255, 255, 255)}


def _color_for(name: str) -> tuple[int, int, int]:
    for k, v in _PALETTE.items():
        if name.startswith(k):
            return v
    return (200, 200, 200)


def mirror_view(frame_bgr: np.ndarray, dets: Sequence[Detection]) -> tuple[np.ndarray, list[Detection]]:
    """表示用に左右反転した画像と、画素座標だけ反転した検出のコピー。ロボット座標・大きさは変えない。

    較正は元の向きの画像で取っているので、処理は元のまま、見せる絵だけ反転する（demo.panel_mirror、2026-10-19）。
    """
    w = frame_bgr.shape[1]
    out = [replace(d, cx=float(w - 1 - d.cx), bbox=(w - d.bbox[0] - d.bbox[2], d.bbox[1], d.bbox[2], d.bbox[3]),
                   extra=dict(d.extra)) for d in dets]
    return np.ascontiguousarray(frame_bgr[:, ::-1]), out


def annotate(frame_bgr: np.ndarray, dets: Sequence[Detection], status: str = "",
             labels: dict[str, str] | None = None) -> np.ndarray:
    """パネル表示用の描画（来場者に「何を見て判断したか」を見せる）。"""
    import cv2
    vis = frame_bgr.copy()
    for d in dets:
        x, y, w, h = d.bbox
        col = _color_for(d.name)
        cv2.rectangle(vis, (x, y), (x + w, y + h), col, 2)
        label = f"#{d.extra.get('id')} {d.name}"
        if d.x_mm is not None:
            label += f" ({d.x_mm:.0f},{d.y_mm:.0f})"
        if d.extra.get("reachable") is False:
            label += " x"
        cv2.putText(vis, label, (x, max(12, y - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.55, col, 2)
        cv2.circle(vis, (int(d.cx), int(d.cy)), 4, col, -1)
    if status:
        cv2.rectangle(vis, (0, 0), (vis.shape[1], 34), (30, 30, 30), -1)
        cv2.putText(vis, status, (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
    return vis


class OpenVocabDetector:
    """L1: 開放語彙検出（Ultralytics YOLO-World）。`pip install ultralytics` 後に使える。

    使い方: det = OpenVocabDetector(); det.set_classes(["sponge", "eraser"]); dets = det.detect(frame)
    展示の主線ではない（CPU では数百 ms〜秒）。色と形で拾えない物が要るときの拡張。
    """
    def __init__(self, weights: str = "yolov8s-worldv2.pt", conf: float = 0.25):
        from ultralytics import YOLO  # type: ignore
        self.model = YOLO(weights)
        self.conf = conf

    def set_classes(self, names: Sequence[str]) -> None:
        self.model.set_classes(list(names))

    def detect(self, frame_bgr: np.ndarray) -> list[Detection]:
        res = self.model.predict(frame_bgr, conf=self.conf, verbose=False)[0]
        out: list[Detection] = []
        for i, b in enumerate(res.boxes):
            x1, y1, x2, y2 = map(float, b.xyxy[0].tolist())
            name = res.names[int(b.cls[0])]
            out.append(Detection(name, (x1 + x2) / 2, (y1 + y2) / 2, (x2 - x1) * (y2 - y1),
                                 (int(x1), int(y1), int(x2 - x1), int(y2 - y1)), 0.0, score=float(b.conf[0]),
                                 extra={"id": i}))
        return out


_BGR = {"red": (0, 0, 220), "green": (0, 200, 0), "blue": (220, 60, 0), "yellow": (0, 220, 240),
        "orange": (0, 140, 255), "white": (245, 245, 245)}


def make_synthetic_frame(width: int = 1280, height: int = 720,
                         blocks: Sequence[tuple[str, int, int, int]] = (),
                         balls: Sequence[tuple[str, int, int, int]] = (),
                         bars: Sequence[tuple[str, int, int, int, int]] = (),
                         bg: tuple[int, int, int] = (40, 40, 40)) -> np.ndarray:
    """テスト用: マット（既定は黒）の上に色ブロック（正方形）・ボール（円）・棒（長方形）を描く。

    blocks=(color, cx, cy, size_px)・balls=(color, cx, cy, radius_px)・bars=(color, cx, cy, w, h)。
    color は "red"/"red_cube" のどちらでもよい（先頭の色名で引く）。bg=(228,228,228) で白い机。
    """
    import cv2
    img = np.full((height, width, 3), bg, dtype=np.uint8)

    def bgr(name: str):
        for k, v in _BGR.items():
            if name.startswith(k):
                return v
        raise KeyError(name)

    for color, cx, cy, s in blocks:
        cv2.rectangle(img, (cx - s // 2, cy - s // 2), (cx + s // 2, cy + s // 2), bgr(color), -1)
    for color, cx, cy, r in balls:
        cv2.circle(img, (cx, cy), r, bgr(color), -1)
    for color, cx, cy, w, h in bars:
        cv2.rectangle(img, (cx - w // 2, cy - h // 2), (cx + w // 2, cy + h // 2), bgr(color), -1)
    return img
