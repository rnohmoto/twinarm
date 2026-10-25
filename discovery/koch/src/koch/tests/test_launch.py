"""The launcher's pure helpers: the teleop command of a pair and the panel command."""

import pytest

from koch import launch

CFG = {
    "leader_port": "/dev/L",
    "follower_port": "/dev/F",
    "leader_serial": None,
    "follower_serial": None,
    "leader_id": "koch_leader_A",
    "follower_id": "koch_follower_A",
    "follower_host": None,
    "cams": "",
}


def _args(*argv):
    return launch.build_parser().parse_args(["--dry-run", *argv])


def _teleop(plan):
    return next(cmd for name, cmd, _ in plan if name == "A-teleop")


def _opt(cmd, flag):
    return cmd[cmd.index(flag) + 1]


@pytest.mark.unit
def test_a_wired_pair_drives_the_follower_from_the_leader(tmp_path) -> None:
    cmd = _teleop(launch.pair_commands("A", CFG, _args("--pair", "A"), tmp_path))
    assert _opt(cmd, "--leader-port") == "/dev/L"
    assert _opt(cmd, "--follower-port") == "/dev/F"
    assert _opt(cmd, "--leader-id") == "koch_leader_A"
    assert _opt(cmd, "--follower-id") == "koch_follower_A"
    assert "--leader-type" not in cmd and "--follower-type" not in cmd


@pytest.mark.unit
def test_swap_roles_exchanges_ports_and_ids_and_names_both_machines(tmp_path) -> None:
    plan = launch.pair_commands(
        "A", CFG, _args("--pair", "A", "--swap-roles"), tmp_path
    )
    cmd = _teleop(plan)
    assert _opt(cmd, "--leader-port") == "/dev/F"
    assert _opt(cmd, "--leader-id") == "koch_follower_A"
    assert _opt(cmd, "--leader-type") == "koch_follower"
    assert _opt(cmd, "--follower-port") == "/dev/L"
    assert _opt(cmd, "--follower-id") == "koch_leader_A"
    assert _opt(cmd, "--follower-type") == "koch_leader"
    assert _opt(cmd, "--ff") == "gripper"
    assert [name for name, _, _ in plan] == ["A-teleop"]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("cfg", "argv"),
    [
        (CFG, ["--pair", "A", "--swap-roles", "--vr", "A"]),
        ({**CFG, "follower_port": "none"}, ["--pair", "A", "--swap-roles"]),
        ({**CFG, "follower_host": "udp://h:9101"}, ["--pair", "A", "--swap-roles"]),
        (CFG, ["--pair", "A", "--swap-roles", "--follower", "none"]),
    ],
)
def test_swap_roles_is_refused_outside_a_wired_handshake(
    tmp_path, cfg, argv, capsys
) -> None:
    assert launch.pair_commands("A", cfg, _args(*argv), tmp_path) == []
    assert "--swap-roles" in capsys.readouterr().out


@pytest.mark.unit
def test_the_wired_backup_lets_a_wireless_config_swap(tmp_path) -> None:
    cfg = {**CFG, "follower_host": "udp://h:9101"}
    args = _args("--pair", "A", "--swap-roles", "--follower", "wired")
    cmd = _teleop(launch.pair_commands("A", cfg, args, tmp_path))
    assert _opt(cmd, "--leader-port") == "/dev/F"
    assert _opt(cmd, "--follower-port") == "/dev/L"


@pytest.mark.unit
def test_panel_labels_name_the_machines_only_when_swapped() -> None:
    config = {"pairs": {"A": CFG, "B": CFG}}
    plain = launch.panel_command(["A", "B"], config, _args())
    assert _opt(plain, "--labels") == "A,B"
    swapped = launch.panel_command(["A"], config, _args("--pair", "A", "--swap-roles"))
    label = _opt(swapped, "--labels")
    assert label.startswith("A") and "フォロワー機" in label and "リーダー機" in label
    assert "," not in label  # the panel splits its labels on commas


@pytest.mark.unit
def test_pair_label_is_the_bare_name_unless_swapped() -> None:
    assert launch.pair_label("B", False) == "B"
    assert launch.pair_label("A", True).startswith("A") and "入替" in launch.pair_label(
        "A", True
    )
