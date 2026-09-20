"""物体検出（L0: HSV 色しきい値＋形の条件 / L1: 開放語彙検出器フック）。

L0 は決定論で速く（CPU 数 ms）、展示の主線。照明を固定し WB を固定すれば安定する。
v2（2026-09-20）: 色に加えて 円形度（4πA/P²）と 外接矩形の縦横比 で「立方体／ボール／消しゴム」を分ける。
L1（YOLO-World 等）は色と形で拾えない物の拡張用フック。未導入でも L0 は動く。
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np

from config import ObjectSpec


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
        return d


def _mask_for(hsv: np.ndarray, ranges: Sequence[Sequence[Sequence[int]]]) -> np.ndarray:
    import cv2
    mask = None
    for lo, hi in ranges:
        m = cv2.inRange(hsv, np.array(lo, dtype=np.uint8), np.array(hi, dtype=np.uint8))
        mask = m if mask is None else cv2.bitwise_or(mask, m)
    return mask


def shape_features(contour) -> tuple[float, float, float]:
    """(面積, 円形度, 長辺/短辺)。円形度は 4πA/P²（円=1・正方形≈0.785）。"""
    import cv2
    area = float(cv2.contourArea(contour))
    per = float(cv2.arcLength(contour, True))
    circ = (4.0 * math.pi * area / (per * per)) if per > 0 else 0.0
    (_, _), (w, h), _ = cv2.minAreaRect(contour)
    lo, hi = (min(w, h), max(w, h))
    aspect = (hi / lo) if lo > 0 else 99.0
    return area, circ, aspect


def detect_objects(frame_bgr: np.ndarray, specs: Sequence[ObjectSpec], min_area: int = 400,
                   max_area: int = 60000, roi: tuple[int, int, int, int] | None = None,
                   blur_ksize: int = 5, morph_ksize: int = 5) -> list[Detection]:
    """対象物ごとに HSV マスク → 開閉 → 輪郭 → 面積・円形度・縦横比で選別。面積の大きい順に返す。"""
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
    for spec in specs:
        lo_area = spec.min_area_px if spec.min_area_px is not None else min_area
        hi_area = spec.max_area_px if spec.max_area_px is not None else max_area
        mask = _mask_for(hsv, spec.ranges)
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
            dets.append(Detection(spec.name, cx + ox, cy + oy, area, (bx + ox, by + oy, bw, bh), float(ang),
                                  circularity=circ, aspect=aspect))
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
                         bars: Sequence[tuple[str, int, int, int, int]] = ()) -> np.ndarray:
    """テスト用: 黒マット上に色ブロック（正方形）・ボール（円）・棒（長方形）を描く。

    blocks=(color, cx, cy, size_px)・balls=(color, cx, cy, radius_px)・bars=(color, cx, cy, w, h)。
    color は "red"/"red_cube" のどちらでもよい（先頭の色名で引く）。
    """
    import cv2
    img = np.full((height, width, 3), (40, 40, 40), dtype=np.uint8)

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
