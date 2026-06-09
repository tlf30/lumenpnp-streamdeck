from __future__ import annotations

import json
import socket
import threading
from dataclasses import dataclass
from typing import Any

from .bridge_config import load_bridge_endpoint


@dataclass
class Position:
    x: float | None = None
    y: float | None = None
    z: float | None = None
    c: float | None = None
    jog_step_mm: float | None = None
    speed_pct: float | None = None
    machine_enabled: bool | None = None
    machine_homed: bool | None = None
    selected_tool: str | None = None
    selected_tool_name: str | None = None
    selected_tool_kind: str | None = None
    move_tool_to_camera_enabled: bool | None = None
    move_camera_to_tool_enabled: bool | None = None
    nozzle_tip: str | None = None
    machine_status: str | None = None
    job_state: str | None = None
    placements_total: int | None = None
    placements_completed: int | None = None
    placements_remaining: int | None = None
    nozzle_parts: dict[str, str | None] | None = None
    actuators: dict[str, bool | None] | None = None
    ok: bool = False
    error: str | None = None


class OpenPnPClient:
    def __init__(self, config: dict | None = None) -> None:
        self.host, self.port = load_bridge_endpoint(config)
        self._sock: socket.socket | None = None
        self._lock = threading.Lock()

    def _disconnect(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    def _connect(self) -> socket.socket:
        client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client.connect((self.host, self.port))
        return client

    def send(self, command: str, timeout: float = 2.0) -> dict[str, Any]:
        with self._lock:
            last_error: str | None = None
            for attempt in range(2):
                try:
                    if self._sock is None:
                        self._sock = self._connect()
                    self._sock.settimeout(timeout)
                    self._sock.sendall((command.strip() + "\n").encode("utf-8"))
                    data = b""
                    while b"\n" not in data:
                        chunk = self._sock.recv(4096)
                        if not chunk:
                            raise ConnectionError("bridge closed connection")
                        data += chunk
                    line = data.split(b"\n", 1)[0].decode("utf-8")
                    return json.loads(line)
                except ConnectionRefusedError:
                    self._disconnect()
                    return {"ok": False, "error": "bridge not running"}
                except (ConnectionError, BrokenPipeError, OSError, json.JSONDecodeError) as error:
                    self._disconnect()
                    last_error = str(error)
                    if attempt == 0:
                        continue
                except Exception as error:
                    self._disconnect()
                    return {"ok": False, "error": str(error)}
            return {"ok": False, "error": last_error or "bridge not running"}

    def ping(self) -> bool:
        return bool(self.send("PING").get("ok"))

    def home(self) -> dict[str, Any]:
        return self.send("HOME")

    def park(self) -> dict[str, Any]:
        return self.send("PARK_XY")

    def jog(self, axis: str, mm: float) -> dict[str, Any]:
        return self.send("JOG {0} {1}".format(axis.upper(), mm))

    def adjust_jog_increment(self, steps: int) -> dict[str, Any]:
        return self.send("ADJUST_JOG_INCREMENT {0}".format(int(steps)))

    def adjust_speed(self, steps: int) -> dict[str, Any]:
        return self.send("ADJUST_SPEED {0}".format(int(steps)))

    def get_position(self) -> Position:
        result = self.send("GET_POSITION")
        if not result.get("ok"):
            return Position(ok=False, error=result.get("error", "unknown error"))
        return Position(
            ok=True,
            x=result.get("x"),
            y=result.get("y"),
            z=result.get("z"),
            c=result.get("c"),
            jog_step_mm=result.get("jog_step_mm"),
            speed_pct=result.get("speed_pct"),
            machine_enabled=result.get("machine_enabled"),
            machine_homed=result.get("machine_homed"),
            selected_tool=result.get("selected_tool"),
            selected_tool_name=result.get("selected_tool_name"),
            selected_tool_kind=result.get("selected_tool_kind"),
            move_tool_to_camera_enabled=result.get("move_tool_to_camera_enabled"),
            move_camera_to_tool_enabled=result.get("move_camera_to_tool_enabled"),
            nozzle_tip=result.get("nozzle_tip"),
            machine_status=result.get("machine_status"),
            job_state=result.get("job_state"),
            placements_total=result.get("placements_total"),
            placements_completed=result.get("placements_completed"),
            placements_remaining=result.get("placements_remaining"),
            nozzle_parts=result.get("nozzle_parts"),
            actuators=result.get("actuators"),
        )

    def read_actuator(self, name: str) -> dict[str, Any]:
        return self.send("READ_ACTUATOR {0}".format(name), timeout=20.0)