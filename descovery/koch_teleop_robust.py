"""koch_teleop_robust.py — 通信リトライ+自動再接続付きテレオペ
使い方: lerobot-teleoperate と同じ引数をこのスクリプトに渡す
"""
import sys, time

# ① sync_read に自動リトライを注入(1回→10回)
from lerobot.motors.motors_bus import MotorsBus
_orig_sync_read = MotorsBus.sync_read
def _sync_read_retry(self, *args, **kwargs):
    kwargs['num_retry'] = max(int(kwargs.get('num_retry', 0) or 0), 10)
    return _orig_sync_read(self, *args, **kwargs)
MotorsBus.sync_read = _sync_read_retry

from lerobot.scripts.lerobot_teleoperate import main

# ② それでも通信断で落ちたら自動で再接続(最大20回)
n = 0
while True:
    try:
        main()
        break
    except KeyboardInterrupt:
        print("\n[robust] Ctrl+C で終了します")
        break
    except ConnectionError as e:
        n += 1
        if n > 20:
            print("[robust] 再接続上限に達しました。物理側(コネクタ)の点検が必要です")
            raise
        print(f"\n[robust] 通信断を検知: {e}\n[robust] 2秒後に再接続します… ({n}/20)")
        time.sleep(2)