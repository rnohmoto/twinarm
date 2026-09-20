"""TaskExecutor: 「見る → 対象を選ぶ → 拾って置く」の実行層。LLM／ルール解析の両方がここを呼ぶ。

安全の要点: LLM もルール解析も「対象物名・ゾーン名・選び方ヒント」しか渡さない。
ロボット座標を作るのは、キャリブ済みホモグラフィ（検出→mm）と設定のゾーンだけ。
v2（2026-09-20）: 対象物は色＋形（ObjectSpec）。物ごとの z_pick。ゾーンのスロット（重ねない）と
「もうそのゾーンにある物は動かさない」判定。片付け（tidy_up）＝スタート台へ全部戻す。
"""
from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from calibrate import PixelToRobot
from config import AppConfig, Zone
from detect import Detection, detect_objects
from robot_dobot import EmergencyStop, OutOfWorkspace, PickPlaceController, RobotBase


@dataclass
class Result:
    ok: bool
    message: str                      # 日本語の短い返事（TTS/パネルにそのまま出す）
    data: dict = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps({"ok": self.ok, "message": self.message, **self.data}, ensure_ascii=False)


def in_zone(det: Detection, zone: Zone) -> bool:
    """検出物（ロボット座標つき）がゾーンの半径内にあるか。"""
    if det.x_mm is None or det.y_mm is None:
        return False
    return math.hypot(det.x_mm - zone.x, det.y_mm - zone.y) <= zone.radius_mm


def choose_slot(zone: Zone, dets: list[Detection], clearance_mm: float = 25.0) -> tuple[float, float]:
    """置く位置。スロットがあれば「近くに物が無い」最初のスロット、無ければゾーン中心。"""
    for sx, sy in zone.slots:
        if all(d.x_mm is None or math.hypot(d.x_mm - sx, d.y_mm - sy) > clearance_mm for d in dets):
            return float(sx), float(sy)
    return zone.x, zone.y


class TaskExecutor:
    def __init__(self, cfg: AppConfig, camera, robot: RobotBase, p2r: PixelToRobot | None,
                 logger=None):
        self.cfg = cfg
        self.camera = camera
        self.robot = robot
        self.p2r = p2r
        self.ctrl = PickPlaceController(robot)
        self.last_frame: np.ndarray | None = None
        self.last_dets: list[Detection] = []
        self.last_target: Detection | None = None
        self.logger = logger or (lambda ev: None)
        self.stats = {"picks_ok": 0, "picks_fail": 0, "tidy": 0}

    # ------------------------------------------------------------ perception
    def _z_pick(self, name: str | None) -> float:
        spec = self.cfg.object_by_name(name) if name else None
        return spec.z_pick if (spec and spec.z_pick is not None) else self.cfg.robot.z_pick

    def observe(self) -> list[Detection]:
        frame = self.camera.read()
        dets = detect_objects(frame, self.cfg.objects, self.cfg.min_area_px, self.cfg.max_area_px)
        if self.p2r is not None:
            for d in dets:
                d.x_mm, d.y_mm = self.p2r.to_robot(d.cx, d.cy)
        for d in dets:  # ロボット座標が作業範囲外の検出は「見えているが拾えない」として印を付ける
            d.extra["reachable"] = bool(d.x_mm is not None and
                                        self.robot.guard.inside(d.x_mm, d.y_mm, self._z_pick(d.name)))
        self.last_frame, self.last_dets = frame, dets
        self.logger({"ev": "observe", "n": len(dets), "dets": [d.to_public() for d in dets]})
        return dets

    def list_objects(self) -> Result:
        dets = self.observe()
        if not dets:
            return Result(True, "今は何も見えません。", {"objects": []})
        counts: dict[str, int] = {}
        for d in dets:
            counts[d.name] = counts.get(d.name, 0) + 1
        summary = "、".join(f"{self.cfg.label(k)}が{v}個" for k, v in counts.items())
        return Result(True, f"{summary}見えています。", {"objects": [d.to_public() for d in dets]})

    # -------------------------------------------------------------- selection
    def select(self, dets: list[Detection], name: str | None, hint: str = "any",
               exclude_zone: Zone | None = None) -> Detection | None:
        cands = [d for d in dets if (name is None or d.name == name) and d.extra.get("reachable", True)
                 and not (exclude_zone is not None and in_zone(d, exclude_zone))]
        if not cands:
            return None
        if hint == "largest":
            return max(cands, key=lambda d: d.area)
        if hint == "smallest":
            return min(cands, key=lambda d: d.area)
        if hint == "nearest":  # ロボットに近い=x_mm が小さい（ベース原点に近い）
            return min(cands, key=lambda d: d.x_mm if d.x_mm is not None else d.cy)
        if hint == "leftmost":  # 画像の左（来場者から見た左＝カメラの向きで合わせる。rotation_deg で調整）
            return min(cands, key=lambda d: d.cx)
        if hint == "rightmost":
            return max(cands, key=lambda d: d.cx)
        return cands[0]  # 面積最大（最も確からしい）

    # -------------------------------------------------------------- execution
    def pick_and_place(self, name: str | None, zone_name: str | None, hint: str = "any",
                       count: int = 1) -> Result:
        if self.p2r is None:
            return Result(False, "カメラとロボットの位置合わせ（キャリブレーション）がまだです。")
        zone = self.cfg.zone_by_name(zone_name) if zone_name else None
        if zone is None:
            names = "・".join(z.aliases[0] for z in self.cfg.zones if z.aliases)
            return Result(False, f"どこに置きますか？（{names}）", {"need": "zone"})
        label = self.cfg.label(name) if name else "それ"
        done = 0
        moved: list[dict] = []
        picked_at: list[tuple[float, float]] = []   # 同じ場所に居続ける物（吸着失敗など）を何度も拾いに行かない
        t0 = time.time()
        while True:
            dets = self.observe()
            fresh = [d for d in dets if d.x_mm is None or
                     all(math.hypot(d.x_mm - x, d.y_mm - y) > 15.0 for x, y in picked_at)]
            target = self.select(fresh, name, hint, exclude_zone=zone)
            if target is None:
                if done == 0:
                    seen = [d for d in dets if name is None or d.name == name]
                    if seen and all(in_zone(d, zone) for d in seen):
                        return Result(True, f"{label}はもう{self.cfg.zone_label(zone.name)}にあります。")
                    if seen:
                        return Result(False, f"{label}は見えていますが、腕が届く範囲にありません。",
                                      {"unreachable": [d.to_public() for d in seen]})
                    return Result(False, f"{label}が見つかりません。")
                break
            px, py = choose_slot(zone, [d for d in dets if d is not target])
            try:
                self.logger({"ev": "pick", "target": target.to_public(), "zone": zone.name, "place": [px, py]})
                self.ctrl.pick_and_place(target.x_mm, target.y_mm, px, py, r=None,
                                         z_place=zone.z_place, z_pick=self._z_pick(target.name))
            except OutOfWorkspace as e:
                self.stats["picks_fail"] += 1
                self.logger({"ev": "error", "type": "OutOfWorkspace", "msg": str(e)})
                return Result(False, "その位置は腕が届きません。", {"error": str(e)})
            except EmergencyStop:
                self.stats["picks_fail"] += 1
                self.logger({"ev": "error", "type": "EmergencyStop"})
                return Result(False, "停止しました。", {"error": "estop"})
            self.last_target = target
            self.stats["picks_ok"] += 1
            done += 1
            moved.append(target.to_public())
            if target.x_mm is not None:
                picked_at.append((target.x_mm, target.y_mm))
            if count != -1 or done >= 12:  # 「全部」でも 12 個で打ち切り（無限ループ防止）
                break
        dt = time.time() - t0
        where = self.cfg.zone_label(zone.name)
        msg = f"{label}を{where}に置きました。" if done == 1 else f"{label}を{done}個、{where}に置きました。"
        return Result(True, msg, {"moved": moved, "seconds": round(dt, 1)})

    def tidy_up(self) -> Result:
        """スタート台の外にある物を全部スタート台へ戻す（実演のリセット）。"""
        start = self.cfg.zone_by_name(self.cfg.demo.start_zone)
        if start is None:
            return Result(False, "スタート台（start ゾーン）が設定にありません。")
        r = self.pick_and_place(None, start.name, "any", count=-1)
        if r.ok:
            self.stats["tidy"] += 1
            n = len(r.data.get("moved", []))
            r = Result(True, "片付けました。" if n else "もう片付いています。", r.data)
        return r

    def go_home(self) -> Result:
        try:
            self.robot.home()
        except (OutOfWorkspace, EmergencyStop) as e:
            return Result(False, "ホームに戻れませんでした。", {"error": str(e)})
        return Result(True, "ホームに戻りました。")

    def stop(self) -> Result:
        self.robot.stop()
        return Result(True, "止まります。")

    def resume(self) -> Result:
        self.robot.clear_stop()
        return Result(True, "再開できます。")


class JsonlLogger:
    """展示中の記録（発話・意図・検出・動作・所要時間）。あとで「データが貯まる」話の素材になる。"""
    def __init__(self, log_dir: str | Path):
        Path(log_dir).mkdir(parents=True, exist_ok=True)
        self.path = Path(log_dir) / time.strftime("pnp_%Y%m%d_%H%M%S.jsonl")
        self.listeners: list = []     # パネル等が最新イベントを受け取る

    def __call__(self, ev: dict) -> None:
        ev = {"t": round(time.time(), 3), **ev}
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")
        for fn in self.listeners:
            try:
                fn(ev)
            except Exception:  # noqa: BLE001 — リスナーの失敗で実演を止めない
                pass
