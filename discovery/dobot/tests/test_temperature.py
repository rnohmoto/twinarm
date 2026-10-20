"""温度センサ（Temp ピン＋サーミスタ）と熱の予算の温度判定のハード無しテスト。"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from config import AppConfig, RobotConfig
from demo import DemoRunner, ThermalGuard, parse_args
from robot_dobot import (
    ID_GET_IO_ADC,
    ID_SET_IO_MULTIPLEXING,
    DryRunRobot,
    PydobotRobot,
    thermistor_c,
)


def _adc_for(r_th: float, r_pullup=4700.0, v_pullup=3.3, fullscale=3.3) -> int:
    v = v_pullup * r_th / (r_pullup + r_th)
    return round(v / fullscale * 4095)


def test_thermistor_conversion_matches_beta_model():
    assert abs(thermistor_c(_adc_for(100000.0)) - 25.0) < 0.3          # R25 → 25 ℃
    import math
    r60 = 100000.0 * math.exp(3950.0 * (1 / 333.15 - 1 / 298.15))
    assert abs(thermistor_c(_adc_for(r60)) - 60.0) < 0.5
    assert thermistor_c(_adc_for(r60)) > thermistor_c(_adc_for(100000.0))   # 抵抗が下がる＝温度が上がる
    assert thermistor_c(0) is None and thermistor_c(4095) is None
    # 5V フルスケールの ADC なら同じ抵抗で読み値が小さくなるが、換算で同じ温度に戻る
    assert abs(thermistor_c(_adc_for(100000.0, fullscale=5.0), adc_fullscale_v=5.0) - 25.0) < 0.3


class _FakeDev:
    """pydobot.Dobot の _send_command だけを持つ偽物。送った Message を記録し、GetIOADC には応答を返す。"""
    def __init__(self, adc_value: int):
        self.sent = []
        self.adc_value = adc_value

    def _send_command(self, msg, wait=False):
        self.sent.append((msg.id, msg.ctrl, bytes(msg.params)))

        class R:
            pass
        r = R()
        if msg.id == ID_GET_IO_ADC:
            r.params = struct.pack("<BH", msg.params[0], self.adc_value)
        else:
            r.params = b""
        return r


def test_pydobot_io_commands_use_official_ids():
    rb = PydobotRobot(RobotConfig(backend="pydobot", port="COM9"))
    rb.dev = _FakeDev(adc_value=3680)
    rb.set_io_multiplexing(1, "adc")
    assert rb.read_adc(1) == 3680
    assert rb.dev.sent[0] == (ID_SET_IO_MULTIPLEXING, 1, bytes([1, 4]))   # 書き込み・EIO1・ADC=4
    assert rb.dev.sent[1] == (ID_GET_IO_ADC, 0, bytes([1]))               # 読み取り・EIO1
    assert "io_mux(1,adc)" in rb.log


def test_dry_robot_simulates_sensor():
    rb = DryRunRobot(RobotConfig(backend="dry"))
    rb.set_io_multiplexing(1, "adc")
    assert 24.0 < thermistor_c(rb.read_adc(1)) < 27.0


def test_thermal_guard_temperature_hysteresis():
    g = ThermalGuard(240, 600, 120)
    g.set_temperature(55.0, 60.0, 50.0)
    assert g.can_run(0) and not g.temp_hot
    g.set_temperature(61.0, 60.0, 50.0)
    assert g.temp_hot and not g.can_run(0)
    g.set_temperature(55.0, 60.0, 50.0)       # まだ再開しない（ヒステリシス）
    assert g.temp_hot
    g.set_temperature(49.0, 60.0, 50.0)
    assert not g.temp_hot and g.can_run(0)
    g.set_temperature(None, 60.0, 50.0)       # センサ無しは状態を変えない
    assert not g.temp_hot


def test_demo_runner_reads_temperature_in_dry_run(tmp_path):
    cfg = AppConfig.default()
    cfg.log_dir = str(tmp_path / "logs")
    cfg.demo.temp_eio = 1
    cfg.save(tmp_path / "config.json")
    runner = DemoRunner(parse_args(["--dry-run", "--no-llm", "--no-panel", "--config", str(tmp_path / "config.json")]))
    try:
        t = runner.read_temperature()
        assert t is not None and 24.0 < t < 27.0
        snap = runner.state.snapshot()
        assert "温度" in snap["robot"] and snap["attract"]["temp_c"] == round(t, 1) and snap["attract"]["temp_hot"] is False
        runner.robot.sim_adc = 3300          # 熱い読み値（≈ 60 ℃ 超）
        runner.read_temperature()
        assert runner.guard.temp_hot and not runner.guard.can_run(__import__("time").time())
        r = runner.run_loop(2)
        assert not r.ok and "休憩" in r.message
    finally:
        runner.shutdown()
