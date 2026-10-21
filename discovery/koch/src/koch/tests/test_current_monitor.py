"""``koch-current``: the standalone current recorder (serial-exclusive, no teleop)."""

from datetime import datetime

import pytest

from koch import current_monitor, paths


@pytest.mark.unit
def test_default_csv_lands_under_work_csv_with_a_timestamp() -> None:
    path = current_monitor.default_csv_path(datetime(2026, 10, 20, 17, 30, 5))
    assert path == paths.WORK_DIR / "csv" / "currents_20261020_173005.csv"


@pytest.mark.unit
def test_parser_defaults_match_the_v0_tool() -> None:
    args = current_monitor.build_parser().parse_args(["/dev/p"])
    assert args.ids == "1,2,3,4,5,6" and args.xl430 == "1,2" and args.torque == "off"
    assert args.hz == 50 and args.csv is None and not args.no_plot


@pytest.mark.unit
def test_column_names_carry_the_arm_tag_joint_and_unit() -> None:
    arm = {"tag": "L", "xl430": {1, 2}}
    assert current_monitor.col_name(arm, 2) == "L_id2_shoulder_lift[%]"
    assert current_monitor.col_name({"tag": "", "xl430": set()}, 6) == "id6_gripper[mA]"
