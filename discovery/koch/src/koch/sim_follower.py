"""An in-process stand-in for lerobot's KochFollower, used by ``koch-host --sim``.

Positions follow the goals with a first-order lag, currents grow with the error,
the gripper current cap round-trips, temperatures and error bits are constant.
"""

from types import SimpleNamespace

from koch.motors import MOTORS


class SimBus:
    """Register reads/writes of the simulated follower."""

    def __init__(self, follower):
        self.follower = follower
        self.motors = list(MOTORS)

    def sync_read(self, data_name, motors=None, *, normalize=True, num_retry=0):
        """Positions follow the goals with a lag; currents grow with the error."""
        f = self.follower
        if data_name == "Present_Position":
            return dict(f.pos)
        if data_name == "Present_Current":
            return {m: int(abs(f.goal[m] - f.pos[m]) * 20) & 0xFFFF for m in MOTORS}
        if data_name == "Hardware_Error_Status":
            return dict.fromkeys(MOTORS, 0)
        raise KeyError(data_name)

    def read(self, data_name, motor, *, normalize=True, num_retry=0):
        """Temperature, gripper cap and error bits."""
        if data_name == "Present_Temperature":
            return 36
        if data_name == "Goal_Current":
            return self.follower.grip_ma
        if data_name == "Hardware_Error_Status":
            return 0
        raise KeyError(data_name)

    def write(self, data_name, motor, value, *, normalize=True, num_retry=0):
        """Only the gripper current cap is modelled."""
        if data_name == "Goal_Current" and motor == "gripper":
            self.follower.grip_ma = int(value)


class SimFollower:
    """A stand-in for lerobot's KochFollower (no hardware)."""

    def __init__(self, max_rel):
        self.pos = dict.fromkeys(MOTORS, 0.0)
        self.pos["gripper"] = 50.0
        self.goal = dict(self.pos)
        self.grip_ma = 0
        self.config = SimpleNamespace(max_relative_target=max_rel)
        self.bus = SimBus(self)
        self.is_connected = False

    def connect(self):
        """Pretend to open the bus."""
        self.is_connected = True

    def disconnect(self):
        """Pretend to close the bus."""
        self.is_connected = False

    def send_action(self, action):
        """Accept normalized goals (clamped like lerobot would)."""
        limit = self.config.max_relative_target
        for key, value in action.items():
            m = key.replace(".pos", "")
            if m in self.goal:
                target = float(value)
                if limit:
                    target = max(min(target, self.pos[m] + limit), self.pos[m] - limit)
                self.goal[m] = target
        return action

    def step(self, dt):
        """First-order lag toward the goals."""
        k = min(1.0, dt * 8.0)
        for m in MOTORS:
            self.pos[m] += (self.goal[m] - self.pos[m]) * k
