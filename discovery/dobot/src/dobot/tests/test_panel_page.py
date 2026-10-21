"""パネルの画面構成（見せるための並び）と、dry-run が実機の較正ファイルを拾わないことのテスト。"""
from __future__ import annotations

import numpy as np

from dobot.calibrate import PixelToRobot
from dobot.config import AppConfig
from dobot.panel_web import PAGE_HTML


def _pos(marker: str) -> int:
    i = PAGE_HTML.find(marker)
    assert i >= 0, marker
    return i


def test_panel_page_is_one_column_in_show_order():
    # 2026-10-19: 見せるための画面。上から カメラ → マイク → 操作ボタン → 記録（ログ）。状態はヘッダーに。
    assert _pos('id="cam"') < _pos('id="ptt"') < _pos('id="say"') < _pos('id="ev"')
    assert _pos('id="state"') < _pos("<main")                     # 状態表示はヘッダー
    assert "grid-template-columns:2fr 1fr" not in PAGE_HTML        # 左右 2 段組はやめた
    for i in ("cam", "ptt", "heard", "proposal", "question", "barfill", "vmsg", "say", "attract",
              "state", "kv", "utt", "intent", "reply", "pick", "hw", "ev", "clock"):
        assert PAGE_HTML.count(f'id="{i}"') == 1, i               # JS が参照する id はそのまま


def test_dry_run_build_uses_the_synthetic_homography_even_if_a_real_one_exists(tmp_path):
    # 2026-10-16: 実機の assets/homography.json があると dry-run のテストが 5 件落ちた（合成画像のブロックが
    # 実機の較正で作業域の外へ写る）。合成カメラのときは合成ホモグラフィを使う。
    from dobot.demo import parse_args
    from dobot.main import build
    bogus = tmp_path / "homography.json"
    PixelToRobot(np.diag([10.0, 10.0, 1.0]), (1280, 720)).save(bogus)
    cfg = AppConfig.default()
    cfg.homography_path = str(bogus)
    cfg.log_dir = str(tmp_path / "logs")
    cfg.save(tmp_path / "config.json")
    _cfg, camera, robot, ex, _agent, _logger = build(parse_args(["--dry-run", "--no-llm", "--no-panel", "--keyboard",
                                                                 "--config", str(tmp_path / "config.json")]))
    try:
        x, y = ex.p2r.to_robot(640, 360)
        assert abs(x - 220) < 1e-6 and abs(y) < 1e-6              # 合成: 画像中央 = (220,0)
    finally:
        camera.close()
        robot.disconnect()
