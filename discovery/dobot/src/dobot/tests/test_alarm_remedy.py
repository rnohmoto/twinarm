"""アラームの対処文（2026-10-26）: 脱調は本体キーのホーミング、超速は速さ、範囲系は作業域の内側へ。"""
from __future__ import annotations

from dobot.jog import ALARM_NAMES, RANGE_ALARMS, alarm_remedy
from dobot.robot_magician import MotionAlarm


def test_lose_step_tells_the_operator_to_home_with_the_key():
    msg = alarm_remedy([0x51])
    assert "本体キー" in msg and "ホーミング" in msg and "停止解除" in msg
    assert "本体キー" in alarm_remedy([0x22, 0x53])          # 脱調が混ざれば脱調の対処が勝つ


def test_overspeed_and_range_alarms_have_their_own_remedy():
    assert "速さ" in alarm_remedy([0x30]) and "本体キー" not in alarm_remedy([0x30])
    assert "作業域の内側" in alarm_remedy([0x22]) and "作業域の内側" in alarm_remedy([0x12, 0x43])
    assert all(c in RANGE_ALARMS for c in (0x12, 0x22, 0x43))
    assert "停止解除" in alarm_remedy([]) and "停止解除" in alarm_remedy([0x03])


def test_motion_alarm_message_carries_name_code_and_remedy():
    e = MotionAlarm([0x50], "動作中")
    assert ALARM_NAMES[0x50] in str(e) and "0x50" in str(e) and "本体キー" in str(e) and e.codes == [0x50]
