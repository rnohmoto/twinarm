"""置き場をカメラ画像の上で見て、クリックで決める（較正済みが前提。腕は動かない）。

setup_wizard 5 番の「吸盤を置き場に合わせて Enter」の代わり。assets/homography.json で画像⇔ロボット座標を
相互変換し、config.json の zones（置き場）を画像に丸で描く。数字キーで置き場を選んで画像をクリックすると、
そのピクセルに対応するロボット座標が新しい中心になる（置くときのスロットも作り直す）。s で config.json に
保存（config.bak_<時刻>.json を残す）。ロボットには接続しない。
z_place（置くときの高さ）はここでは決めない: ウィザード 5 番の最後の質問か config.json で。

キー: 1〜9=置き場を選ぶ / Tab=次の置き場 / クリック=選んだ置き場をそこへ / r=保存した状態に戻す /
      s=config.json に保存 / q=終了（未保存の変更があれば 2 回）
"""
from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from calibrate import PixelToRobot
from config import AppConfig, RobotConfig, Zone
from robot_dobot import WorkspaceGuard
from setup_wizard import place_zone, save_config_with_backup, zone_fits_workspace

# BGR。置き場ごとに色を変える（選択中は太線）
ZONE_COLORS = [(0, 220, 255), (0, 165, 255), (0, 200, 0), (255, 0, 255), (255, 200, 0), (200, 200, 200)]
OUTLINE_COLOR = (180, 180, 180)
TEXT_COLOR = (255, 255, 255)
LEGEND_LINE_PX = 22
KEY_TAB = 9
KEY_NONE = 255  # waitKey が -1 のとき & 0xFF


def _px(uv: tuple[float, float]) -> tuple[int, int]:
    """(u,v) を描画用の整数ピクセルに。"""
    return round(uv[0]), round(uv[1])


def zone_overlay(zone: Zone, p2r: PixelToRobot) -> tuple[tuple[int, int], int, list[tuple[int, int]]]:
    """置き場の中心・半径・スロットをピクセルで。半径は中心での mm/px で換算。"""
    u, v = p2r.to_pixel(zone.x, zone.y)
    r_px = zone.radius_mm / p2r.mm_per_px(u, v)
    slots = [_px(p2r.to_pixel(sx, sy)) for sx, sy in zone.slots]
    return _px((u, v)), round(r_px), slots


def workspace_outline(robot: RobotConfig, p2r: PixelToRobot) -> list[tuple[int, int]]:
    """腕の作業域（x_min..x_max × y_min..y_max）の四隅をピクセルで。届く距離の制限は別（click_to_zone で見る）。"""
    corners = [(robot.x_min, robot.y_min), (robot.x_max, robot.y_min),
               (robot.x_max, robot.y_max), (robot.x_min, robot.y_max)]
    return [_px(p2r.to_pixel(x, y)) for x, y in corners]


def click_to_zone(zones: Sequence[Zone], index: int, u: float, v: float, p2r: PixelToRobot,
                  robot: RobotConfig) -> tuple[list[Zone], bool, str]:
    """クリック位置を zones[index] の新しい中心にしたリストを返す。中心かスロットが腕の作業域の外なら元のまま（ok=False）。"""
    x, y = p2r.to_robot(u, v)
    zone = zones[index]
    z = zone.z_place if zone.z_place is not None else robot.z_place
    if not WorkspaceGuard(robot).inside(x, y, z):
        return list(zones), False, (f"{zone.name}: ({x:.0f},{y:.0f}) は腕の作業域の外です"
                                    f"（x {robot.x_min:.0f}〜{robot.x_max:.0f}, y {robot.y_min:.0f}〜{robot.y_max:.0f},"
                                    f" 届く距離 {robot.reach_min:.0f}〜{robot.reach_max:.0f} mm）")
    new = place_zone(zone, x, y)
    if not zone_fits_workspace(new, robot):
        return list(zones), False, (f"{zone.name}: 中心 ({x:.0f},{y:.0f}) は入りますが、置く位置（スロット）が"
                                    "腕の作業域の外に出ます。もう少し内側をクリックしてください")
    return [new if i == index else q for i, q in enumerate(zones)], True, \
        f"{zone.name} = ({new.x}, {new.y})  slots={len(new.slots)}"


def render(frame: np.ndarray, zones: Sequence[Zone], selected: int, p2r: PixelToRobot, robot: RobotConfig,
           dirty: bool) -> np.ndarray:
    """枠・置き場・凡例を描いた新しい画像を返す（frame は変更しない）。画面の文字は OpenCV の都合で英語。"""
    import cv2
    font = cv2.FONT_HERSHEY_SIMPLEX
    vis = frame.copy()
    outline = np.array(workspace_outline(robot, p2r), dtype=np.int32)
    cv2.polylines(vis, [outline], True, OUTLINE_COLOR, 1)
    cv2.putText(vis, "arm workspace", (int(outline[:, 0].min()) + 4, int(outline[:, 1].min()) + 16),
                font, 0.5, OUTLINE_COLOR, 1)
    for i, zone in enumerate(zones):
        color = ZONE_COLORS[i % len(ZONE_COLORS)]
        thick = 3 if i == selected else 1
        (u, v), r_px, slots = zone_overlay(zone, p2r)
        cv2.circle(vis, (u, v), r_px, color, thick)
        cv2.drawMarker(vis, (u, v), color, cv2.MARKER_CROSS, 16, thick)
        for s in slots:
            cv2.circle(vis, s, 3, color, -1)
        cv2.putText(vis, f"{i + 1} {zone.name}", (u + 10, v - 10), font, 0.6, color, 2)
    for i, zone in enumerate(zones):
        mark = ">" if i == selected else " "
        cv2.putText(vis, f"{mark}{i + 1} {zone.name} ({zone.x:.0f},{zone.y:.0f})",
                    (10, 24 + LEGEND_LINE_PX * i), font, 0.6, ZONE_COLORS[i % len(ZONE_COLORS)], 2)
    status = "UNSAVED (s=save)" if dirty else "saved"
    cv2.putText(vis, f"click = move '{zones[selected].name}' | 1-9/Tab select | r=revert s=save q=quit | {status}",
                (10, vis.shape[0] - 12), font, 0.55, TEXT_COLOR, 2)
    return vis


def run(cfg: AppConfig, config_path: Path, camera, p2r: PixelToRobot) -> None:
    import cv2
    zones: list[Zone] = list(cfg.zones)
    selected, dirty, quit_armed = 0, False, False
    clicks: list[tuple[float, float]] = []

    def on_mouse(event, x, y, flags, param):
        if event == cv2.EVENT_LBUTTONDOWN:
            clicks.append((float(x), float(y)))

    win = "zones (click = move selected zone, s = save, q = quit)"
    cv2.namedWindow(win)
    cv2.setMouseCallback(win, on_mouse)
    print("置き場:", ", ".join(f"{i + 1}={z.name}" for i, z in enumerate(zones)), "— 数字で選んで画像をクリック")
    while True:
        frame = camera.read()
        while clicks:
            u, v = clicks.pop(0)
            zones, ok, msg = click_to_zone(zones, selected, u, v, p2r, cfg.robot)
            dirty = dirty or ok
            quit_armed = False
            print(msg)
        cv2.imshow(win, render(frame, zones, selected, p2r, cfg.robot, dirty))
        k = cv2.waitKey(30) & 0xFF
        if k == KEY_NONE:
            continue
        if ord("1") <= k <= ord("9") and k - ord("1") < len(zones):
            selected = k - ord("1")
        elif k == KEY_TAB:
            selected = (selected + 1) % len(zones)
        elif k == ord("r"):
            zones, dirty = list(cfg.zones), False
            print("保存した状態に戻しました")
        elif k == ord("s"):
            cfg = replace(cfg, zones=zones)
            bak = save_config_with_backup(cfg, config_path)
            dirty = False
            print(f"保存しました: {config_path}" + (f"（元は {bak.name}）" if bak else ""))
        elif k == ord("q"):
            if dirty and not quit_armed:
                quit_armed = True
                print("未保存の変更があります: s で保存、捨てて終わるならもう一度 q")
                continue
            break
    cv2.destroyWindow(win)
    if dirty:
        print("未保存の変更は捨てました（config.json は変わっていません）")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="置き場をカメラ画像の上で見て、クリックで決める（腕は動かない）")
    ap.add_argument("--config", default=str(HERE / "config.json"))
    ap.add_argument("--image", default=None, help="カメラの代わりに静止画で（確認用）")
    args = ap.parse_args()
    config_path = Path(args.config)
    if not config_path.exists():
        raise SystemExit(f"{config_path} がありません")
    cfg = AppConfig.load(config_path)
    hp = Path(cfg.homography_path)
    hp = hp if hp.is_absolute() else HERE / hp
    if not hp.exists():
        raise SystemExit(f"較正がまだです: {hp} がありません。先に calibrate.py を実行してください")
    p2r = PixelToRobot.load(hp)
    from camera import FileCamera, make_camera
    if args.image:
        import cv2
        still = cv2.imread(args.image)
        if still is None:
            raise SystemExit(f"画像を読めません: {args.image}")
        cam = FileCamera(cfg.camera, still)
    else:
        cam = make_camera(cfg.camera)
    with cam:
        run(cfg, config_path, cam, p2r)
