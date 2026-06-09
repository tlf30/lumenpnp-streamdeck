# OpenPnP Stream Deck bridge - installed as ~/.openpnp2/scripts/Events/Startup.py
# Jython 2.7 compatible

from __future__ import absolute_import, print_function

import json
import math
import os
import socket
import threading
import traceback

from org.openpnp.model import Length, LengthUnit, Location
from org.openpnp.model.Motion import MotionOption
from javax.swing import SwingUtilities
from org.openpnp.util.UiUtils import submitUiMachineTask
from org.openpnp.util.MovableUtils import park, fireTargetedUserAction

BRIDGE_HOST = "127.0.0.1"
BRIDGE_PORT = 64738
BRIDGE_INFO_PATH = os.path.expanduser("~/.openpnp2/openpnp-bridge.json")
LOG_PATH = os.path.expanduser("~/.openpnp2/log/streamdeck-bridge.log")
DEFAULT_JOG_MM = 1.0
TRACKED_ACTUATORS = ("VAC1", "VAC2")


def _log(message):
    try:
        with open(LOG_PATH, "a") as handle:
            handle.write(message + "\n")
    except Exception:
        print("[streamdeck-bridge] " + message)


def _reply(connection, payload):
    if not isinstance(payload, basestring):
        payload = json.dumps(payload)
    try:
        connection.sendall(payload + "\n")
    except Exception:
        # Client disconnected before the response was sent (e.g. persistent socket reuse).
        pass


def _selected_tool():
    try:
        controls = gui.getMachineControls()
        tool = controls.getSelectedTool()
        if tool is not None:
            return tool
    except Exception:
        pass
    return machine.getDefaultHead().getDefaultNozzle()


def _tool_name(tool):
    if tool is None:
        return None
    try:
        return tool.getName()
    except TypeError:
        return tool.name


def _tool_java_class_name(tool):
    if tool is None:
        return ""
    try:
        return tool.getClass().getCanonicalName()
    except TypeError:
        return str(tool.getClass())


def _tool_kind(tool):
    if tool is None:
        return None
    class_name = _tool_java_class_name(tool)
    if class_name.endswith("Camera"):
        return "camera"
    if class_name.endswith("Nozzle"):
        return "nozzle"
    return "other"


def _tool_display_name(tool):
    if tool is None:
        return None
    if _tool_kind(tool) == "camera":
        return "CAM"
    return _tool_name(tool)


def _cycle_tool_targets():
    head = machine.getDefaultHead()
    targets = []
    for nozzle in head.getNozzles():
        targets.append(nozzle)
    for camera in head.getCameras():
        targets.append(camera)
    return targets


def _nozzle_tip_name(tool):
    if tool is None or _tool_kind(tool) != "nozzle":
        return None
    try:
        tip = tool.getNozzleTip()
    except Exception:
        return None
    if tip is None:
        return None
    return _tool_name(tip)


def _reflect_field(obj, field_name):
    field = obj.getClass().getDeclaredField(field_name)
    field.setAccessible(True)
    return field.get(obj)


def _get_machine_status_text():
    try:
        label = _reflect_field(gui, "lblStatus")
        text = label.getText()
        if text is None:
            return None
        text = text.strip()
        return text if text else None
    except Exception:
        return None


def _get_job_state():
    try:
        state = _reflect_field(gui.getJobTab(), "state")
        return str(state.name())
    except Exception:
        return "Stopped"


def _is_active_placement(placement, board_location):
    if str(placement.getType()) != "Placement":
        return False
    if not placement.isEnabled():
        return False
    if placement.getSide() != board_location.getGlobalSide():
        return False
    return True


def _count_placements():
    job = gui.getJobTab().getJob()
    total = 0
    completed = 0
    for board_location in job.getBoardLocations():
        if not board_location.isEnabled():
            continue
        board = board_location.getBoard()
        for placement in board.getPlacements():
            if not _is_active_placement(placement, board_location):
                continue
            total += 1
            if job.retrievePlacedStatus(board_location, placement.getId()):
                completed += 1
    remaining = max(total - completed, 0)
    return completed, total, remaining


def _nozzle_parts():
    parts = {}
    for head in machine.getHeads():
        for nozzle in head.getNozzles():
            name = _tool_name(nozzle)
            if not name:
                continue
            try:
                part = nozzle.getPart()
            except Exception:
                part = None
            if part is None:
                parts[name] = None
            else:
                parts[name] = part.getId()
    return parts


def _job_state_payload():
    completed, total, remaining = _count_placements()
    return {
        "machine_status": _get_machine_status_text(),
        "job_state": _get_job_state(),
        "placements_completed": completed,
        "placements_total": total,
        "placements_remaining": remaining,
        "nozzle_parts": _nozzle_parts(),
    }


def _target_action_state():
    controls = gui.getMachineControls()
    return {
        "move_tool_to_camera_enabled": controls.targetToolAction.isEnabled(),
        "move_camera_to_tool_enabled": controls.targetCameraAction.isEnabled(),
    }


def _selected_tool_state():
    tool = _selected_tool()
    payload = {
        "selected_tool": _tool_display_name(tool),
        "selected_tool_name": _tool_name(tool),
        "selected_tool_kind": _tool_kind(tool),
        "nozzle_tip": _nozzle_tip_name(tool),
    }
    try:
        payload.update(_target_action_state())
    except Exception:
        pass
    return payload


def _do_cycle_tool():
    controls = gui.getMachineControls()
    targets = _cycle_tool_targets()
    if not targets:
        raise Exception("No tools configured on the default head")
    current = controls.getSelectedTool()
    index = -1
    for i in range(len(targets)):
        if targets[i] == current:
            index = i
            break
    next_tool = targets[(index + 1) % len(targets)]
    controls.setSelectedTool(next_tool)
    return _selected_tool_state()


def _run_on_edt(task):
    if SwingUtilities.isEventDispatchThread():
        return task()
    result = [None, None]

    def runnable():
        try:
            result[0] = task()
        except Exception as error:
            result[1] = error

    SwingUtilities.invokeAndWait(runnable)
    if result[1] is not None:
        raise result[1]
    return result[0]


def _get_jog_controls_panel():
    return gui.getMachineControls().getJogControlsPanel()


def _get_jog_increment_mm():
    try:
        increment = _get_jog_controls_panel().getJogIncrement()
        return round(float(increment), 4)
    except Exception:
        return DEFAULT_JOG_MM


def _adjust_jog_increment(steps):
    panel = _get_jog_controls_panel()
    steps = int(steps)
    if steps > 0:
        for _ in range(steps):
            panel.raiseIncrementAction.actionPerformed(None)
    elif steps < 0:
        for _ in range(-steps):
            panel.lowerIncrementAction.actionPerformed(None)


def _get_speed_slider(panel):
    try:
        field = panel.getClass().getDeclaredField("speedSlider")
        field.setAccessible(True)
        return field.get(panel)
    except Exception:
        return None


def _adjust_machine_speed(steps):
    steps = int(steps)
    if steps == 0:
        return
    panel = _get_jog_controls_panel()
    try:
        old = int(round(float(panel.getSpeed()) * 100))
    except Exception:
        old = int(round(float(machine.getSpeed()) * 100))
    new = max(0, min(100, old + steps))
    try:
        min_speed = int(math.ceil(machine.getMotionPlanner().getMinimumSpeed() * 100))
    except Exception:
        min_speed = 1
    if new > 0 and new < min_speed:
        if steps < 0:
            new = 0
        else:
            new = min_speed
    machine.setSpeed(new * 0.01)
    slider = _get_speed_slider(panel)
    if slider is not None:
        slider.setValue(new)


def _get_machine_speed_pct():
    try:
        speed = gui.getMachineControls().getJogControlsPanel().getSpeed()
        return round(float(speed) * 100.0, 1)
    except Exception:
        try:
            return round(float(machine.getSpeed()) * 100.0, 1)
        except Exception:
            return None


def _find_actuator(name):
    # VAC1/VAC2 live on the head, not the machine-level actuator list.
    for actuator in machine.getAllActuators():
        if actuator.getName() == name:
            return actuator
    return None


def _get_actuator(name):
    actuator = _find_actuator(name)
    if actuator is not None:
        return actuator
    raise Exception("Actuator not found: " + name)


def _actuator_state(name):
    try:
        actuator = _find_actuator(name)
        if actuator is None:
            return None
        state = actuator.isActuated()
        if state is None:
            # enabled-actuation=AssumeUnknown resets to unknown on connect; treat as OFF.
            if machine.isEnabled():
                return False
            return None
        return bool(state)
    except Exception:
        return None


def _format_actuator_reading(raw):
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    try:
        number = float(text)
        if number == int(number):
            return str(int(number))
        return str(round(number, 1))
    except ValueError:
        return text


def _do_read_actuator(name):
    actuator = _get_actuator(name)
    return _format_actuator_reading(actuator.read())


def _get_position():
    try:
        tool = _selected_tool()
        location = tool.getLocation().convertToUnits(LengthUnit.Millimeters)
        actuators = {}
        for name in TRACKED_ACTUATORS:
            actuators[name] = _actuator_state(name)
        payload = {
            "ok": True,
            "x": round(location.getX(), 3),
            "y": round(location.getY(), 3),
            "z": round(location.getZ(), 3),
            "c": round(location.getRotation(), 3),
            "jog_step_mm": _get_jog_increment_mm(),
            "speed_pct": _get_machine_speed_pct(),
            "machine_enabled": machine.isEnabled(),
            "machine_homed": machine.isHomed(),
            "actuators": actuators,
        }
        payload.update(_selected_tool_state())
        payload.update(_job_state_payload())
        return payload
    except Exception as error:
        return {"ok": False, "error": str(error)}


def _do_home():
    machine.home()


def _do_toggle_machine():
    # Same code path as OpenPnP's machine connect/disconnect (power) button.
    gui.getMachineControls().startStopMachineAction.actionPerformed(None)


def _do_toggle_job():
    gui.getJobTab().startPauseResumeJobAction.actionPerformed(None)


def _do_step_job():
    gui.getJobTab().stepJobAction.actionPerformed(None)


def _do_move_camera_to_tool():
    gui.getMachineControls().targetCameraAction.actionPerformed(None)


def _do_move_tool_to_camera():
    gui.getMachineControls().targetToolAction.actionPerformed(None)


def _do_set_actuator(name, on):
    # Match OpenPnP ActuatorControlDialog: actuate inside a machine task only.
    # Do not import AbstractActuator (Jython cannot) or fireTargetedUserAction
    # (triggers unrelated safe-Z moves for head-mounted actuators).
    actuator = _get_actuator(name)
    # actuateObject avoids Jython overload ambiguity (boolean vs double vs string).
    actuator.actuateObject(True if on else False)


def _do_toggle_actuator(name):
    actuator = _get_actuator(name)
    current = actuator.isActuated()
    target = True
    if current is not None:
        target = not current
    _do_set_actuator(name, target)


def _do_park_xy():
    head = _selected_tool().getHead()
    if head is None:
        head = machine.getDefaultHead()
    park(head)
    fireTargetedUserAction(head.getDefaultHeadMountable())


def _do_park_z():
    hm = _selected_tool()
    if machine.isSafeZPark():
        hm.getHead().moveToSafeZ()
    location = hm.getLocation()
    safe_z = hm.getEffectiveSafeZ()
    location = location.deriveLengths(None, None, safe_z, None)
    hm.moveTo(location)
    fireTargetedUserAction(hm)


def _do_park_c():
    from org.openpnp.spi.Nozzle import RotationMode
    from org.openpnp.spi.base.AbstractNozzle import AbstractNozzle

    hm = _selected_tool()
    location = hm.getLocation()
    park_angle = 0.0

    if isinstance(hm, AbstractNozzle):
        nozzle = hm
        if nozzle.getRotationMode() == RotationMode.LimitedArticulation:
            if nozzle.getPart() is None:
                nozzle.setRotationModeOffset(None)
            limits = nozzle.getRotationModeLimits()
            park_angle = round((limits[0] + limits[1]) / 2.0 / 90.0) * 90.0
            if park_angle < limits[0] or park_angle > limits[1]:
                park_angle = (limits[1] + limits[0]) / 2.0

    location = location.derive(None, None, None, park_angle)
    hm.moveTo(location)
    fireTargetedUserAction(hm, True)


def _do_jog(axis, delta_mm):
    tool = _selected_tool()
    location = tool.getLocation().convertToUnits(LengthUnit.Millimeters)
    x = location.getX()
    y = location.getY()
    z = location.getZ()
    c = location.getRotation()

    axis = axis.upper()
    if axis == "X":
        x += delta_mm
    elif axis == "Y":
        y += delta_mm
    elif axis == "Z":
        z += delta_mm
    elif axis == "C":
        c += delta_mm
    else:
        raise Exception("Unknown axis: " + axis)

    target = Location(LengthUnit.Millimeters, x, y, z, c)
    gui.getMachineControls().checkJogMotionSafety(tool, target)
    tool.moveTo(target, MotionOption.JogMotion)
    fireTargetedUserAction(tool, True)


def _machine_error_message(error):
    message = str(error)
    if hasattr(error, "getCause") and error.getCause() is not None:
        message = str(error.getCause())
    return message


def _submit_machine_motion_task(task_fn, action, busy_timeout_ms=5000, task_timeout_ms=120000):
    """Run a blocking machine motion on the machine thread; reject if already busy."""
    if not machine.isEnabled():
        return {"ok": False, "error": "Machine is not connected — enable the machine first."}
    if machine.isBusy():
        return {
            "ok": False,
            "error": "Machine is busy — wait for the current operation to finish.",
        }

    def callable():
        _log("{0} started".format(action))
        task_fn()
        return True

    try:
        machine.execute(callable, True, busy_timeout_ms, task_timeout_ms)
    except Exception as error:
        _log("{0} failed: {1}".format(action, traceback.format_exc()))
        return {"ok": False, "error": _machine_error_message(error), "action": action}

    _log("{0} ok".format(action))
    return {"ok": True, "action": action}


def _submit_actuator_task(actuator_name, task_fn, action):
    if not machine.isEnabled():
        return {"ok": False, "error": "Machine is not connected — enable the machine first."}

    def callable():
        _log("actuator {0} {1} started".format(actuator_name, action))
        task_fn(actuator_name)
        return True

    # Run synchronously so actuator errors/timeouts reach the Stream Deck client.
    # VAC actuators coordinate with WaitForStillstand; if the planner never settles,
    # this times out instead of silently queueing forever.
    try:
        machine.execute(callable, True, 5000, 15000)
    except Exception as error:
        message = str(error)
        if hasattr(error, "getCause") and error.getCause() is not None:
            message = str(error.getCause())
        _log("actuator {0} {1} failed: {2}".format(
            actuator_name, action, traceback.format_exc()))
        return {"ok": False, "error": message, "actuator": actuator_name}

    _log("actuator {0} {1} ok".format(actuator_name, action))
    return {"ok": True, "action": action, "actuator": actuator_name}


def _submit_read_actuator_task(actuator_name):
    if not machine.isEnabled():
        return {"ok": False, "error": "Machine is not connected — enable the machine first."}

    def callable():
        _log("actuator {0} read started".format(actuator_name))
        return _do_read_actuator(actuator_name)

    try:
        value = machine.execute(callable, True, 5000, 15000)
    except Exception as error:
        message = str(error)
        if hasattr(error, "getCause") and error.getCause() is not None:
            message = str(error.getCause())
        _log("actuator {0} read failed: {1}".format(
            actuator_name, traceback.format_exc()))
        return {"ok": False, "error": message, "actuator": actuator_name}

    _log("actuator {0} read ok: {1}".format(actuator_name, value))
    return {"ok": True, "action": "read_actuator", "actuator": actuator_name, "value": value}


def _handle_command(line):
    parts = line.strip().split()
    if not parts:
        return {"ok": False, "error": "empty command"}

    command = parts[0].upper()

    if command == "PING":
        return {"ok": True}

    if command == "GET_POSITION":
        return _get_position()

    if command == "HOME":
        return _submit_machine_motion_task(_do_home, "home")

    if command == "TOGGLE_MACHINE":
        SwingUtilities.invokeLater(_do_toggle_machine)
        return {"ok": True, "action": "toggle_machine"}

    if command == "CYCLE_TOOL":
        try:
            state = _run_on_edt(_do_cycle_tool)
        except Exception as error:
            return {"ok": False, "error": str(error)}
        response = {"ok": True, "action": "cycle_tool"}
        response.update(state)
        return response

    if command == "TOGGLE_JOB":
        SwingUtilities.invokeLater(_do_toggle_job)
        return {"ok": True, "action": "toggle_job"}

    if command == "STEP_JOB":
        SwingUtilities.invokeLater(_do_step_job)
        return {"ok": True, "action": "step_job"}

    if command == "MOVE_CAMERA_TO_TOOL":
        SwingUtilities.invokeLater(_do_move_camera_to_tool)
        return {"ok": True, "action": "move_camera_to_tool"}

    if command == "MOVE_TOOL_TO_CAMERA":
        SwingUtilities.invokeLater(_do_move_tool_to_camera)
        return {"ok": True, "action": "move_tool_to_camera"}

    if command == "TOGGLE_ACTUATOR":
        if len(parts) != 2:
            return {"ok": False, "error": "usage: TOGGLE_ACTUATOR <name>"}
        return _submit_actuator_task(parts[1], _do_toggle_actuator, "toggle_actuator")

    if command == "SET_ACTUATOR":
        if len(parts) != 3:
            return {"ok": False, "error": "usage: SET_ACTUATOR <name> ON|OFF"}
        actuator_name = parts[1]
        state = parts[2].upper()
        if state not in ("ON", "OFF"):
            return {"ok": False, "error": "usage: SET_ACTUATOR <name> ON|OFF"}

        def task_fn(name):
            _do_set_actuator(name, state == "ON")

        return _submit_actuator_task(actuator_name, task_fn, "set_actuator")

    if command == "READ_ACTUATOR":
        if len(parts) != 2:
            return {"ok": False, "error": "usage: READ_ACTUATOR <name>"}
        return _submit_read_actuator_task(parts[1])

    if command in ("PARK", "PARK_XY"):
        return _submit_machine_motion_task(_do_park_xy, "park_xy")

    if command == "PARK_Z":
        return _submit_machine_motion_task(_do_park_z, "park_z")

    if command == "PARK_C":
        return _submit_machine_motion_task(_do_park_c, "park_c")

    if command == "JOG":
        if len(parts) != 3:
            return {"ok": False, "error": "usage: JOG <axis> <mm>"}
        axis = parts[1]
        delta_mm = float(parts[2])

        def task():
            _do_jog(axis, delta_mm)

        submitUiMachineTask(task)
        return {"ok": True, "action": "jog", "axis": axis.upper(), "delta": delta_mm}

    if command == "ADJUST_JOG_INCREMENT":
        if len(parts) != 2:
            return {"ok": False, "error": "usage: ADJUST_JOG_INCREMENT <steps>"}
        try:
            steps = int(parts[1])
        except ValueError:
            return {"ok": False, "error": "usage: ADJUST_JOG_INCREMENT <steps>"}
        if steps == 0:
            return {
                "ok": True,
                "action": "adjust_jog_increment",
                "steps": 0,
                "jog_step_mm": _get_jog_increment_mm(),
            }
        try:
            _run_on_edt(lambda: _adjust_jog_increment(steps))
        except Exception as error:
            return {"ok": False, "error": str(error)}
        return {
            "ok": True,
            "action": "adjust_jog_increment",
            "steps": steps,
            "jog_step_mm": _get_jog_increment_mm(),
        }

    if command == "ADJUST_SPEED":
        if len(parts) != 2:
            return {"ok": False, "error": "usage: ADJUST_SPEED <steps>"}
        try:
            steps = int(parts[1])
        except ValueError:
            return {"ok": False, "error": "usage: ADJUST_SPEED <steps>"}
        if steps == 0:
            return {
                "ok": True,
                "action": "adjust_speed",
                "steps": 0,
                "speed_pct": _get_machine_speed_pct(),
            }
        try:
            _run_on_edt(lambda: _adjust_machine_speed(steps))
        except Exception as error:
            return {"ok": False, "error": str(error)}
        return {
            "ok": True,
            "action": "adjust_speed",
            "steps": steps,
            "speed_pct": _get_machine_speed_pct(),
        }

    return {"ok": False, "error": "unknown command: " + command}


def _client_loop(connection):
    try:
        data = ""
        while True:
            chunk = connection.recv(4096)
            if not chunk:
                break
            data += chunk
            while "\n" in data:
                line, data = data.split("\n", 1)
                if not line.strip():
                    continue
                try:
                    response = _handle_command(line)
                except Exception as error:
                    response = {"ok": False, "error": str(error)}
                    _log("command error: " + traceback.format_exc())
                _reply(connection, response)
    except Exception:
        _log("client error: " + traceback.format_exc())
    finally:
        try:
            connection.close()
        except Exception:
            pass


def _accept_loop(server):
    while True:
        try:
            connection, _addr = server.accept()
            thread = threading.Thread(target=_client_loop, args=(connection,))
            thread.daemon = True
            thread.start()
        except Exception:
            _log("accept error: " + traceback.format_exc())


def _write_bridge_info():
    info = json.dumps({"host": BRIDGE_HOST, "port": BRIDGE_PORT})
    try:
        with open(BRIDGE_INFO_PATH, "w") as handle:
            handle.write(info)
    except Exception as error:
        _log("failed to write bridge info: " + str(error))


def _start_bridge():
    # Jython does not support AF_UNIX; use localhost TCP instead.
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((BRIDGE_HOST, BRIDGE_PORT))
    server.listen(5)

    thread = threading.Thread(target=_accept_loop, args=(server,))
    thread.daemon = True
    thread.start()

    _write_bridge_info()
    _log("listening on {0}:{1}".format(BRIDGE_HOST, BRIDGE_PORT))


_start_bridge()