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
from robot_dobot import (
    EmergencyStop,
    MotionError,
    OutOfWorkspace,
    PickPlaceController,
    RobotBase,
)


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


PLACE_MARGIN_MM = 8.0        # 置くときに、隣の物との間に空ける余裕
DEFAULT_ITEM_MM = 40.0       # 大きさが分からない物（較正前など）の仮の長辺
PLACE_SEARCH_STEP_MM = 10.0  # スロットも中心もふさがっているときに置き場の中を探す格子の間隔


def _is_clear(x: float, y: float, dets: list[Detection], item_half: float, margin: float) -> bool:
    for d in dets:
        if d.x_mm is None:
            continue
        need = item_half + (d.size_mm or DEFAULT_ITEM_MM) / 2 + margin
        if math.hypot(d.x_mm - x, d.y_mm - y) < need:
            return False
    return True


def _search_points(zone: Zone, step: float) -> list[tuple[float, float]]:
    """置き場の半径内の格子点を中心に近い順に。"""
    n = int(zone.radius_mm // step)
    pts = [(round(zone.x + i * step, 1), round(zone.y + j * step, 1))
           for i in range(-n, n + 1) for j in range(-n, n + 1) if math.hypot(i * step, j * step) <= zone.radius_mm]
    return sorted(pts, key=lambda q: math.hypot(q[0] - zone.x, q[1] - zone.y))


def choose_place(zone: Zone, dets: list[Detection], item_size_mm: float | None, guard=None, z: float = 0.0,
                 margin_mm: float = PLACE_MARGIN_MM) -> tuple[float, float] | None:
    """置く位置。見えている物（dets、ロボット座標と大きさ入り）の上に乗せない場所を、スロット → 中心 →
    置き場の半径内の格子（中心に近い順）の順に探す。guard を渡せば腕の作業域の外も除く。無ければ None。

    以前は「スロットから 25 mm 以内に物が無ければ可、全部ふさがれば中心」だったので、40 mm 超の木片を 30 mm
    間隔のスロットに重ねて置いたり、箱の中心の物の上に置いたりした（2026-10-19）。
    """
    item_half = (item_size_mm or DEFAULT_ITEM_MM) / 2
    cands: list[tuple[float, float]] = [(float(sx), float(sy)) for sx, sy in zone.slots]
    cands.append((zone.x, zone.y))
    cands.extend(_search_points(zone, PLACE_SEARCH_STEP_MM))
    seen: set[tuple[float, float]] = set()
    for x, y in cands:
        if (x, y) in seen:
            continue
        seen.add((x, y))
        if math.hypot(x - zone.x, y - zone.y) > zone.radius_mm:
            continue
        if guard is not None and not guard.inside(x, y, z):
            continue
        if _is_clear(x, y, dets, item_half, margin_mm):
            return x, y
    return None


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

    def detect(self, frame: np.ndarray) -> list[Detection]:
        """画像 1 枚から検出し、ロボット座標・大きさ・届くかを埋める。状態もログも触らないので表示スレッドからも呼べる。"""
        dets = detect_objects(frame, self.cfg.objects, self.cfg.min_area_px, self.cfg.max_area_px)
        if self.p2r is not None:
            for d in dets:
                d.x_mm, d.y_mm = self.p2r.to_robot(d.cx, d.cy)
                d.size_mm = max(d.bbox[2], d.bbox[3]) * self.p2r.mm_per_px(d.cx, d.cy)
        for d in dets:  # ロボット座標が作業範囲外の検出は「見えているが拾えない」として印を付ける
            d.extra["reachable"] = bool(d.x_mm is not None and
                                        self.robot.guard.inside(d.x_mm, d.y_mm, self._z_pick(d.name)))
        return dets

    def observe(self) -> list[Detection]:
        frame = self.camera.read()
        dets = self.detect(frame)
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
            others = [d for d in dets if d is not target]
            z_place = zone.z_place if zone.z_place is not None else self.cfg.robot.z_place
            place = choose_place(zone, others, target.size_mm, self.robot.guard, z_place)
            if place is None:   # 見えている物の上に乗せない。空きが無ければ拾いにも行かない
                self.logger({"ev": "zone_full", "zone": zone.name,
                             "in_zone": len([d for d in others if in_zone(d, zone)])})
                if done:
                    break
                return Result(False, f"{self.cfg.zone_label(zone.name)}に空きがありません。先に片付けてください。",
                              {"full": zone.name})
            px, py = place
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
            except MotionError as e:
                self.stats["picks_fail"] += 1
                self.logger({"ev": "error", "type": type(e).__name__, "msg": str(e)})
                return Result(False, f"ロボットが止まりました。{e}", {"error": str(e)})
            self.last_target = target
            self.stats["picks_ok"] += 1
            done += 1
            moved.append(target.to_public())
            if target.x_mm is not None:
                picked_at.append((target.x_mm, target.y_mm))
            if count == -1:
                if done >= 12:  # 「全部」でも 12 個で打ち切り（無限ループ防止）
                    break
            elif done >= count:
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

    def run_loop(self, cycles: int) -> Result:
        """「ループして」: 運ぶ→戻す を cycles 回。実体は demo.py（loop_handler）が持つ。"""
        handler = getattr(self, "loop_handler", None)
        if handler is None:
            return Result(False, "この画面ではループは使えません（demo.py で起動してください）。")
        return handler(int(cycles))

    def go_home(self) -> Result:
        try:
            self.robot.home()
        except (OutOfWorkspace, EmergencyStop, MotionError) as e:
            return Result(False, "ホームに戻れませんでした。", {"error": str(e)})
        return Result(True, "ホームに戻りました。")

    def round_trip(self) -> Result:
        """作業域内を 1 往復して home へ（試運転・非常停止の試験用）。"""
        try:
            self.robot.round_trip()
        except EmergencyStop as e:
            return Result(False, "往復の途中で止めました。", {"error": str(e)})
        except (OutOfWorkspace, MotionError) as e:
            return Result(False, "往復できませんでした。", {"error": str(e)})
        return Result(True, "往復してホームに戻りました。")

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
