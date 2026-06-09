from __future__ import annotations

from dataclasses import dataclass


# Stream Deck + XL: 9 columns x 4 rows (36 keys), index = row * 9 + col
#
# OpenPnP-style jog keypad:
#   R0: [HOME] [Y+] [Z+] [N→Cam]                  [LOCK]
#   R1: [PWR]  [X-] [PkXY] [X+] [PkZ]             [JOB]
#   R2:      [Y-]    [Z-] [Cam→N]                 [STEP]
#   R3: [TOOL] [C+] [PkC]  [C-]              [VAC1][VAC2]

HOME_KEY_INDEX = 0
LOCK_KEY_INDEX = 8
POWER_KEY_INDEX = 9
JOB_KEY_INDEX = 17
JOB_STEP_KEY_INDEX = 26
TOOL_TO_CAM_KEY_INDEX = 5
CAM_TO_TOOL_KEY_INDEX = 23
TOOL_KEY_INDEX = 27
VAC1_KEY_INDEX = 34
VAC2_KEY_INDEX = 35

# Stream Deck + XL rotary dials 0–3 (left to right) → machine axes.
# Dial 4 (second from right) → OpenPnP jog increment slider.
# Dial 5 (rightmost) → OpenPnP speed slider (%).
DIAL_AXES = ("X", "Y", "Z", "C")
JOG_INCREMENT_DIAL_INDEX = 4
SPEED_DIAL_INDEX = 5
SPEED_STEP_PCT = 1
# OpenPnP JogControlsPanel slider levels (mm, LengthUnit.Millimeters).
JOG_INCREMENT_STEPS_MM = (0.01, 0.1, 1.0, 10.0, 100.0)
DEFAULT_DIAL_STEP_SIZES_MM = (1.0, 0.1, 0.01)
DEFAULT_DIAL_XY_STEP_SIZES_MM = (10.0, 1.0, 0.1, 0.01)


@dataclass(frozen=True)
class KeyAction:
    label: str
    command: str | None = None
    accent: str = "#4a5568"
    kind: str = "command"  # "command" | "lock" | "icon" | "toggle" | "tool" | "job_icon"
    sublabel: str | None = None
    icon: str | None = None  # "home" | "power" | "job" | "job_step" | "center_camera" | "center_tool"


JOG_COLOR = "#4a5568"
PARK_COLOR = "#b36b00"
VAC_OFF_COLOR = "#4a5568"
VAC_ON_COLOR = "#1d4ed8"
# OpenPnP FlatDarkLaf jog panel button background
HOME_COLOR = "#4c5052"
TOOL_COLOR = "#2d4a6f"
JOB_COLOR = "#1e3a2f"
JOB_STEP_COLOR = "#3a351e"
POSITION_COLOR = "#3a4f5c"

KEY_ACTIONS: dict[int, KeyAction] = {
    # Row 0
    0: KeyAction("", "HOME", HOME_COLOR, kind="icon", icon="home"),
    2: KeyAction("Y+", "JOG Y 1", JOG_COLOR),
    4: KeyAction("Z+", "JOG Z 1", JOG_COLOR),
    TOOL_TO_CAM_KEY_INDEX: KeyAction(
        "", "MOVE_TOOL_TO_CAMERA", POSITION_COLOR, kind="icon", icon="center_tool"
    ),
    LOCK_KEY_INDEX: KeyAction("LOCK", kind="lock", accent="#8b1e1e"),
    # Row 1 — power + X cluster + park Z + job start/pause
    POWER_KEY_INDEX: KeyAction("", "TOGGLE_MACHINE", "#1e2430", kind="icon", icon="power"),
    JOB_KEY_INDEX: KeyAction("", "TOGGLE_JOB", JOB_COLOR, kind="job_icon", icon="job"),
    10: KeyAction("X-", "JOG X -1", JOG_COLOR),
    11: KeyAction("PARK", "PARK_XY", PARK_COLOR, sublabel="XY"),
    12: KeyAction("X+", "JOG X 1", JOG_COLOR),
    13: KeyAction("PARK", "PARK_Z", PARK_COLOR, sublabel="Z"),
    # Row 2 — Y/Z jog + job step
    20: KeyAction("Y-", "JOG Y -1", JOG_COLOR),
    22: KeyAction("Z-", "JOG Z -1", JOG_COLOR),
    CAM_TO_TOOL_KEY_INDEX: KeyAction(
        "", "MOVE_CAMERA_TO_TOOL", POSITION_COLOR, kind="icon", icon="center_camera"
    ),
    JOB_STEP_KEY_INDEX: KeyAction("", "STEP_JOB", JOB_STEP_COLOR, kind="job_icon", icon="job_step"),
    # Row 3 — tool selector + rotation
    TOOL_KEY_INDEX: KeyAction("TOOL", "CYCLE_TOOL", TOOL_COLOR, kind="tool"),
    28: KeyAction("C+", "JOG C 1", JOG_COLOR),
    29: KeyAction("PARK", "PARK_C", PARK_COLOR, sublabel="C"),
    30: KeyAction("C-", "JOG C -1", JOG_COLOR),
    # Row 3 — lower-right vacuum toggles (H1:VAC1, H1:VAC2)
    VAC1_KEY_INDEX: KeyAction("VAC1", "TOGGLE_ACTUATOR VAC1", VAC_OFF_COLOR, kind="toggle"),
    VAC2_KEY_INDEX: KeyAction("VAC2", "TOGGLE_ACTUATOR VAC2", VAC_OFF_COLOR, kind="toggle"),
}


def is_lock_key(key: int) -> bool:
    action = KEY_ACTIONS.get(key)
    return action is not None and action.kind == "lock"


def is_home_key(key: int) -> bool:
    return key == HOME_KEY_INDEX


def is_release_triggered_key(key: int) -> bool:
    """Fire once on key release to avoid double-taps while a motion is running."""
    if is_home_key(key) or is_tool_to_cam_key(key) or is_cam_to_tool_key(key):
        return True
    if is_job_key(key):
        return True
    action = KEY_ACTIONS.get(key)
    if action is None or action.command is None:
        return False
    return action.command.startswith("PARK")


def is_power_key(key: int) -> bool:
    return key == POWER_KEY_INDEX


def is_icon_key(key: int) -> bool:
    action = KEY_ACTIONS.get(key)
    return action is not None and action.kind == "icon"


def is_toggle_key(key: int) -> bool:
    action = KEY_ACTIONS.get(key)
    return action is not None and action.kind == "toggle"


def is_tool_key(key: int) -> bool:
    action = KEY_ACTIONS.get(key)
    return action is not None and action.kind == "tool"


def is_tool_to_cam_key(key: int) -> bool:
    return key == TOOL_TO_CAM_KEY_INDEX


def is_cam_to_tool_key(key: int) -> bool:
    return key == CAM_TO_TOOL_KEY_INDEX


def is_openpnp_target_action_enabled(
    key: int,
    machine_enabled: bool | None,
    selected_tool_kind: str | None,
    *,
    move_tool_to_camera_enabled: bool | None = None,
    move_camera_to_tool_enabled: bool | None = None,
) -> bool:
    """Match OpenPnP MachineControlsPanel.enableToolActions().

    targetToolAction (move tool to camera): machine on and camera selected.
    targetCameraAction (move camera to tool): machine on and non-camera selected.

    When the bridge reports action.isEnabled(), that value takes precedence.
    """
    if not is_tool_to_cam_key(key) and not is_cam_to_tool_key(key):
        return True
    if is_tool_to_cam_key(key) and move_tool_to_camera_enabled is not None:
        return move_tool_to_camera_enabled
    if is_cam_to_tool_key(key) and move_camera_to_tool_enabled is not None:
        return move_camera_to_tool_enabled
    if machine_enabled is not True or selected_tool_kind is None:
        return False
    is_camera = selected_tool_kind == "camera"
    if is_tool_to_cam_key(key):
        return is_camera
    return not is_camera


def is_job_key(key: int) -> bool:
    action = KEY_ACTIONS.get(key)
    return action is not None and action.kind == "job_icon"


def actuator_name_for_key(key: int) -> str | None:
    action = KEY_ACTIONS.get(key)
    if action is None or action.command is None:
        return None
    if action.command.startswith("TOGGLE_ACTUATOR "):
        return action.command.split()[1]
    return None


def is_motion_key(key: int) -> bool:
    action = KEY_ACTIONS.get(key)
    return (
        action is not None
        and action.kind in ("command", "icon", "toggle")
        and action.command != "TOGGLE_MACHINE"
    )


def is_lock_blocked_key(key: int) -> bool:
    return (
        is_motion_key(key)
        or is_power_key(key)
        or is_tool_key(key)
        or is_job_key(key)
    )


def is_key_allowed(
    key: int,
    machine_enabled: bool | None,
    machine_homed: bool | None,
    selected_tool_kind: str | None = None,
    *,
    move_tool_to_camera_enabled: bool | None = None,
    move_camera_to_tool_enabled: bool | None = None,
) -> bool:
    """Whether a key press should be sent to OpenPnP (lock is handled separately)."""
    if is_lock_key(key):
        return True
    if machine_enabled is False:
        return is_power_key(key)
    if machine_homed is False:
        return is_power_key(key) or is_home_key(key) or is_tool_key(key) or is_job_key(key)
    if not is_openpnp_target_action_enabled(
        key,
        machine_enabled,
        selected_tool_kind,
        move_tool_to_camera_enabled=move_tool_to_camera_enabled,
        move_camera_to_tool_enabled=move_camera_to_tool_enabled,
    ):
        return False
    return True


def is_key_disabled(
    key: int,
    locked: bool,
    machine_enabled: bool | None,
    machine_homed: bool | None,
    selected_tool_kind: str | None = None,
    *,
    bridge_connected: bool = True,
    move_tool_to_camera_enabled: bool | None = None,
    move_camera_to_tool_enabled: bool | None = None,
) -> bool:
    if is_lock_key(key):
        return locked and not bridge_connected
    if locked and is_lock_blocked_key(key):
        return True
    if machine_enabled is False and not is_power_key(key):
        return True
    if machine_enabled is False and is_job_key(key):
        return True
    if machine_enabled is not False and machine_homed is False:
        if (
            not is_power_key(key)
            and not is_home_key(key)
            and not is_tool_key(key)
            and not is_job_key(key)
        ):
            return True
    if not is_openpnp_target_action_enabled(
        key,
        machine_enabled,
        selected_tool_kind,
        move_tool_to_camera_enabled=move_tool_to_camera_enabled,
        move_camera_to_tool_enabled=move_camera_to_tool_enabled,
    ):
        return True
    return False


def build_command(action: KeyAction, jog_step_mm: float) -> str | None:
    if action.command is None:
        return None
    if action.command.startswith("JOG "):
        parts = action.command.split()
        axis = parts[1]
        direction = float(parts[2])
        delta = jog_step_mm if direction > 0 else -jog_step_mm
        return "JOG {0} {1}".format(axis, delta)
    return action.command


def iter_control_keys() -> list[int]:
    return sorted(KEY_ACTIONS.keys())