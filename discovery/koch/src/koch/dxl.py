"""Raw Dynamixel SDK helpers for the bus tools: registers, models, ports.

The SDK is imported inside ``open_port`` so that importing this module (or any
tool built on it) never touches a serial port.
"""

from koch.motors import MOTORS, to_signed16

__all__ = [
    "ADDR_ID",
    "ADDR_PRESENT_CURRENT",
    "ADDR_PRESENT_POSITION",
    "ADDR_TORQUE_ENABLE",
    "BAUD",
    "FACTORY_BAUD",
    "JOINTS",
    "MODEL_NAMES",
    "PROTOCOL",
    "model_name",
    "open_port",
    "s32",
    "to_signed16",
]

BAUD = 1_000_000
FACTORY_BAUD = 57_600  # a motor fresh out of the box answers only here
PROTOCOL = 2.0
ADDR_ID = 7  # EEPROM
ADDR_TORQUE_ENABLE = 64
ADDR_PRESENT_CURRENT = 126  # XL330: 1 unit = 1 mA; XL430: Present_Load, 0.1 %/unit
ADDR_PRESENT_POSITION = 132
MODEL_NAMES = {1060: "XL430-W250", 1200: "XL330-M288", 1190: "XL330-M077"}
JOINTS = dict(enumerate(MOTORS, start=1))  # motor ID -> joint name


def model_name(number: int) -> str:
    """Human name of a model number, or ``model <n>`` for one we do not know."""
    return MODEL_NAMES.get(number, f"model {number}")


def s32(v: int) -> int:
    """Two's complement of a 32-bit register value."""
    return v - (1 << 32) if v >= (1 << 31) else v


def open_port(path: str, baud: int = BAUD):
    """Open a bus at ``baud`` and return ``(port, packet)``; exits with the path on failure."""
    from dynamixel_sdk import PacketHandler, PortHandler

    port = PortHandler(path)
    packet = PacketHandler(PROTOCOL)
    try:
        opened = port.openPort() and port.setBaudRate(baud)
    except OSError as e:  # pyserial raises on a missing or busy device
        raise SystemExit(f"ポートを開けません: {path} ({e})") from e
    if not opened:
        raise SystemExit(f"ポートを開けません: {path}")
    return port, packet
