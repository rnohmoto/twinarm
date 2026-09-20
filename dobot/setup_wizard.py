"""初期設定ウィザード: Magician とカメラを繋いで、これ 1 本を通せば「拾って置く」まで動く状態にする。

  uv run python setup_wizard.py                 # 最初から（config.json を読み、無ければ既定を作る）
  uv run python setup_wizard.py --from 4        # 途中から（1 カメラ・2 ロボット・3 マーカー・4 較正・5 置き場・6 高さ・7 検出・8 試運転）

各ステップの結果は config.json（と assets/homography.json）に保存されるので、途中でやめても続きからできる。
腕を動かすのは、ステップ内で「y」と答えたときだけ。ポートは候補を見せるだけで自動では選ばない。
手で腕を動かすときは、前腕のロック解除ボタン（Unlock key）を押しながら動かす（Dobot Magician の手持ちティーチング）。
"""
from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass

from config import AppConfig

STEPS = ["カメラ", "ロボット", "マーカー印刷と配置", "キャリブレーション", "置き場の座標", "物の高さ", "検出の確認", "試運転"]


def grid_slots(cx: float, cy: float, dx: float = 50.0, dy: float = 40.0, nx: int = 2, ny: int = 3) -> list[list[float]]:
    """中心 (cx,cy) の周りに nx×ny のスロットを作る（片付け先・トレイ内の置き位置）。"""
    xs = [cx + (i - (nx - 1) / 2) * dx for i in range(nx)]
    ys = [cy + (j - (ny - 1) / 2) * dy for j in range(ny)]
    return [[round(x, 1), round(y, 1)] for x in xs for y in ys]


def summarize(cfg: AppConfig, homography_path: Path) -> str:
    lines = [f"カメラ: backend={cfg.camera.backend} index={cfg.camera.index} {cfg.camera.width}x{cfg.camera.height}"
             f" 手動露出={not cfg.camera.auto_exposure}",
             f"ロボット: backend={cfg.robot.backend} port={cfg.robot.port} EE={cfg.robot.end_effector}"
             f" z_safe={cfg.robot.z_safe} z_pick(既定)={cfg.robot.z_pick} z_place={cfg.robot.z_place}",
             f"キャリブ: {'あり' if homography_path.exists() else '未'} ({homography_path})",
             "置き場: " + ", ".join(f"{z.name}=({z.x:.0f},{z.y:.0f})" for z in cfg.zones),
             "物の高さ: " + ", ".join(f"{o.name}={o.z_pick if o.z_pick is not None else '既定'}" for o in cfg.objects)]
    return "\n".join(lines)


def ask(prompt: str, default: str = "") -> str:
    s = input(f"{prompt}{' [' + default + ']' if default else ''}: ").strip()
    return s or default


def yes(prompt: str) -> bool:
    return ask(prompt + " (y/N)", "N").lower().startswith("y")


class Wizard:
    def __init__(self, config_path: Path):
        self.config_path = config_path
        self.cfg = AppConfig.load(config_path) if config_path.exists() else AppConfig.default()
        hp = Path(self.cfg.homography_path)
        self.homography_path = hp if hp.is_absolute() else HERE / hp
        self.camera = None
        self.robot = None

    # ------------------------------------------------------------ persistence
    def save(self) -> None:
        if self.config_path.exists():
            bak = self.config_path.with_suffix(f".bak_{time.strftime('%Y%m%d_%H%M%S')}.json")
            shutil.copy(self.config_path, bak)
        self.cfg.save(self.config_path)
        print(f"  → 保存しました: {self.config_path}")

    # ---------------------------------------------------------------- devices
    def open_camera(self):
        if self.camera is None:
            from camera import make_camera
            self.cfg.camera.backend = "opencv"
            self.camera = make_camera(self.cfg.camera)
            self.camera.open()
        return self.camera

    def open_robot(self):
        if self.robot is None:
            from robot_dobot import make_robot
            self.cfg.robot.backend = "pydobot"
            self.robot = make_robot(self.cfg.robot)
            self.robot.connect()
        return self.robot

    def pose_xy(self) -> tuple[float, float, float]:
        x, y, z, _ = self.open_robot().pose()
        return x, y, z

    # ------------------------------------------------------------------ steps
    def step1_camera(self) -> None:
        from camera import list_opencv_cameras
        from check_camera import brightness_stats, judge
        print("\n[1/8] カメラ — 一覧を出します（他のアプリがカメラを掴んでいると出ません）")
        cams = list_opencv_cameras()
        for c in cams:
            print(f"  index {c['index']}: readable={c['readable']} {int(c['width'])}x{int(c['height'])}")
        idx = ask("使うカメラ番号", str(self.cfg.camera.index if cams else 0))
        self.cfg.camera.index = int(idx) if idx.isdigit() else idx
        self.cfg.camera.auto_exposure = False
        self.camera = None
        cam = self.open_camera()
        frames = [cam.read() for _ in range(15)]
        props = cam.actual_props()
        for line in judge(brightness_stats([f.mean(axis=2) for f in frames]), props, cam.auto_exposure_applied):
            print("  -", line)
        print("  プレビューを出します。カメラが真下を向き、作業域全体が入り、ArUco を置く四隅が見えるように調整。q で次へ")
        import cv2
        while True:
            f = cam.read()
            h, w = f.shape[:2]
            cv2.line(f, (w // 2, 0), (w // 2, h), (0, 255, 255), 1)
            cv2.line(f, (0, h // 2), (w, h // 2), (0, 255, 255), 1)
            cv2.rectangle(f, (int(w * .1), int(h * .1)), (int(w * .9), int(h * .9)), (0, 200, 0), 1)
            cv2.putText(f, "aim: workspace inside green box / q=next s=snapshot", (10, 24),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
            cv2.imshow("wizard: camera", f)
            k = cv2.waitKey(30) & 0xFF
            if k == ord("q"):
                break
            if k == ord("s"):
                out = HERE / "logs" / time.strftime("wizard_camera_%Y%m%d_%H%M%S.jpg")
                out.parent.mkdir(parents=True, exist_ok=True)
                cv2.imwrite(str(out), cam.read())
                print("  snapshot:", out)
        cv2.destroyAllWindows()
        self.save()

    def step2_robot(self) -> None:
        from robot_dobot import PydobotRobot
        print("\n[2/8] ロボット — 電源 ON → 本体キー 2 秒長押しでホーミング（LED 緑）を先に済ませてください")
        cands = PydobotRobot.list_candidate_ports()
        print("  候補ポート:", ", ".join(cands) if cands else "なし（CP210x ドライバ・ケーブル・電源を確認）")
        port = ask("使うポート（候補から選んで入力）", self.cfg.robot.port or (cands[0] if len(cands) == 1 else ""))
        if not port:
            raise SystemExit("ポートが無いので中断します")
        self.cfg.robot.port = port
        self.robot = None
        x, y, z = self.pose_xy()
        print(f"  接続 OK。現在位置 x={x:.1f} y={y:.1f} z={z:.1f}")
        if not yes("ホーミングは済んでいますか（LED 緑）"):
            print("  本体キーを 2 秒長押ししてから、もう一度このステップを実行してください（--from 2）")
            raise SystemExit(2)
        ee = ask("エンドエフェクタ suction/gripper", self.cfg.robot.end_effector)
        self.cfg.robot.end_effector = ee if ee in ("suction", "gripper") else "suction"
        if yes("ホーム姿勢へ動かして確認しますか（腕が動きます）"):
            self.open_robot().home()
            print("  ホームへ動きました")
        self.save()

    def step3_markers(self) -> None:
        from calibrate import detect_aruco_centers, make_aruco_sheet
        print("\n[3/8] マーカー — assets/markers に印刷用 PNG を出します。4 枚印刷して厚紙に貼り、机の四隅（腕が届く範囲の内側）へ")
        d = HERE / "assets" / "markers"
        if not d.exists():
            make_aruco_sheet(d)
        print(f"  印刷: {d} の id0〜id3（時計回りに配置）")
        cam = self.open_camera()
        print("  カメラで 4 枚とも見えたら q で次へ")
        import cv2
        while True:
            f = cam.read()
            centers = detect_aruco_centers(f)
            for i, (u, v) in centers.items():
                cv2.circle(f, (int(u), int(v)), 8, (0, 255, 255), 2)
                cv2.putText(f, f"id{i}", (int(u) + 10, int(v)), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
            cv2.putText(f, f"markers seen: {sorted(centers)}  q=next", (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
            cv2.imshow("wizard: markers", f)
            if (cv2.waitKey(30) & 0xFF) == ord("q"):
                break
        cv2.destroyAllWindows()

    def step4_calibrate(self) -> None:
        from calibrate import interactive_calibration
        print("\n[4/8] キャリブレーション — 前腕のロック解除ボタンを押しながら、吸盤の先端を各マーカーの中心に合わせて space")
        print("  4 枚で q。RMS ≦ 2mm なら合格。5mm を超えたら、マーカーの浮き・カメラのずれを疑ってやり直し")
        p2r = interactive_calibration(self.open_camera(), self.open_robot(), self.homography_path, use_aruco=True)
        print(f"  mm/px（中央）= {p2r.mm_per_px(self.cfg.camera.width / 2, self.cfg.camera.height / 2):.3f}")
        self.save()

    def step5_zones(self) -> None:
        print("\n[5/8] 置き場 — 各置き場の中心に吸盤の先端を合わせて Enter（s で飛ばす）。スタート台は片付け先")
        for z in self.cfg.zones:
            a = ask(f"  {z.name}（{z.aliases[0] if z.aliases else ''}）に合わせたら Enter / s=スキップ", "")
            if a.lower() == "s":
                continue
            x, y, zz = self.pose_xy()
            z.x, z.y = round(x, 1), round(y, 1)
            if z.slots:
                nx, ny = (2, 3) if z.name == "start" else (2, 2)
                z.slots = grid_slots(z.x, z.y, 45.0 if z.name == "start" else 30.0, 40.0 if z.name == "start" else 30.0, nx, ny)
            print(f"    {z.name} = ({z.x}, {z.y})  slots={len(z.slots)}")
        a = ask("  トレイの底に吸盤が触れる高さまで下げたら Enter（z_place を決める）/ s=スキップ", "")
        if a.lower() != "s":
            _, _, zz = self.pose_xy()
            self.cfg.robot.z_place = round(zz + 3.0, 1)
            print(f"    z_place = {self.cfg.robot.z_place}（底 +3mm）")
        self.save()

    def step6_heights(self) -> None:
        print("\n[6/8] 物の高さ — 各対象物を机に置き、その上面に吸盤を当てて Enter（s で飛ばす）。z_pick を物ごとに記録")
        for o in self.cfg.objects:
            a = ask(f"  {o.label}（{o.name}）の上面に当てたら Enter / s=スキップ", "")
            if a.lower() == "s":
                continue
            _, _, zz = self.pose_xy()
            o.z_pick = round(zz - 1.0, 1)   # 1mm 押し付け
            print(f"    {o.name}.z_pick = {o.z_pick}")
        a = ask("  退避高さ z_safe（物の一番高いところ +20mm 以上）", str(self.cfg.robot.z_safe))
        try:
            self.cfg.robot.z_safe = float(a)
        except ValueError:
            pass
        self.save()

    def step7_tune(self) -> None:
        from main import tune_loop
        print("\n[7/8] 検出 — 対象物を作業域に並べ、枠が全部に付き、余計な枠が出ないことを確認（q で次へ）")
        print("  出ない/誤検出なら照明と背景を直す。しきい値は config.json の objects[].ranges を編集して再実行")
        tune_loop(self.cfg, self.open_camera())

    def step8_smoke(self) -> None:
        from calibrate import PixelToRobot
        from planner import TaskExecutor
        from robot_dobot import DryRunRobot
        print("\n[8/8] 試運転 — まず記録だけ（腕は動かない）")
        p2r = PixelToRobot.load(self.homography_path)
        dry = DryRunRobot(self.cfg.robot)
        dry.connect()
        ex = TaskExecutor(self.cfg, self.open_camera(), dry, p2r)
        r = ex.list_objects()
        print("  見えている:", r.message)
        first = ex.last_dets[0].name if ex.last_dets else None
        zone = self.cfg.zones[0].name
        if first:
            r = ex.pick_and_place(first, zone)
            print("  記録:", r.message)
            print("  " + " / ".join(dry.log))
        if first and yes("実機で同じ 1 回を動かしますか（腕が動きます。手を退けて）"):
            ex_live = TaskExecutor(self.cfg, self.open_camera(), self.open_robot(), p2r)
            r = ex_live.pick_and_place(first, zone)
            print("  実機:", r.message)
            ex_live.go_home()
        self.save()
        print("\n=== 設定のまとめ ===")
        print(summarize(self.cfg, self.homography_path))
        print("\n次: uv run python demo.py --config config.json --robot pydobot --port", self.cfg.robot.port,
              "--camera-index", self.cfg.camera.index, "--no-llm   → http://127.0.0.1:%d" % self.cfg.demo.panel_port)

    def close(self) -> None:
        try:
            if self.robot is not None:
                self.robot.disconnect()
        finally:
            if self.camera is not None:
                self.camera.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Dobot Magician setup wizard")
    ap.add_argument("--config", default=str(HERE / "config.json"))
    ap.add_argument("--from", dest="start", type=int, default=1, help="このステップから（1〜8）")
    ap.add_argument("--only", type=int, default=None, help="このステップだけ")
    args = ap.parse_args(argv)
    w = Wizard(Path(args.config))
    steps = [w.step1_camera, w.step2_robot, w.step3_markers, w.step4_calibrate, w.step5_zones, w.step6_heights,
             w.step7_tune, w.step8_smoke]
    todo = [args.only] if args.only else list(range(args.start, 9))
    print("設定ウィザード —", " → ".join(f"{i}.{n}" for i, n in enumerate(STEPS, 1)))
    try:
        for i in todo:
            steps[i - 1]()
    finally:
        w.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
