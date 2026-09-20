"""物体検出（L0: HSV 色しきい値 / L1: 開放語彙検出器フック）。

L0 は決定論で速く（CPU 1ms 級）、展示の主線。照明を固定し WB を固定すれば安定する。
L1（YOLO-World 等）は「スポンジ」「消しゴム」など色以外の指示の拡張用フック。未導入でも L0 は動く。
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np

from config import ColorRange


@dataclass
class Detection:
    color: str                 # 色名（ColorRange.name）または開放語彙のラベル
    cx: float                  # 画像中心 [px]
    cy: float
    area: float                # [px^2]
    bbox: tuple[int, int, int, int]   # x, y, w, h
    angle_deg: float = 0.0     # minAreaRect の角度（グリッパ回転用）
    x_mm: float | None = None      # ロボット座標（キャリブ後に planner が埋める）
    y_mm: float | None = None
    score: float = 1.0
    extra: dict = field(default_factory=dict)

    def to_public(self) -> dict:
        """LLM に渡す最小表現（座標は丸める・内部情報は出さない）。"""
        d = {"id": self.extra.get("id"), "color": self.color, "area_px": int(self.area),
             "u": int(self.cx), "v": int(self.cy)}
        if self.x_mm is not None:
            d["x_mm"] = round(self.x_mm, 1)
            d["y_mm"] = round(self.y_mm, 1)
        return d


def _mask_for(hsv: np.ndarray, cr: ColorRange) -> np.ndarray:
    import cv2
    mask = None
    for lo, hi in cr.ranges:
        m = cv2.inRange(hsv, np.array(lo, dtype=np.uint8), np.array(hi, dtype=np.uint8))
        mask = m if mask is None else cv2.bitwise_or(mask, m)
    return mask


def detect_colors(frame_bgr: np.ndarray, colors: Sequence[ColorRange], min_area: int = 400,
                  max_area: int = 60000, roi: tuple[int, int, int, int] | None = None,
                  blur_ksize: int = 5, morph_ksize: int = 5) -> list[Detection]:
    """色ごとに HSV マスク → 開閉 → 輪郭 → 面積フィルタ。面積の大きい順に返す。roi=(x,y,w,h)。"""
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
    for cr in colors:
        mask = _mask_for(hsv, cr)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for c in contours:
            area = float(cv2.contourArea(c))
            if area < min_area or area > max_area:
                continue
            M = cv2.moments(c)
            if M["m00"] == 0:
                continue
            cx, cy = M["m10"] / M["m00"], M["m01"] / M["m00"]
            bx, by, bw, bh = cv2.boundingRect(c)
            (_, _), (_, _), ang = cv2.minAreaRect(c)
            dets.append(Detection(cr.name, cx + ox, cy + oy, area, (bx + ox, by + oy, bw, bh), float(ang)))
    dets.sort(key=lambda d: -d.area)
    for i, d in enumerate(dets):
        d.extra["id"] = i
    return dets


def annotate(frame_bgr: np.ndarray, dets: Sequence[Detection], status: str = "") -> np.ndarray:
    """パネル表示用の描画（来場者に「何を見て判断したか」を見せる）。"""
    import cv2
    vis = frame_bgr.copy()
    palette = {"red": (0, 0, 255), "green": (0, 200, 0), "blue": (255, 100, 0), "yellow": (0, 220, 255)}
    for d in dets:
        x, y, w, h = d.bbox
        col = palette.get(d.color, (255, 255, 255))
        cv2.rectangle(vis, (x, y), (x + w, y + h), col, 2)
        label = f"#{d.extra.get('id')} {d.color}"
        if d.x_mm is not None:
            label += f" ({d.x_mm:.0f},{d.y_mm:.0f})"
        cv2.putText(vis, label, (x, max(12, y - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.55, col, 2)
        cv2.circle(vis, (int(d.cx), int(d.cy)), 4, col, -1)
    if status:
        cv2.rectangle(vis, (0, 0), (vis.shape[1], 34), (30, 30, 30), -1)
        cv2.putText(vis, status, (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
    return vis


class OpenVocabDetector:
    """L1: 開放語彙検出（Ultralytics YOLO-World）。`pip install ultralytics` 後に使える。

    使い方: det = OpenVocabDetector(); det.set_classes(["sponge", "eraser"]); dets = det.detect(frame)
    展示の主線ではない（CPU では数百 ms〜秒）。色以外の指示が要るときの拡張。
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


def make_synthetic_frame(width: int = 1280, height: int = 720,
                         blocks: Sequence[tuple[str, int, int, int]] = ()) -> np.ndarray:
    """テスト用: 黒マット上に色ブロックを描いた画像。blocks=(color, cx, cy, size_px)。"""
    import cv2
    bgr = {"red": (0, 0, 220), "green": (0, 200, 0), "blue": (220, 60, 0), "yellow": (0, 220, 240)}
    img = np.full((height, width, 3), (40, 40, 40), dtype=np.uint8)
    for color, cx, cy, s in blocks:
        cv2.rectangle(img, (cx - s // 2, cy - s // 2), (cx + s // 2, cy + s // 2), bgr[color], -1)
    return img
