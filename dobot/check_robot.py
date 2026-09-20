"""Dobot Magician（初代・USB シリアル）の疎通確認。既定は「読むだけ」で、動かすのは明示フラグのときだけ。

使い方（このフォルダで）:
  uv run python check_robot.py --list                              # 候補ポートを列挙（読み取りのみ・選ばない）
  uv run python check_robot.py --port /dev/tty.usbserial-XXXX      # 接続して現在位置を読む（動かない）
  uv run python check_robot.py --port ... --home                   # ホーム姿勢（config の home_xyzr）へ動く
  uv run python check_robot.py --port ... --round-trip             # 作業域内を 1 往復（吸着/把持は使わない）
  uv run python check_robot.py --dry --round-trip                  # 実機なしのリハーサル（記録だけ）

前提（実機）:
  - 電源 ON → 本体キー長押し 2 秒でホーミング（LED 青点滅→緑）。電源投入のたびに必要
  - 動く前に作業域から手・物を退ける。止めるときは Ctrl+C か本体の電源スイッチ
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from config import AppConfig, RobotConfig
from robot_dobot import DryRunRobot, PydobotRobot, make_robot

HERE = Path(__file__).resolve().parent


def load_robot_config(path: Path, port: str | None, dry: bool) -> RobotConfig:
    cfg = AppConfig.load(path).robot if path.exists() else AppConfig.default().robot
    cfg.backend = "dry" if dry else "pydobot"
    if port:
        cfg.port = port
    return cfg


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Dobot Magician check (read-only unless a move flag is given)")
    ap.add_argument("--list", action="store_true", help="候補ポートを列挙して終了")
    ap.add_argument("--port", default=None, help="シリアルポート（Mac: /dev/tty.usbserial-*・Windows: COMn）")
    ap.add_argument("--config", default=str(HERE / "config.json"))
    ap.add_argument("--dry", action="store_true", help="実機なし（DryRunRobot）")
    ap.add_argument("--home", action="store_true", help="ホーム姿勢へ動く")
    ap.add_argument("--round-trip", action="store_true", help="作業域内を 1 往復して戻る（動く）")
    ap.add_argument("--adc", type=int, default=None, metavar="EIO", help="EIO ピンの ADC 生値を読む（読み取りのみ）")
    ap.add_argument("--temp", type=int, default=None, metavar="EIO",
                    help="EIO ピンのサーミスタを ℃ で読む（config の demo.temp_* で換算・読み取りのみ）")
    args = ap.parse_args(argv)

    if args.list:
        cands = PydobotRobot.list_candidate_ports()
        print("候補ポート:", ", ".join(cands) if cands else "なし（CP210x ドライバ・ケーブル・電源を確認）")
        print("→ 使うポートは --port で明示する（自動では選ばない）")
        return 0

    cfg = load_robot_config(Path(args.config), args.port, args.dry)
    rb = make_robot(cfg)
    rb.connect()
    try:
        if isinstance(rb, DryRunRobot):
            print("dry-run: 実機には繋いでいない")
        else:
            x, y, z, r = rb.pose()
            print(f"connected {cfg.port}  pose x={x:.1f} y={y:.1f} z={z:.1f} r={r:.1f}")
        eio = args.temp if args.temp is not None else args.adc
        if eio is not None:
            from robot_dobot import thermistor_c
            app = AppConfig.load(Path(args.config)) if Path(args.config).exists() else AppConfig.default()
            d = app.demo
            rb.set_io_multiplexing(eio, "adc")
            vals = [rb.read_adc(eio) for _ in range(5)]
            adc = sorted(vals)[len(vals) // 2]
            volts = d.temp_adc_fullscale_v * adc / 4095
            print(f"EIO{eio}: adc={adc} (5 回の中央値・生値 {vals})  ≈ {volts:.2f} V @ フルスケール {d.temp_adc_fullscale_v} V")
            print("  何も繋いでいなければ 3.3 V 相当（4095 付近＝3.3V フルスケール／2700 付近＝5V フルスケール）")
            if args.temp is not None:
                t = thermistor_c(adc, d.temp_r_pullup, d.temp_r25, d.temp_beta, 3.3, d.temp_adc_fullscale_v)
                print(f"  温度 ≈ {t:.1f} ℃" if t is not None else "  温度: 範囲外（センサ未接続か短絡）")
        if args.home:
            rb.home()
            print("home:", cfg.home_xyzr)
        if args.round_trip:
            x0, y0, z0, r0 = cfg.home_xyzr
            rb.home()
            rb.move_to(x0 + 30, y0 + 40, cfg.z_safe, r0)
            rb.move_to(x0 + 30, y0 - 40, cfg.z_safe, r0)
            rb.home()
            print("round-trip done (within workspace guard)")
    finally:
        rb.disconnect()
    print(json.dumps(rb.log, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
