"""The Mac-side client of the wireless follower: ``koch-host`` driven over UDP.

``RemoteFollower`` looks like lerobot's KochFollower to the frame loop
(connect / disconnect / is_connected, send_action, and ``bus.sync_read/read/write``
for the few registers it uses). Joint targets go out with a sequence number; the
host's state messages come back on a daemon thread and are graded by ``koch.link``.
When no state has arrived for ``LinkPolicy.stale_after_s``, reads raise
``ConnectionError`` so the teleop's reconnect path takes over.
"""

import json
import socket
import threading
import time

from koch.link import LinkState, accept_packet, link_health, loss_ratio
from koch.motors import MOTORS

LINK_HELLO_TIMEOUT_SEC = 3.0
LINK_RX_TIMEOUT_SEC = 0.2


class RemoteBus:
    """The subset of MotorsBus the frame loop uses, answered from the host's last state."""

    def __init__(self, owner):
        self._owner = owner
        self.motors = list(MOTORS)

    def sync_read(self, data_name, motors=None, *, normalize=True, num_retry=0):
        """Positions, raw currents and error bits from the last state message."""
        state = self._owner.fresh_state()
        if data_name == "Present_Position":
            return {m: float(v) for m, v in state.get("fpos", {}).items()}
        if data_name == "Present_Current":
            return {m: int(v) for m, v in state.get("cur", {}).items()}
        if data_name == "Hardware_Error_Status":
            return {m: int(v) for m, v in (state.get("hw") or {}).items()}
        raise KeyError(data_name)

    def read(self, data_name, motor, *, normalize=True, num_retry=0):
        """Temperature, gripper cap and error bits of one motor."""
        state = self._owner.fresh_state()
        if data_name == "Present_Temperature":
            return int((state.get("temp") or {}).get(motor, 0))
        if data_name == "Goal_Current":
            return int(self._owner.grip_ma or 0)
        if data_name == "Hardware_Error_Status":
            return int((state.get("hw") or {}).get(motor, 0))
        raise KeyError(data_name)

    def write(self, data_name, motor, value, *, normalize=True, num_retry=0):
        """Only the gripper current cap can be written; it becomes a cfg message."""
        if data_name == "Goal_Current" and motor == "gripper":
            self._owner.grip_ma = int(value)
            self._owner.send_cfg(grip_ma=int(value))
            return
        raise KeyError(data_name)


class RemoteFollowerConfig:
    """`config.max_relative_target` that forwards changes to the host."""

    def __init__(self, owner, max_rel):
        self._owner = owner
        self._max_rel = max_rel

    @property
    def max_relative_target(self):
        """Current safety limiter (also applied on the host)."""
        return self._max_rel

    @max_relative_target.setter
    def max_relative_target(self, value):
        self._max_rel = value
        self._owner.send_cfg(max_rel=value)


class RemoteFollower:
    """A Koch follower driven by koch-host over UDP (wireless follower).

    The frame loop treats it like KochFollower: connect/disconnect/is_connected,
    send_action, and `bus.sync_read/read/write` for the few registers it uses.
    When no state has arrived for LinkPolicy.stale_after_s, reads raise
    ConnectionError so the existing reconnect path takes over.
    """

    def __init__(self, url, max_rel, grip_ma=None):
        host, _, port = url.removeprefix("udp://").partition(":")
        if not host or not port.isdigit():
            raise ValueError(f"--follower-port は udp://<host>:<port> の形: {url}")
        self.addr = (host, int(port))
        self.sock = None
        self.seq = 0
        self.state = {}
        self.link = LinkState()
        self.rtt_ms = 0.0
        self.grip_ma = grip_ma
        self.bus = RemoteBus(self)
        self.config = RemoteFollowerConfig(self, max_rel)
        self._lock = threading.Lock()

    @property
    def is_connected(self):
        """True while the socket is open and states keep arriving."""
        with self._lock:
            return (
                self.sock is not None
                and link_health(self.link, time.monotonic()) != "stale"
            )

    def connect(self):
        """Open the socket, greet the host and wait for its first state."""
        self.disconnect()
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(LINK_RX_TIMEOUT_SEC)
        with self._lock:
            self.sock = sock
            self.link = LinkState()
        threading.Thread(target=self._rx_loop, args=(sock,), daemon=True).start()
        deadline = time.monotonic() + LINK_HELLO_TIMEOUT_SEC
        while time.monotonic() < deadline:
            self._send({"type": "hello"})
            self.send_cfg(max_rel=self.config.max_relative_target, grip_ma=self.grip_ma)
            time.sleep(0.3)
            if self.is_connected:
                print(f"[link] フォロワー無線 接続 {self.addr[0]}:{self.addr[1]}")
                return
        raise ConnectionError(
            f"follower host {self.addr[0]}:{self.addr[1]} から状態が届きません"
        )

    def disconnect(self):
        """Close the socket (the host keeps holding the pose)."""
        with self._lock:
            sock, self.sock = self.sock, None
        if sock is not None:
            sock.close()

    def _rx_loop(self, sock):
        while True:
            with self._lock:
                if self.sock is not sock:
                    return
            try:
                data = sock.recv(65535)
            except TimeoutError:
                continue
            except OSError:
                return
            try:
                msg = json.loads(data.decode())
            except ValueError:
                continue
            if msg.get("type") != "state":
                continue
            now = time.monotonic()
            with self._lock:
                self.link, ok = accept_packet(self.link, int(msg.get("n", -1)), now)
                if ok:
                    self.state = msg
                    if msg.get("echo_t") is not None:
                        rtt = max(0.0, (now - float(msg["echo_t"])) * 1000)
                        self.rtt_ms = 0.8 * self.rtt_ms + 0.2 * rtt

    def fresh_state(self):
        """Last state, or ConnectionError when the link has gone stale."""
        with self._lock:
            state, link = self.state, self.link
        now = time.monotonic()
        if link_health(link, now) == "stale":
            age = now - link.last_rx if link.received else float("inf")
            raise ConnectionError(f"フォロワー無線が途絶({age:.1f}s)")
        return state

    def _send(self, msg):
        with self._lock:
            sock = self.sock
        if sock is not None:
            try:
                sock.sendto(json.dumps(msg).encode(), self.addr)
            except OSError:
                pass

    def send_action(self, action):
        """Ship the leader's goals to the host (the host clamps and writes them)."""
        self.seq += 1
        self._send(
            {
                "type": "action",
                "seq": self.seq,
                "t": round(time.monotonic(), 4),
                "action": {k: float(v) for k, v in action.items()},
            }
        )
        return action

    def send_cfg(self, **fields):
        """Forward max_rel / grip_ma to the host."""
        self._send(
            {"type": "cfg", **{k: v for k, v in fields.items() if v is not None}}
        )

    def link_stats(self):
        """Age / rtt / loss / health for telemetry and alerts."""
        with self._lock:
            link, rtt = self.link, self.rtt_ms
        now = time.monotonic()
        age = (now - link.last_rx) * 1000 if link.received else -1
        return {
            "age_ms": int(age),
            "rtt_ms": int(rtt),
            "loss": round(loss_ratio(link), 3),
            "health": link_health(link, now),
        }
