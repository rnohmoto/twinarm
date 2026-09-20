"""TaskExecutor: 「見る → 対象を選ぶ → 拾って置く」の実行層。LLM／ルール解析の両方がここを呼ぶ。

安全の要点: LLM もルール解析も「色名・ゾーン名・選び方ヒント」しか渡さない。
ロボット座標を作るのは、キャリブ済みホモグラフィ（検出→mm）と設定のゾーンだけ。
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from calibrate import PixelToRobot
from config import AppConfig
from detect import Detection, detect_colors
from robot_dobot import EmergencyStop, OutOfWorkspace, PickPlaceController, RobotBase


@dataclass
class Result:
    ok: bool
    message: str                      # 日本語の短い返事（TTS/パネルにそのまま出す）
    data: dict = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps({"ok": self.ok, "message": self.message, **self.data}, ensure_ascii=False)


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

    # ------------------------------------------------------------ perception
    def observe(self) -> list[Detection]:
        frame = self.camera.read()
        dets = detect_colors(frame, self.cfg.colors, self.cfg.min_area_px, self.cfg.max_area_px)
        if self.p2r is not None:
            for d in dets:
                d.x_mm, d.y_mm = self.p2r.to_robot(d.cx, d.cy)
        # ロボット座標が作業範囲外の検出は「見えているが拾えない」として印を付ける
        for d in dets:
            d.extra["reachable"] = bool(d.x_mm is not None and
                                        self.robot.guard.inside(d.x_mm, d.y_mm, self.cfg.robot.z_pick))
        self.last_frame, self.last_dets = frame, dets
        self.logger({"ev": "observe", "n": len(dets), "dets": [d.to_public() for d in dets]})
        return dets

    def list_objects(self) -> Result:
        dets = self.observe()
        if not dets:
            return Result(True, "今は何も見えません。", {"objects": []})
        counts: dict[str, int] = {}
        for d in dets:
            counts[d.color] = counts.get(d.color, 0) + 1
        jp = {"red": "赤", "green": "緑", "blue": "青", "yellow": "黄色"}
        summary = "、".join(f"{jp.get(k, k)}が{v}個" for k, v in counts.items())
        return Result(True, f"{summary}見えています。", {"objects": [d.to_public() for d in dets]})

    # -------------------------------------------------------------- selection
    def select(self, dets: list[Detection], color: str | None, hint: str = "any") -> Detection | None:
        cands = [d for d in dets if (color is None or d.color == color) and d.extra.get("reachable", True)]
        if not cands:
            return None
        if hint == "largest":
            return max(cands, key=lambda d: d.area)
        if hint == "smallest":
            return min(cands, key=lambda d: d.area)
        if hint == "nearest":  # ロボットに近い=x_mm が小さい（ベース原点に近い）
            key = (lambda d: d.x_mm if d.x_mm is not None else d.cy)
            return min(cands, key=key)
        if hint == "leftmost":  # 画像の左（来場者から見た左＝カメラの向きで合わせる。rotation_deg で調整）
            return min(cands, key=lambda d: d.cx)
        if hint == "rightmost":
            return max(cands, key=lambda d: d.cx)
        return cands[0]  # 面積最大（最も確からしい）

    # -------------------------------------------------------------- execution
    def pick_and_place(self, color: str | None, zone_name: str | None, hint: str = "any",
                       count: int = 1) -> Result:
        jp = {"red": "赤", "green": "緑", "blue": "青", "yellow": "黄色"}
        if self.p2r is None:
            return Result(False, "カメラとロボットの位置合わせ（キャリブレーション）がまだです。")
        zone = self.cfg.zone_by_name(zone_name) if zone_name else None
        if zone is None:
            names = "・".join(z.aliases[0] for z in self.cfg.zones if z.aliases)
            return Result(False, f"どこに置きますか？（{names}）", {"need": "zone"})
        done = 0
        moved: list[dict] = []
        t0 = time.time()
        while True:
            dets = self.observe()
            target = self.select(dets, color, hint)
            if target is None:
                if done == 0:
                    seen = [d for d in dets if color is None or d.color == color]
                    if seen:
                        return Result(False, f"{jp.get(color, color)}は見えていますが、腕が届く範囲にありません。",
                                      {"unreachable": [d.to_public() for d in seen]})
                    return Result(False, f"{jp.get(color, color) if color else '対象'}が見つかりません。")
                break
            try:
                self.logger({"ev": "pick", "target": target.to_public(), "zone": zone.name})
                self.ctrl.pick_and_place(target.x_mm, target.y_mm, zone.x, zone.y,
                                         r=None, z_place=zone.z_place)
            except OutOfWorkspace as e:
                self.logger({"ev": "error", "type": "OutOfWorkspace", "msg": str(e)})
                return Result(False, "その位置は腕が届きません。", {"error": str(e)})
            except EmergencyStop:
                self.logger({"ev": "error", "type": "EmergencyStop"})
                return Result(False, "停止しました。", {"error": "estop"})
            self.last_target = target
            done += 1
            moved.append(target.to_public())
            if count != -1 or done >= 12:  # 「全部」でも 12 個で打ち切り（無限ループ防止）
                if count != -1:
                    break
                if done >= 12:
                    break
        dt = time.time() - t0
        c = jp.get(color, color) if color else "それ"
        where = zone.aliases[0] if zone.aliases else zone.name
        msg = f"{c}を{where}に置きました。" if done == 1 else f"{c}を{done}個、{where}に置きました。"
        return Result(True, msg, {"moved": moved, "seconds": round(dt, 1)})

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

    def __call__(self, ev: dict) -> None:
        ev = {"t": round(time.time(), 3), **ev}
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")
