"""Virtual Dynamixel bus: a stand-in for ``dynamixel_sdk`` with simulated Koch servos.

Put the parent folder first on ``PYTHONPATH`` and lerobot's ``DynamixelMotorsBus`` talks to
simulated servos instead of a serial port, so the koch4 scripts can be run end to end with
no arms attached (start-up, mode switching, the frame loop, shutdown):

    PYTHONPATH=koch4/simbus KOCH4_SIMBUS=<scenario.json> python koch4/koch4_teleop.py ...

This is a rehearsal of the *software path* only. It does not model forces, friction, heat
or timing of real servos, so nothing observed here says how the arms behave.

Scenario file (JSON)::

    {"ports": {"<port string given to the script>": {
         "kind": "leader" | "follower",        # servo models of a Koch leader / follower
         "calibration": "<lerobot calibration json>",
         "hand": true,                          # joints are moved by a (scripted) hand
         "eeprom_mismatch": false,              # EEPROM differs from the file -> lerobot asks
         "object": 0.5,                         # follower gripper cannot close below this (0-1)
         "drop_at_s": null, "drop_for_s": 1.0,  # bus dropout window (reconnect path)
         "hw_error_at_s": null                  # latch an overload error on the gripper
     }},
     "trace": "<jsonl path>"}                   # every register write, plus final dumps

What is modelled: the control table per servo model (addresses and lengths come from
lerobot's own tables), EEPROM write protection while torque is on (Access Error 0x07),
the reset of gains / Goal_PWM / Goal_Current when Operating_Mode is written, position
tracking of torque-on servos, a blocked follower gripper that draws current, a scripted
hand on leader joints, optional dropouts and a latched hardware error.
"""

import atexit
import json
import math
import os
import time
from pathlib import Path

COMM_SUCCESS = 0
COMM_PORT_BUSY = -1000
COMM_TX_FAIL = -1001
COMM_RX_FAIL = -1002
COMM_TX_ERROR = -2000
COMM_RX_WAITING = -3000
COMM_RX_TIMEOUT = -3001
COMM_RX_CORRUPT = -3002
COMM_NOT_AVAILABLE = -9000

ERR_ACCESS = 0x07  # write to the EEPROM area while torque is on, or unknown address
EEPROM_END = 64  # X-series: addresses below 64 are EEPROM, Torque_Enable is 64
MODE_CURRENT, MODE_POSITION, MODE_EXTENDED, MODE_CURRENT_POSITION, MODE_PWM = (
    0,
    3,
    4,
    5,
    16,
)
POSITION_MODES = (MODE_POSITION, MODE_EXTENDED, MODE_CURRENT_POSITION)
PWM_LIMIT = 885
CURRENT_LIMIT = 1750
DEFAULT_P_GAIN = {"xl330-m077": 400, "xl330-m288": 400, "xl430-w250": 800}
TRACK_TICKS_PER_S = 4000.0
BLOCKED_MA_PER_TICK = 4.0
SIGNED = {  # registers the servo interprets as two's complement
    "Homing_Offset": 4,
    "Goal_PWM": 2,
    "Goal_Current": 2,
    "Goal_Velocity": 4,
    "Goal_Position": 4,
    "Present_PWM": 2,
    "Present_Current": 2,
    "Present_Velocity": 4,
    "Present_Position": 4,
}
KOCH_MODELS = {
    "leader": dict.fromkeys(range(1, 7), "xl330-m077"),
    "follower": {
        1: "xl430-w250",
        2: "xl430-w250",
        3: "xl330-m288",
        4: "xl330-m288",
        5: "xl330-m288",
        6: "xl330-m288",
    },
}
JOINTS = {
    1: "shoulder_pan",
    2: "shoulder_lift",
    3: "elbow_flex",
    4: "wrist_flex",
    5: "wrist_roll",
    6: "gripper",
}
GRIPPER_ID = 6


def DXL_MAKEWORD(a, b):
    """Low byte and high byte to a word."""
    return (a & 0xFF) | ((b & 0xFF) << 8)


def DXL_MAKEDWORD(a, b):
    """Low word and high word to a double word."""
    return (a & 0xFFFF) | ((b & 0xFFFF) << 16)


def DXL_LOWORD(value):
    """Low word of a double word."""
    return value & 0xFFFF


def DXL_HIWORD(value):
    """High word of a double word."""
    return (value >> 16) & 0xFFFF


def DXL_LOBYTE(value):
    """Low byte of a word."""
    return value & 0xFF


def DXL_HIBYTE(value):
    """High byte of a word."""
    return (value >> 8) & 0xFF


def _signed(value, n_bytes):
    """Two's complement to a Python int."""
    bits = 8 * n_bytes
    value &= (1 << bits) - 1
    return value - (1 << bits) if value >= 1 << (bits - 1) else value


def _unsigned(value, n_bytes):
    """Python int to two's complement."""
    return int(value) & ((1 << (8 * n_bytes)) - 1)


_SCENARIO = {}


def _scenario():
    """Load the scenario named by KOCH4_SIMBUS (cached)."""
    if not _SCENARIO:
        path = os.environ.get("KOCH4_SIMBUS")
        if not path:
            raise RuntimeError(
                "simbus: KOCH4_SIMBUS is not set (path of the scenario json). "
                "This fake dynamixel_sdk must not be on PYTHONPATH for real hardware."
            )
        with open(path, encoding="utf-8") as f:
            _SCENARIO.update(json.load(f))
    return _SCENARIO


_ARMS = {}
_T0 = time.monotonic()


def _trace(record):
    """Append one record to the trace file, if the scenario names one."""
    path = _scenario().get("trace")
    if not path:
        return
    record = {"t": round(time.monotonic() - _T0, 3), "pid": os.getpid(), **record}
    with open(
        f"{path}.{os.getpid()}", "a", encoding="utf-8"
    ) as f:  # one file per process
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


class _Servo:
    """One simulated servo: a control table plus a little behaviour."""

    def __init__(self, arm, id_, model, cal, spec):
        from lerobot.motors.dynamixel.tables import (
            MODEL_CONTROL_TABLE,
            MODEL_NUMBER_TABLE,
        )

        self.arm, self.id, self.model = arm, id_, model
        self.name = JOINTS[id_]
        self.table = MODEL_CONTROL_TABLE[model]
        self.by_addr = {
            addr: (name, length) for name, (addr, length) in self.table.items()
        }
        self.model_number = MODEL_NUMBER_TABLE[model]
        self.reg = dict.fromkeys(self.table, 0)
        self.rmin, self.rmax = int(cal["range_min"]), int(cal["range_max"])
        mismatch = bool(spec.get("eeprom_mismatch"))
        self.reg.update(
            {
                "Model_Number": self.model_number,
                "ID": id_,
                "Return_Delay_Time": 250,
                "Drive_Mode": int(cal["drive_mode"]),
                "Operating_Mode": MODE_POSITION,
                "Homing_Offset": _unsigned(0 if mismatch else cal["homing_offset"], 4),
                "Min_Position_Limit": 0 if mismatch else self.rmin,
                "Max_Position_Limit": 4095 if mismatch else self.rmax,
                "PWM_Limit": PWM_LIMIT,
                "Current_Limit": CURRENT_LIMIT,
                "Goal_PWM": PWM_LIMIT,
                "Goal_Current": CURRENT_LIMIT,
                "Position_P_Gain": DEFAULT_P_GAIN[model],
                "Present_Temperature": 35,
            }
        )
        self.hand = bool(spec.get("hand"))
        self.pos = float(self.script(0.0) if self.hand else (self.rmin + self.rmax) / 2)
        self.reg["Goal_Position"] = _unsigned(round(self.pos), 4)
        self.current = 0.0
        self.block = None  # follower gripper: lowest tick an "object" lets it reach
        if self.id == GRIPPER_ID and not self.hand and spec.get("object") is not None:
            self.block = self.rmin + float(spec["object"]) * (self.rmax - self.rmin)
        self.applied = {"pwm": 0, "current": 0}  # outputs seen while torque was on

    def script(self, t):
        """Where a hand holds this joint at time t (raw ticks inside the calibrated range)."""
        span = self.rmax - self.rmin
        if self.id == GRIPPER_ID:  # open ... squeeze ... hold ... release, every 6 s
            open_tick, closed_tick = self.rmax, self.rmin + 0.15 * span
            if self.reg.get("Drive_Mode", 0) == 1:
                open_tick, closed_tick = self.rmin, self.rmax - 0.15 * span
            phase = t % 6.0
            if phase < 2.0:
                a = 0.0
            elif phase < 3.0:
                a = phase - 2.0
            elif phase < 5.0:
                a = 1.0
            else:
                a = 6.0 - phase
            return open_tick + a * (closed_tick - open_tick)
        mid = (self.rmin + self.rmax) / 2
        return mid + 0.2 * span * math.sin(2 * math.pi * t / (5.0 + self.id))

    def step(self, t, dt):
        """Advance the servo to time t."""
        mode = self.reg["Operating_Mode"]
        torque = self.reg["Torque_Enable"] == 1
        goal = _signed(self.reg["Goal_Position"], 4)
        self.current = 0.0
        if self.hand:
            self.pos = self.script(t)  # the hand wins against the servo
            if torque and mode == MODE_PWM:
                self.applied["pwm"] = _signed(self.reg["Goal_PWM"], 2)
            if torque and mode == MODE_CURRENT:
                self.current = float(_signed(self.reg["Goal_Current"], 2))
                self.applied["current"] = int(self.current)
            if torque and mode == MODE_CURRENT_POSITION:
                cap = abs(_signed(self.reg["Goal_Current"], 2))
                err = goal - self.pos
                self.current = math.copysign(
                    min(cap, BLOCKED_MA_PER_TICK * abs(err)), err
                )
        elif torque and mode in POSITION_MODES:
            step = TRACK_TICKS_PER_S * dt
            target = self.pos + max(-step, min(step, goal - self.pos))
            if self.block is not None and target < self.block:
                target = (
                    max(self.pos, self.block) if self.pos >= self.block else self.pos
                )
                cap = abs(_signed(self.reg["Goal_Current"], 2))
                err = goal - target
                self.current = math.copysign(
                    min(cap, BLOCKED_MA_PER_TICK * abs(err)), err
                )
            self.pos = target
        self.reg["Present_Position"] = _unsigned(round(self.pos), 4)
        self.reg["Present_Current"] = _unsigned(round(self.current), 2)
        at = self.arm.spec.get("hw_error_at_s")
        if at is not None and self.id == GRIPPER_ID and t >= float(at):
            self.reg["Hardware_Error_Status"] = 0x20  # overload, latched
            self.reg["Torque_Enable"] = 0

    def read(self, addr, length):
        """Return (value, error) for a register read."""
        hit = self.by_addr.get(addr)
        if hit is None or hit[1] != length:
            return 0, ERR_ACCESS
        return self.reg[hit[0]], 0

    def write(self, addr, length, value):
        """Apply a register write; returns the status error byte."""
        hit = self.by_addr.get(addr)
        if hit is None or hit[1] != length:
            return ERR_ACCESS
        name = hit[0]
        torque = self.reg["Torque_Enable"] == 1
        if addr < EEPROM_END and torque:
            _trace(
                {
                    "port": self.arm.tag,
                    "motor": self.name,
                    "reject": name,
                    "why": "torque on",
                }
            )
            return ERR_ACCESS
        if name == "Torque_Enable" and value == 1 and self.reg["Hardware_Error_Status"]:
            return ERR_ACCESS  # a latched error keeps the servo off until power cycle
        self.reg[name] = value
        if name == "Operating_Mode":  # the servo resets these when the mode changes
            self.reg["Position_P_Gain"] = DEFAULT_P_GAIN[self.model]
            self.reg["Goal_PWM"] = self.reg["PWM_Limit"]
            self.reg["Goal_Current"] = self.reg["Current_Limit"]
        if name == "Torque_Enable" and value == 1:
            self.reg["Goal_Position"] = _unsigned(
                round(self.pos), 4
            )  # servo holds where it is
        if name != "Goal_Position":
            n = SIGNED.get(name)
            shown = _signed(value, n) if n else value
            _trace(
                {"port": self.arm.tag, "motor": self.name, "reg": name, "value": shown}
            )
        else:
            self.arm.goal_writes += 1
        return 0


class _Arm:
    """Six simulated servos behind one port."""

    def __init__(self, port, spec):
        self.port, self.spec = port, spec
        self.tag = spec.get("tag") or Path(str(port)).name
        with open(spec["calibration"], encoding="utf-8") as f:
            cal = json.load(f)
        by_id = {int(c["id"]): c for c in cal.values()}
        self.servos = {
            id_: _Servo(self, id_, model, by_id[id_], spec)
            for id_, model in KOCH_MODELS[spec["kind"]].items()
        }
        self.t_last = 0.0
        self.goal_writes = 0
        self.opened = 0
        atexit.register(self.dump, "exit")

    def now(self):
        """Seconds since the process started using the simulator."""
        return time.monotonic() - _T0

    def step(self):
        """Advance every servo to the present."""
        t = self.now()
        dt = max(0.0, t - self.t_last)
        self.t_last = t
        for servo in self.servos.values():
            servo.step(t, dt)

    def dropped(self):
        """True while the scenario's dropout window is open."""
        at = self.spec.get("drop_at_s")
        if at is None:
            return False
        return (
            float(at)
            <= self.now()
            < float(at) + float(self.spec.get("drop_for_s", 1.0))
        )

    def dump(self, why):
        """Write the final register state of every servo to the trace."""
        _trace(
            {
                "port": self.tag,
                "dump": why,
                "goal_position_writes": self.goal_writes,
                "servos": {
                    s.name: {
                        "mode": s.reg["Operating_Mode"],
                        "torque": s.reg["Torque_Enable"],
                        "goal_pwm": _signed(s.reg["Goal_PWM"], 2),
                        "goal_current": _signed(s.reg["Goal_Current"], 2),
                        "p_gain": s.reg["Position_P_Gain"],
                        "applied": s.applied,
                    }
                    for s in self.servos.values()
                },
            }
        )


def _arm(port):
    """The simulated arm behind a port (created on first use)."""
    key = str(port)
    if key not in _ARMS:
        ports = _scenario().get("ports", {})
        if key not in ports:
            raise OSError(f"simbus: no simulated arm for port {key!r}")
        _ARMS[key] = _Arm(key, ports[key])
    return _ARMS[key]


class PortHandler:
    """Stand-in for the SDK's serial port handle."""

    def __init__(self, port_name):
        self.port_name = port_name
        self.is_open = False
        self.is_using = False
        self.baudrate = 1_000_000
        self.packet_timeout = 0.0

    def openPort(self):
        """Attach to the simulated arm of this port."""
        arm = _arm(self.port_name)
        arm.opened += 1
        self.is_open = True
        _trace({"port": arm.tag, "event": "open"})
        return True

    def closePort(self):
        """Detach; the final register state goes to the trace."""
        if self.is_open:
            _arm(self.port_name).dump("close")
        self.is_open = False

    def clearPort(self):
        """Nothing is buffered."""

    def setBaudRate(self, baudrate):
        """Remember the baud rate."""
        self.baudrate = int(baudrate)
        return True

    def getBaudRate(self):
        """Return the baud rate last set."""
        return self.baudrate

    def setPacketTimeoutMillis(self, msec):
        """Remember the timeout."""
        self.packet_timeout = float(msec)

    def setPacketTimeout(self, packet_length):
        """Unused: timeouts never occur unless the scenario drops the bus."""

    def isPacketTimeout(self):
        """No packet ever times out here."""
        return False


class PacketHandler:
    """Stand-in for the SDK's protocol 2.0 packet handler."""

    def __init__(self, protocol_version=2.0):
        self.protocol_version = protocol_version

    def getProtocolVersion(self):
        """Return the protocol version given at construction."""
        return self.protocol_version

    def getTxRxResult(self, result):
        """Describe a communication result."""
        return {
            COMM_SUCCESS: "[TxRxResult] Communication success!",
            COMM_RX_TIMEOUT: "[TxRxResult] There is no status packet!",
            COMM_TX_FAIL: "[TxRxResult] Failed transmit instruction packet!",
        }.get(result, f"[TxRxResult] simbus result {result}")

    def getRxPacketError(self, error):
        """Describe a status-packet error byte."""
        if error & 0x7F == ERR_ACCESS:
            return "[RxPacketError] Access error! (EEPROM write with torque on, or bad address)"
        return f"[RxPacketError] simbus error {error}" if error else ""

    def _servo(self, port, dxl_id):
        arm = _arm(port.port_name)
        if not port.is_open or arm.dropped():
            return None
        arm.step()
        return arm.servos.get(dxl_id)

    def ping(self, port, dxl_id):
        """Return (model_number, result, error)."""
        servo = self._servo(port, dxl_id)
        if servo is None:
            return 0, COMM_RX_TIMEOUT, 0
        return servo.model_number, COMM_SUCCESS, 0

    def broadcastPing(self, port):
        """Return ({id: [model_number, firmware]}, result)."""
        arm = _arm(port.port_name)
        if not port.is_open or arm.dropped():
            return {}, COMM_RX_TIMEOUT
        return {i: [s.model_number, 52] for i, s in arm.servos.items()}, COMM_SUCCESS

    def _read(self, port, dxl_id, address, length):
        servo = self._servo(port, dxl_id)
        if servo is None:
            return 0, COMM_RX_TIMEOUT, 0
        value, error = servo.read(address, length)
        return value, COMM_SUCCESS, error

    def read1ByteTxRx(self, port, dxl_id, address):
        """Read one byte."""
        return self._read(port, dxl_id, address, 1)

    def read2ByteTxRx(self, port, dxl_id, address):
        """Read two bytes."""
        return self._read(port, dxl_id, address, 2)

    def read4ByteTxRx(self, port, dxl_id, address):
        """Read four bytes."""
        return self._read(port, dxl_id, address, 4)

    def writeTxRx(self, port, dxl_id, address, length, data):
        """Write ``length`` bytes given as a list; returns (result, error)."""
        servo = self._servo(port, dxl_id)
        if servo is None:
            return COMM_RX_TIMEOUT, 0
        value = sum((b & 0xFF) << (8 * i) for i, b in enumerate(data[:length]))
        return COMM_SUCCESS, servo.write(address, length, value)

    def write1ByteTxRx(self, port, dxl_id, address, data):
        """Write one byte."""
        return self.writeTxRx(port, dxl_id, address, 1, [data])

    def write2ByteTxRx(self, port, dxl_id, address, data):
        """Write two bytes."""
        return self.writeTxRx(
            port, dxl_id, address, 2, [DXL_LOBYTE(data), DXL_HIBYTE(data)]
        )

    def write4ByteTxRx(self, port, dxl_id, address, data):
        """Write four bytes."""
        low, high = DXL_LOWORD(data), DXL_HIWORD(data)
        chunks = [DXL_LOBYTE(low), DXL_HIBYTE(low), DXL_LOBYTE(high), DXL_HIBYTE(high)]
        return self.writeTxRx(port, dxl_id, address, 4, chunks)


class GroupSyncRead:
    """Stand-in for the SDK's sync read."""

    def __init__(self, port, ph, start_address, data_length):
        self.port, self.ph = port, ph
        self.start_address, self.data_length = start_address, data_length
        self.ids = []
        self.data = {}

    def addParam(self, dxl_id):
        """Queue one servo."""
        if dxl_id in self.ids:
            return False
        self.ids.append(dxl_id)
        return True

    def removeParam(self, dxl_id):
        """Drop one servo."""
        if dxl_id in self.ids:
            self.ids.remove(dxl_id)

    def clearParam(self):
        """Drop every servo."""
        self.ids, self.data = [], {}

    def txRxPacket(self):
        """Read the register from every queued servo."""
        arm = _arm(self.port.port_name)
        if not self.port.is_open or arm.dropped():
            return COMM_RX_TIMEOUT
        arm.step()
        self.data = {}
        for dxl_id in self.ids:
            servo = arm.servos.get(dxl_id)
            if servo is None:
                return COMM_RX_TIMEOUT
            value, error = servo.read(self.start_address, self.data_length)
            if error:
                return COMM_RX_CORRUPT
            self.data[dxl_id] = value
        return COMM_SUCCESS

    def isAvailable(self, dxl_id, address, data_length):
        """True when the last read returned this servo's register."""
        return dxl_id in self.data and address == self.start_address

    def getData(self, dxl_id, address, data_length):
        """Value read for one servo."""
        return self.data.get(dxl_id, 0)


class GroupSyncWrite:
    """Stand-in for the SDK's sync write."""

    def __init__(self, port, ph, start_address, data_length):
        self.port, self.ph = port, ph
        self.start_address, self.data_length = start_address, data_length
        self.params = {}

    def addParam(self, dxl_id, data):
        """Queue one servo's bytes."""
        if dxl_id in self.params:
            return False
        self.params[dxl_id] = list(data)
        return True

    def removeParam(self, dxl_id):
        """Drop one servo."""
        self.params.pop(dxl_id, None)

    def changeParam(self, dxl_id, data):
        """Replace one servo's bytes."""
        self.params[dxl_id] = list(data)
        return True

    def clearParam(self):
        """Drop every servo."""
        self.params = {}

    def txPacket(self):
        """Write the register on every queued servo (no status packets, as on the wire)."""
        arm = _arm(self.port.port_name)
        if not self.port.is_open or arm.dropped():
            return COMM_TX_FAIL
        arm.step()
        for dxl_id, data in self.params.items():
            servo = arm.servos.get(dxl_id)
            if servo is not None:
                value = sum(
                    (b & 0xFF) << (8 * i)
                    for i, b in enumerate(data[: self.data_length])
                )
                servo.write(self.start_address, self.data_length, value)
        return COMM_SUCCESS
