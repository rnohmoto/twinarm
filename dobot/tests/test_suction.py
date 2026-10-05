"""吸引 ON/OFF（suction.py）のハード無しテスト: フレーム・デバイス・自動 OFF・パネル HTTP。"""
from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from suction import (
    ID_DEVICE_SN,
    ID_SUCTION_CUP,
    DrySuction,
    MagicianSuction,
    ProtocolError,
    SuctionPanel,
    SuctionWorker,
    build_frame,
    main,
    parse_frame,
    resolve_port,
)


class FakeSerial:
    """書かれたフレームを覚え、Magician と同じ形の応答を返す。"""

    def __init__(self, name: bytes = b"DT1426040100\x00"):
        self.written: list[bytes] = []
        self._rx = b""
        self._name = name
        self.suction = (0, 0)
        self.closed = False

    def write(self, data: bytes) -> None:
        self.written.append(bytes(data))
        cmd_id, ctrl, params = parse_frame(bytes(data))
        if cmd_id == ID_DEVICE_SN:
            self._rx += build_frame(cmd_id, 0, self._name)
        elif cmd_id == ID_SUCTION_CUP and ctrl & 1:
            self.suction = (params[0], params[1])
            self._rx += build_frame(cmd_id, ctrl)
        elif cmd_id == ID_SUCTION_CUP:
            self._rx += build_frame(cmd_id, 0, bytes(self.suction))

    def read(self, n: int = 1) -> bytes:
        out, self._rx = self._rx[:n], self._rx[n:]
        return out

    def reset_input_buffer(self) -> None:
        self._rx = b""

    def close(self) -> None:
        self.closed = True


def test_frame_checksum_makes_payload_sum_zero():
    # 公式プロトコル: checksum = payload（id, ctrl, params）の和の 2 の補数
    assert build_frame(0) == bytes.fromhex("aa aa 02 00 00 00")
    assert build_frame(1) == bytes.fromhex("aa aa 02 01 00 ff")
    assert build_frame(ID_SUCTION_CUP, 1, b"\x01\x01") == bytes.fromhex("aa aa 04 3e 01 01 01 bf")


def test_parse_frame_roundtrip_and_rejects_corruption():
    assert parse_frame(build_frame(62, 0, b"\x01\x00")) == (62, 0, b"\x01\x00")
    broken = bytearray(build_frame(62, 0, b"\x01\x00"))
    broken[-1] ^= 0xFF
    with pytest.raises(ProtocolError):
        parse_frame(bytes(broken))
    with pytest.raises(ProtocolError):
        parse_frame(b"\x00\x01\x02")


def test_magician_suction_sends_immediate_commands_and_reads_back():
    ser = FakeSerial()
    dev = MagicianSuction(ser)
    assert dev.device_sn() == "DT1426040100"
    assert dev.set(True) is True
    assert ser.suction == (1, 1)
    # ctrl=1: 書き込み・即時（キューに積まない）
    assert parse_frame(ser.written[-2])[:2] == (ID_SUCTION_CUP, 1)
    assert dev.set(False) is False
    assert ser.suction == (0, 0)
    dev.close()
    assert ser.closed


def test_magician_suction_reports_silence_as_protocol_error():
    ser = FakeSerial()
    ser.write = lambda data: None  # 応答しない機器
    with pytest.raises(ProtocolError):
        MagicianSuction(ser, timeout_s=0.05).state()


def test_resolve_port_never_guesses(tmp_path):
    cfg = tmp_path / "config.json"
    assert resolve_port("/dev/x", cfg) == "/dev/x"
    with pytest.raises(SystemExit):
        resolve_port(None, cfg)
    cfg.write_text(json.dumps({"robot": {"port": "/dev/from-config"}}), encoding="utf-8")
    assert resolve_port(None, cfg) == "/dev/from-config"


def test_worker_turns_off_after_max_on_and_on_stop():
    now = [100.0]
    dev = DrySuction()
    w = SuctionWorker(dev, max_on_s=30, clock=lambda: now[0])
    w.apply(True)
    assert w.snapshot()["on"] is True and w.snapshot()["remaining_s"] == 30
    now[0] += 31
    w.tick()
    snap = w.snapshot()
    assert snap["on"] is False and dev.on is False and "自動" in snap["note"]
    w.apply(True)
    w.shutdown()
    assert dev.on is False


def test_worker_keeps_error_visible():
    class Broken(DrySuction):
        def set(self, on: bool) -> bool:
            raise ProtocolError("no reply")

    w = SuctionWorker(Broken(), max_on_s=0)
    with pytest.raises(ProtocolError):
        w.apply(True)
    assert "no reply" in w.snapshot()["error"]


def test_panel_http_toggles_through_the_worker_thread():
    dev = DrySuction()
    worker = SuctionWorker(dev, max_on_s=0)
    worker.start()
    panel = SuctionPanel(worker, port=0)
    port = panel.start()
    base = f"http://127.0.0.1:{port}"

    def post(body: dict) -> dict:
        req = urllib.request.Request(base + "/suction", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read())

    try:
        assert "吸引" in urllib.request.urlopen(base + "/", timeout=5).read().decode("utf-8")
        assert post({"on": True})["on"] is True and dev.on is True
        assert json.loads(urllib.request.urlopen(base + "/status", timeout=5).read())["on"] is True
        assert post({"on": False})["on"] is False and dev.on is False
        with pytest.raises(urllib.error.HTTPError):
            post({"on": "yes"})
    finally:
        panel.stop()
        worker.shutdown()
    assert dev.on is False


def test_cli_dry_run_commands(capsys):
    assert main(["--dry", "on"]) == 0
    assert main(["--dry", "pulse", "--seconds", "0"]) == 0
    assert main(["--dry", "status"]) == 0
    assert "dry-run" in capsys.readouterr().out
