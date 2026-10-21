"""Raw Dynamixel SDK helpers shared by the bus tools (the SDK itself stays lazy)."""

import subprocess
import sys

import pytest

from koch import dxl
from koch.motors import MOTORS, to_signed16


@pytest.mark.unit
def test_register_addresses_and_bauds() -> None:
    assert (dxl.ADDR_ID, dxl.ADDR_TORQUE_ENABLE) == (7, 64)
    assert (dxl.ADDR_PRESENT_CURRENT, dxl.ADDR_PRESENT_POSITION) == (126, 132)
    assert dxl.BAUD == 1_000_000 and dxl.FACTORY_BAUD == 57_600


@pytest.mark.unit
def test_joint_ids_follow_the_bus_order() -> None:
    assert dxl.JOINTS == dict(enumerate(MOTORS, start=1))


@pytest.mark.unit
def test_model_names_fall_back_to_the_number() -> None:
    assert dxl.model_name(1190) == "XL330-M077"
    assert dxl.model_name(1060) == "XL430-W250"
    assert dxl.model_name(4242) == "model 4242"


@pytest.mark.unit
def test_sign_helpers() -> None:
    assert dxl.s32(1 << 31) == -(1 << 31)
    assert dxl.s32(5) == 5
    assert dxl.to_signed16 is to_signed16


@pytest.mark.integration
def test_importing_dxl_does_not_import_the_sdk() -> None:
    probe = "import sys, koch.dxl; print('dynamixel_sdk' in sys.modules)"
    out = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    )
    assert out.stdout.strip() == "False"


@pytest.mark.unit
def test_open_port_exits_with_the_path_when_the_port_cannot_be_opened(tmp_path) -> None:
    pytest.importorskip("dynamixel_sdk")
    with pytest.raises(SystemExit, match=str(tmp_path / "nope")):
        dxl.open_port(str(tmp_path / "nope"))
