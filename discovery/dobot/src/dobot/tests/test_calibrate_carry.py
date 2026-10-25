"""dobot-calibrate --carry（ブロック 1 個をロボットが持ち運ぶ較正）と --verify（確認）のオフラインテスト。

机の上のブロック 1 個を模擬する World が、腕の ee_on/ee_off と pose から物の位置を追い、カメラはその位置に
合成の青ブロックを描く。人の手は「最初の点で吸盤の真下に置く」の 1 回だけ。
"""
from __future__ import annotations

import math

import numpy as np

from dobot.calibrate import (
    CARRY_RESIDUAL_MAX_MM,
    Pair,
    PixelToRobot,
    carry_calibration,
    carry_points,
    drive_grid,
    fit_dropping_outliers,
    load_pairs,
    object_heights,
    pairs_sidecar,
    residual_mm,
    verify_calibration,
    well_conditioned,
)
from dobot.config import AppConfig
from dobot.detect import make_synthetic_frame
from dobot.robot_dobot import DryRunRobot


def _true_p2r() -> PixelToRobot:
    # 0.25 mm/px、画像中央 (640,360) = ロボット (220,0)、画像の右が -y（test_calibrate_drive と同じ真値）
    pairs = [Pair(u, v, 220 - (v - 360) * 0.25, -(u - 640) * 0.25)
             for u, v in [(200, 100), (1000, 100), (1000, 600), (200, 600)]]
    return PixelToRobot.fit(pairs, (1280, 720))


class World:
    """机の上のブロック 1 個。吸盤が物の 5 mm 以内で ON になれば持ち上がり、OFF でその場に置かれる。"""

    def __init__(self, truth: PixelToRobot, block_xy: tuple[float, float] | None = None, fail_picks: tuple[int, ...] = ()):
        self.truth = truth
        self.block = block_xy
        self.held = False
        self.picks = 0
        self.fail_picks = set(fail_picks)    # 何回目の吸着を失敗させるか（吸着ミスの模擬）
        self.robot: DryRunRobot | None = None

    def on_ee(self, on: bool) -> None:
        x, y, _z, _r = self.robot.pose()
        if on:
            self.picks += 1
            if self.picks in self.fail_picks or self.block is None:
                return
            if math.hypot(self.block[0] - x, self.block[1] - y) <= 5.0:
                self.held = True
        elif self.held:
            self.block = (x, y)
            self.held = False

    def read(self) -> np.ndarray:
        if self.held or self.block is None:
            return make_synthetic_frame()
        u, v = self.truth.to_pixel(*self.block)
        return make_synthetic_frame(blocks=[("blue", round(u), round(v), 40)])


class WorldRobot(DryRunRobot):
    def __init__(self, cfg, world: World):
        super().__init__(cfg)
        self.world = world
        world.robot = self
        self.moves: list[tuple[float, float, float]] = []

    def _move(self, x, y, z, r, wait, linear=True):
        super()._move(x, y, z, r, wait, linear)
        self.moves.append((x, y, z))

    def _ee(self, on):
        super()._ee(on)
        self.world.on_ee(on)


def _setup(fail_picks=()):
    cfg = AppConfig.default()
    cfg.robot.ee_settle_s = 0.0                                        # 吸着待ちの sleep はテストでは省く
    spec = next(o for o in cfg.objects if o.name == "blue_cube")
    truth = _true_p2r()
    world = World(truth, fail_picks=fail_picks)
    robot = WorldRobot(cfg.robot, world)
    robot.connect()
    return cfg, spec, truth, world, robot


def _human(world: World, prompts: list[str]):
    """最初の問い（吸盤の真下に置いて Enter）で、腕の真下にブロックを置く。それ以外は Enter だけ。"""
    def ask(q: str) -> str:
        prompts.append(q)
        if len(prompts) == 1:
            x, y, _z, _r = world.robot.pose()
            world.block = (x, y)
        return ""
    return ask


def test_carry_points_orders_center_first_and_quick_is_center_plus_corners():
    rc = AppConfig.default().robot
    full = carry_points(rc, "full")
    quick = carry_points(rc, "quick")
    assert len(full) == 9 and set(full) == set(drive_grid(rc))
    assert len(quick) == 5 and quick[4] == (225.0, 0.0) and full[4] == (225.0, 0.0)
    assert set(quick[:4]) == {(175.0, -125.0), (175.0, 125.0), (275.0, -125.0), (275.0, 125.0)}   # 四隅が先
    assert full[:5] == quick                                           # 9 点でも最初の 5 点は四隅＋中心
    assert set(full[5:]) == {(225.0, -125.0), (275.0, 0.0), (225.0, 125.0), (175.0, 0.0)}
    assert well_conditioned(full[:4]) and well_conditioned(quick) and well_conditioned(full)
    assert not well_conditioned([(225.0, 0.0), (175.0, -125.0), (225.0, -125.0), (275.0, -125.0)])   # 同じ辺の 3 点＋中心
    assert not well_conditioned([(225.0, 0.0), (175.0, -125.0), (275.0, 125.0), (275.0, -125.0)])    # 中心＋対角の 2 隅＋1 隅
    assert not well_conditioned(full[:3])


def test_object_heights_place_is_pick_plus_three_unless_given():
    rc = AppConfig.default().robot
    spec = next(o for o in AppConfig.default().objects if o.name == "blue_cube")
    spec.z_pick = -57.1
    assert object_heights(rc, spec) == (-57.1, -49.1, -54.1)          # ベンチの実測と同じ規則
    spec.z_place = -40.0
    assert object_heights(rc, spec)[2] == -40.0


def test_carry_calibration_needs_the_human_once_and_fits_the_truth(tmp_path):
    cfg, spec, truth, world, robot = _setup()
    out = tmp_path / "homography.json"
    prompts: list[str] = []
    said: list[str] = []
    p2r = carry_calibration(world, robot, cfg, spec, out, ask=_human(world, prompts), say=said.append)
    assert len(prompts) == 1 and "真下" in prompts[0]                 # 人が置くのは最初の 1 回だけ
    assert out.exists() and len(load_pairs(pairs_sidecar(out))) == 9
    for x, y in drive_grid(cfg.robot):                                 # 真値と 1 mm 以内
        px, py = p2r.to_robot(*truth.to_pixel(x, y))
        assert abs(px - x) < 1.0 and abs(py - y) < 1.0, (x, y, px, py)
    assert tuple(robot.pose()[:3]) == tuple(cfg.robot.home_xyzr[:3])   # 終わったらホーム
    last = carry_points(cfg.robot)[-1]
    assert not world.held and world.block == last                      # 物は最後の点に置いたまま
    assert world.picks == 8                                            # 9 点 → 運ぶ回数は 8
    z_pick, _, z_place = object_heights(cfg.robot, spec)
    assert (last[0], last[1], z_place) in robot.moves                  # 最後の点には置くだけで、拾い直さない
    assert (last[0], last[1], z_pick) not in robot.moves
    first = carry_points(cfg.robot)[0]
    assert (first[0], first[1], z_pick) in robot.moves and (first[0], first[1], z_pick + 8.0) in robot.moves
    assert all(z >= cfg.robot.z_min for _, _, z in robot.moves)
    assert any("合格" in s for s in said)


def test_carry_calibration_recovers_from_a_missed_pick_without_asking(tmp_path):
    cfg, spec, truth, world, robot = _setup(fail_picks=(2,))            # 2 回目の吸着（1 点目→2 点目へ運ぶとき）が空振り
    prompts: list[str] = []
    said: list[str] = []
    p2r = carry_calibration(world, robot, cfg, spec, tmp_path / "h.json", ask=_human(world, prompts), say=said.append)
    assert len(prompts) == 1                                           # 拾い直しは自動で、人には聞かない
    assert any("拾い直します" in s for s in said)
    assert len(load_pairs(pairs_sidecar(tmp_path / "h.json"))) == 9 and world.picks == 9
    for x, y in drive_grid(cfg.robot):
        px, py = p2r.to_robot(*truth.to_pixel(x, y))
        assert abs(px - x) < 1.0 and abs(py - y) < 1.0


def test_carry_calibration_asks_the_human_when_the_block_disappears(tmp_path):
    cfg, spec, truth, world, robot = _setup()
    prompts: list[str] = []

    def ask(q: str) -> str:
        prompts.append(q)
        x, y, _z, _r = world.robot.pose()
        if len(prompts) == 1:
            world.block = (x, y)
            return ""
        if len(prompts) == 2:                                          # 置き直しを頼まれた: 吸盤の真下に置く
            world.block = (x, y)
            world.held = False
            return ""
        return "q"

    real_on_ee = world.on_ee

    def on_ee(on: bool):                                               # 3 回目の吸着のあと、運搬中に落とした体（物が消える）
        real_on_ee(on)
        if on and world.picks == 3 and world.held:
            world.held = False
            world.block = None

    world.on_ee = on_ee
    p2r = carry_calibration(world, robot, cfg, spec, tmp_path / "h.json", ask=ask, say=lambda *a: None)
    assert len(prompts) == 2 and "見えません" in prompts[1]
    assert len(load_pairs(pairs_sidecar(tmp_path / "h.json"))) == 9
    for x, y in drive_grid(cfg.robot):
        px, py = p2r.to_robot(*truth.to_pixel(x, y))
        assert abs(px - x) < 1.0 and abs(py - y) < 1.0


def test_carry_calibration_quit_keeps_at_least_four_points(tmp_path):
    cfg, spec, truth, world, robot = _setup()
    prompts: list[str] = []

    def ask(q: str) -> str:
        prompts.append(q)
        x, y, _z, _r = world.robot.pose()
        if len(prompts) == 1:
            world.block = (x, y)
            return ""
        return "q"

    def on_ee(on: bool, real=world.on_ee):
        real(on)
        if on and world.picks == 5 and world.held:                     # 5 回目の運搬で落とす → 6 点目で人に聞く → q
            world.held = False
            world.block = None

    world.on_ee = on_ee
    p2r = carry_calibration(world, robot, cfg, spec, tmp_path / "h.json", ask=ask, say=lambda *a: None)
    assert len(prompts) == 2 and len(load_pairs(pairs_sidecar(tmp_path / "h.json"))) == 5
    assert p2r.rms_error_mm(load_pairs(pairs_sidecar(tmp_path / "h.json"))) < 0.5
    assert tuple(robot.pose()[:3]) == tuple(cfg.robot.home_xyzr[:3])


def test_fit_dropping_outliers_removes_a_bad_point_and_residual_flags_it():
    truth = _true_p2r()
    pts = carry_points(AppConfig.default().robot)
    pairs = [Pair(*truth.to_pixel(x, y), x, y) for x, y in pts]
    assert residual_mm(pairs[:4], truth.to_pixel(*pts[4]), pts[4]) < 0.5
    bad = Pair(*truth.to_pixel(260.0, 60.0), 230.0, 60.0)              # 30 mm ずれた点（落ちた物を見た体）
    assert residual_mm(pairs[:4], (bad.u, bad.v), (bad.x, bad.y)) > CARRY_RESIDUAL_MAX_MM
    p2r, kept, dropped = fit_dropping_outliers([*pairs, bad], (1280, 720))
    assert dropped == [bad] and len(kept) == 9 and p2r.rms_error_mm(kept) < 0.5
    # 一直線に近い 4 点（同じ辺の 3 点＋中心）では残差の検査をしない（当てはめが信用できない）
    line = [Pair(*truth.to_pixel(x, y), x, y) for x, y in [(225.0, 0.0), (175.0, -125.0), (225.0, -125.0), (275.0, -125.0)]]
    assert residual_mm(line, (bad.u, bad.v), (bad.x, bad.y)) == 0.0


def test_carry_calibration_skips_a_point_after_three_failed_placements(tmp_path):
    cfg, spec, truth, world, robot = _setup()
    prompts: list[str] = []

    def ask(q: str) -> str:                                            # 人は Enter を押すだけで、物を置かない
        prompts.append(q)
        x, y, _z, _r = world.robot.pose()
        if len(prompts) == 1:
            world.block = (x, y)
        return ""

    def on_ee(on: bool, real=world.on_ee):
        real(on)
        if on and world.picks == 6 and world.held:                     # 6 回目の運搬で落として物が消える
            world.held = False
            world.block = None

    world.on_ee = on_ee
    said: list[str] = []
    p2r = carry_calibration(world, robot, cfg, spec, tmp_path / "h.json", ask=ask, say=said.append)
    assert len(prompts) == 1 + 3 * 3                                   # 同じ点で 3 回聞いたら飛ばす（無限には聞かない）。残り 3 点
    assert any("飛ばします" in s for s in said) and any("飛ばした点" in s for s in said)
    kept = load_pairs(pairs_sidecar(tmp_path / "h.json"))
    assert len(kept) == 6 and p2r.rms_error_mm(kept) < 0.5             # 7 点目で消え、8・9 点目も物が無いので 6 点
    assert tuple(robot.pose()[:3]) == tuple(cfg.robot.home_xyzr[:3])


def test_verify_calibration_passes_with_the_true_homography_and_fails_with_a_shifted_one():
    cfg, spec, truth, world, robot = _setup()
    world.block = (250.0, 60.0)
    r = verify_calibration(world, robot, cfg, spec, truth, say=lambda *a: None)
    assert r["ok"] and r["error_mm"] < 1.0 and r["placed_at"] == [225.0, 0.0]
    assert abs(r["picked_at"][0] - 250.0) < 1.0 and abs(r["picked_at"][1] - 60.0) < 1.0
    assert tuple(robot.pose()[:3]) == tuple(cfg.robot.home_xyzr[:3]) and world.block == (225.0, 0.0)

    cfg, spec, truth, world, robot = _setup()
    world.block = (250.0, 60.0)
    shifted = PixelToRobot.fit([Pair(*truth.to_pixel(x, y), x + 20.0, y) for x, y in carry_points(cfg.robot)], (1280, 720))
    r = verify_calibration(world, robot, cfg, spec, shifted, say=lambda *a: None)
    assert not r["ok"] and "拾えません" in r["reason"] and world.block == (250.0, 60.0)

    cfg, spec, truth, world, robot = _setup()                           # 何も置いていない
    r = verify_calibration(world, robot, cfg, spec, truth, say=lambda *a: None)
    assert not r["ok"] and "見えません" in r["reason"]
