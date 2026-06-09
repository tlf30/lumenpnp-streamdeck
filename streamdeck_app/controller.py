#!/usr/bin/env python3
"""Stream Deck + XL controller for LumenPNP / OpenPnP."""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import streamdeck_app.patch_streamdeck  # noqa: F401 - registers Stream Deck + XL support
from StreamDeck.DeviceManager import DeviceManager
from StreamDeck.Devices.StreamDeck import DialEventType
from StreamDeck.ImageHelpers import PILHelper

from streamdeck_app.client import OpenPnPClient, Position
from streamdeck_app.config import load_config
from streamdeck_app.hid_spec import SPEC_POLL_INTERVAL_MS
from streamdeck_app.layout import (
    CAM_TO_TOOL_KEY_INDEX,
    DIAL_AXES,
    DEFAULT_DIAL_STEP_SIZES_MM,
    DEFAULT_DIAL_XY_STEP_SIZES_MM,
    JOG_INCREMENT_DIAL_INDEX,
    JOG_INCREMENT_STEPS_MM,
    SPEED_DIAL_INDEX,
    SPEED_STEP_PCT,
    JOB_KEY_INDEX,
    JOB_STEP_KEY_INDEX,
    KEY_ACTIONS,
    LOCK_KEY_INDEX,
    POWER_KEY_INDEX,
    TOOL_KEY_INDEX,
    TOOL_TO_CAM_KEY_INDEX,
    VAC1_KEY_INDEX,
    VAC2_KEY_INDEX,
    actuator_name_for_key,
    build_command,
    is_cam_to_tool_key,
    is_icon_key,
    is_job_key,
    is_key_allowed,
    is_key_disabled,
    is_lock_blocked_key,
    is_lock_key,
    is_openpnp_target_action_enabled,
    is_power_key,
    is_release_triggered_key,
    is_tool_to_cam_key,
    is_toggle_key,
    is_tool_key,
    iter_control_keys,
)
from streamdeck_app.render import (
    render_button_image,
    render_icon_button,
    render_lock_button,
    render_toggle_button,
    render_tool_button,
    render_touchscreen_status,
)

LOG_PATH = Path.home() / ".openpnp2" / "log" / "streamdeck-controller.log"


def _log(message: str) -> None:
    line = time.strftime("%Y-%m-%d %H:%M:%S") + " " + message + "\n"
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(line)
    except OSError:
        pass
    print(message, flush=True)


class StreamDeckController:
    def __init__(self, config: dict) -> None:
        self.config = config
        self.client = OpenPnPClient(config)
        self.deck = None
        self.last_position: Position | None = None
        self.bridge_connected = False
        self.notified_disconnect = False
        self.pressed_keys: set[int] = set()
        self.locked = config.get("default_locked", True)
        self.last_locked: bool | None = None
        self.jog_step_mm = float(config.get("jog_step_mm", 1.0))
        self.last_jog_step_mm: float | None = None
        self.speed_pct = float(config.get("default_speed_pct", 100.0))
        self.last_speed_pct: float | None = None
        self._speed_poll_hold_until = 0.0
        self.machine_enabled: bool | None = None
        self.last_machine_enabled: bool | None = None
        self.machine_homed: bool | None = None
        self.last_machine_homed: bool | None = None
        self.selected_tool: str | None = None
        self.last_selected_tool: str | None = None
        self.selected_tool_kind: str | None = None
        self.last_selected_tool_kind: str | None = None
        self.move_tool_to_camera_enabled: bool | None = None
        self.last_move_tool_to_camera_enabled: bool | None = None
        self.move_camera_to_tool_enabled: bool | None = None
        self.last_move_camera_to_tool_enabled: bool | None = None
        self.nozzle_tip: str | None = None
        self.last_nozzle_tip: str | None = None
        self.job_state: str | None = None
        self.last_job_state: str | None = None
        self.actuator_states: dict[str, bool | None] = {"VAC1": None, "VAC2": None}
        self.last_actuator_states: dict[str, bool | None] = {"VAC1": None, "VAC2": None}
        self.actuator_readings: dict[str, str | None] = {"VAC1": None, "VAC2": None}
        self.last_actuator_readings: dict[str, str | None] = {"VAC1": None, "VAC2": None}
        self._last_vacuum_poll = 0.0
        self._vacuum_read_index = 0
        self._dirty_control_keys: set[int] = set(iter_control_keys())
        self._last_touchscreen_render = 0.0
        self._touchscreen_min_interval_sec = (
            float(config.get("touchscreen_min_interval_ms", 500)) / 1000.0
        )
        self._key_applied_down: set[int] = set()
        self.lock_idle_timeout_sec = float(config.get("lock_idle_timeout_sec", 120))
        self.lock_idle_warning_sec = float(config.get("lock_idle_warning_sec", 10))
        self._last_input_time = time.time()
        self._idle_warning_active = False
        self._last_warning_flash_phase: int | None = None
        dial_step_sizes = config.get("dial_step_sizes_mm", list(DEFAULT_DIAL_STEP_SIZES_MM))
        self._dial_step_sizes = tuple(float(step) for step in dial_step_sizes)
        if not self._dial_step_sizes:
            self._dial_step_sizes = DEFAULT_DIAL_STEP_SIZES_MM
        dial_xy_step_sizes = config.get("dial_xy_step_sizes_mm", list(DEFAULT_DIAL_XY_STEP_SIZES_MM))
        self._dial_xy_step_sizes = tuple(float(step) for step in dial_xy_step_sizes)
        if not self._dial_xy_step_sizes:
            self._dial_xy_step_sizes = DEFAULT_DIAL_XY_STEP_SIZES_MM
        default_dial_step = float(config.get("dial_default_step_mm", self._dial_step_sizes[0]))
        default_xy_dial_step = float(
            config.get("dial_xy_default_step_mm", self._dial_xy_step_sizes[0])
        )
        self.dial_steps_mm = {
            axis: default_xy_dial_step if axis in ("X", "Y") else default_dial_step
            for axis in DIAL_AXES
        }
        self.last_dial_steps_mm = dict(self.dial_steps_mm)
        self._dial_events: queue.Queue[tuple[int, DialEventType, object]] = queue.Queue()
        self._pending_dial_jog = {axis: 0.0 for axis in DIAL_AXES}
        self._pending_dial_jog_started: float | None = None
        self._dial_jog_flush_sec = float(config.get("dial_jog_coalesce_ms", 30)) / 1000.0
        self._jog_increment_dial_index = int(
            config.get("jog_increment_dial_index", JOG_INCREMENT_DIAL_INDEX)
        )
        self._speed_dial_index = int(
            config.get("speed_dial_index", SPEED_DIAL_INDEX)
        )
        self._lockable_dial_indices = {
            self._jog_increment_dial_index,
            self._speed_dial_index,
        }
        self._dial_unlock_timeout_sec = float(
            config.get("dial_unlock_timeout_sec", 10)
        )
        self._dial_locked = set(self._lockable_dial_indices)
        self._dial_unlock_deadline: dict[int, float] = {}
        self._lock_key_applied_down = False
        self._lock_gesture_started_locked = False
        self._lock_gesture_cooldown_until = 0.0
        self._lock_key_touch_started_at = 0.0
        self._lock_min_press_sec = 0.12
        self._lock_gesture_cooldown_sec = 0.2
        self._key_event_queue: queue.SimpleQueue[tuple[int, bool]] = (
            queue.SimpleQueue()
        )

    def _notify(self, message: str) -> None:
        if not self.config.get("notifications_enabled", True):
            return
        subprocess.Popen(
            ["notify-send", "Stream Deck", message],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def open_deck(self):
        decks = DeviceManager().enumerate()
        if not decks:
            raise RuntimeError("No Stream Deck found. Check USB connection and udev rules.")

        self.deck = decks[0]
        self.deck.open()
        self.deck._setup_reader(None)
        self.deck.reset()
        self.deck.set_brightness(self.config.get("brightness", 60))
        if hasattr(self.deck.device, "input_read_timeout_ms"):
            self.deck.device.input_read_timeout_ms = 0
        if hasattr(self.deck, "seed_input_baseline"):
            self.deck.seed_input_baseline()

        if not getattr(self.deck, "DECK_TOUCH", False):
            raise RuntimeError(
                "This device has no touch strip. XYZ display requires a Stream Deck + XL."
            )

        _log(f"Connected to {self.deck.deck_type()} ({self.deck.key_count()} keys)")

    def _attach_deck_callbacks(self) -> None:
        if self.deck is None:
            return
        self._lock_gesture_started_locked = False
        self._lock_gesture_cooldown_until = 0.0
        self._lock_key_touch_started_at = 0.0
        self._reset_lock_key_tracking()
        self._reset_key_tracking()
        self.deck.set_key_callback(self._on_key_change)
        if hasattr(self.deck, "set_dial_callback"):
            self.deck.set_dial_callback(self._on_dial_change)

    def _ensure_input_reader(self) -> None:
        if self.deck is None or not self.deck.is_open():
            return
        thread = getattr(self.deck, "read_thread", None)
        if thread is not None and thread.is_alive():
            return
        _log("deck input reader stopped; restarting")
        self.deck._setup_reader(self.deck._read)

    def _drain_deck_input(self) -> None:
        if self.deck is None or not self.deck.is_open():
            return
        if not hasattr(self.deck, "poll_input"):
            return
        if hasattr(self.deck.device, "input_read_timeout_ms"):
            self.deck.device.input_read_timeout_ms = 0
        self.deck.poll_input(64)

    @contextmanager
    def _deck_update(self):
        """Bulk USB writes; HID reads run concurrently (official SDK model)."""
        if self.deck is None:
            yield
            return
        try:
            yield
        finally:
            self._reconcile_input()

    def _mark_controls_dirty(self, *keys: int) -> None:
        self._dirty_control_keys.update(keys)

    def _mark_all_controls_dirty(self) -> None:
        self._dirty_control_keys.update(iter_control_keys())

    def _take_dirty_controls(self) -> list[int]:
        keys = sorted(self._dirty_control_keys)
        self._dirty_control_keys.clear()
        return keys

    def _touchscreen_render_due(self, *, urgent: bool) -> bool:
        if urgent:
            return True
        return (
            time.monotonic() - self._last_touchscreen_render
            >= self._touchscreen_min_interval_sec
        )

    def _poll_deck_input(self) -> None:
        if self.deck is None or not self.deck.is_open():
            return
        self._ensure_input_reader()

    def _lock_key_hardware_down(self) -> bool | None:
        latest = getattr(self.deck, "_latest_key_states", None)
        if not latest or LOCK_KEY_INDEX >= len(latest):
            return None
        return bool(latest[LOCK_KEY_INDEX])

    def _on_key_change(self, deck, key: int, state: bool) -> None:
        self._key_event_queue.put((key, state))

    def _on_dial_change(self, deck, dial_index: int, event_type: DialEventType, value) -> None:
        self._dial_events.put((dial_index, event_type, value))

    def _reset_idle_timer(self) -> bool:
        was_warning = self._idle_warning_active
        self._last_input_time = time.time()
        self._idle_warning_active = False
        self._last_warning_flash_phase = None
        return was_warning

    def _lock_warning_pulse(self) -> bool:
        if self.locked or self.lock_idle_timeout_sec <= 0:
            return False
        elapsed = time.time() - self._last_input_time
        warning_start = self.lock_idle_timeout_sec - self.lock_idle_warning_sec
        if elapsed < warning_start:
            return False
        return int((elapsed - warning_start) * 2) % 2 == 1

    def _check_idle_lock(self) -> bool:
        """Auto-lock after idle timeout. Returns True if lock state changed."""
        if self.locked or self.lock_idle_timeout_sec <= 0:
            self._idle_warning_active = False
            self._last_warning_flash_phase = None
            return False

        elapsed = time.time() - self._last_input_time
        if elapsed < self.lock_idle_timeout_sec:
            return False

        self.locked = True
        self.last_locked = True
        self._reset_lock_key_tracking()
        self._discard_dial_input()
        self._idle_warning_active = False
        self._last_warning_flash_phase = None
        _log("machine controls auto-locked (idle timeout)")
        self._mark_all_controls_dirty()
        position = self.last_position or Position(ok=False)
        self._render_frame(
            lock_key_pressed=False,
            control_keys=self._take_dirty_controls(),
            touchscreen=(position, self.bridge_connected),
        )
        self._notify("Controls locked — idle timeout")
        return True

    def _idle_lock_needs_lock_rerender(self) -> bool:
        if self.locked or self.lock_idle_timeout_sec <= 0:
            if self._idle_warning_active:
                self._idle_warning_active = False
                self._last_warning_flash_phase = None
                return True
            return False

        elapsed = time.time() - self._last_input_time
        warning_start = max(0.0, self.lock_idle_timeout_sec - self.lock_idle_warning_sec)
        warning_active = elapsed >= warning_start
        if warning_active != self._idle_warning_active:
            self._idle_warning_active = warning_active
            self._last_warning_flash_phase = None
            return True

        if not warning_active:
            return False

        phase = int((elapsed - warning_start) * 2)
        if phase == self._last_warning_flash_phase:
            return False
        self._last_warning_flash_phase = phase
        return True

    def _reset_key_tracking(self) -> None:
        latest = getattr(self.deck, "_latest_key_states", None)
        if not latest:
            self._key_applied_down.clear()
            return
        tracked = set(iter_control_keys()) - {LOCK_KEY_INDEX}
        self._key_applied_down = {
            key
            for key, down in enumerate(latest)
            if down and key in tracked
        }
        for key in list(self.pressed_keys):
            if is_lock_key(key):
                continue
            if key not in self._key_applied_down:
                self.pressed_keys.discard(key)

    def _collect_pending_key_edges(self) -> list[tuple[int, bool]]:
        edges: list[tuple[int, bool]] = []
        pop_edges = getattr(self.deck, "pop_key_edges", None)
        if pop_edges is not None:
            edges.extend(pop_edges())
        while True:
            try:
                edges.append(self._key_event_queue.get_nowait())
            except queue.Empty:
                break
        return edges

    def _process_key_edges(self) -> bool:
        cleared_warning = False
        tracked = set(iter_control_keys()) - {LOCK_KEY_INDEX}
        for key, down in self._collect_pending_key_edges():
            try:
                if key == LOCK_KEY_INDEX:
                    if down:
                        if self._lock_key_applied_down:
                            continue
                        self._lock_key_applied_down = True
                        self._on_lock_key_pressed()
                    else:
                        if not self._lock_key_applied_down:
                            continue
                        self._lock_key_applied_down = False
                        self._on_lock_key_released()
                    continue
                if key not in tracked:
                    continue
                if down:
                    if key in self._key_applied_down:
                        continue
                    if self._reset_idle_timer():
                        cleared_warning = True
                    self._key_applied_down.add(key)
                    self._on_control_key_pressed(key)
                else:
                    if key not in self._key_applied_down:
                        continue
                    self._key_applied_down.discard(key)
                    self._on_control_key_released(key)
            except Exception as error:
                _log(f"key {key} edge handler error: {error}")
                if key == LOCK_KEY_INDEX:
                    self._lock_key_applied_down = False
                else:
                    self._key_applied_down.discard(key)
                self.pressed_keys.discard(key)
        return cleared_warning

    def _reconcile_keys_level(self) -> bool:
        latest = getattr(self.deck, "_latest_key_states", None)
        if not latest:
            return False

        cleared_warning = False
        tracked = set(iter_control_keys()) - {LOCK_KEY_INDEX}
        for key in sorted(tracked):
            if key >= len(latest):
                continue
            hw_down = bool(latest[key])
            applied = key in self._key_applied_down
            if hw_down == applied:
                continue
            try:
                if hw_down:
                    if self._reset_idle_timer():
                        cleared_warning = True
                    self._key_applied_down.add(key)
                    self._on_control_key_pressed(key)
                else:
                    self._key_applied_down.discard(key)
                    self._on_control_key_released(key)
            except Exception as error:
                _log(f"key {key} handler error: {error}")
                if hw_down:
                    self._key_applied_down.discard(key)
                else:
                    self._key_applied_down.add(key)
                self.pressed_keys.discard(key)
        return cleared_warning

    def _on_control_key_pressed(self, key: int) -> None:
        self.pressed_keys.add(key)
        _log(f"key {key} down")
        if is_release_triggered_key(key):
            self._render_key(key, pressed=True)
            return
        self._handle_press(key)

    def _on_control_key_released(self, key: int) -> None:
        self.pressed_keys.discard(key)
        _log(f"key {key} up")
        if is_release_triggered_key(key):
            self._handle_press(key)
        self._render_key(key, pressed=False)

    def _reset_lock_key_tracking(self) -> None:
        hw_down = self._lock_key_hardware_down()
        self._lock_key_applied_down = hw_down if hw_down is not None else False
        self.pressed_keys.discard(LOCK_KEY_INDEX)
        self._lock_gesture_started_locked = False

    def _reconcile_lock_key_level(self) -> None:
        hw_down = self._lock_key_hardware_down()
        if hw_down is None or hw_down == self._lock_key_applied_down:
            return
        self._lock_key_applied_down = hw_down
        if hw_down:
            self._on_lock_key_pressed()
        else:
            self._on_lock_key_released()

    def _reconcile_input(self) -> bool:
        cleared = self._process_key_edges()
        self._reconcile_lock_key_level()
        if self._reconcile_keys_level():
            cleared = True
        return cleared

    def _on_lock_key_pressed(self) -> None:
        now = time.time()
        self._lock_gesture_started_locked = self.locked
        self._lock_key_touch_started_at = now
        self.pressed_keys.add(LOCK_KEY_INDEX)
        _log(f"key {LOCK_KEY_INDEX} down")
        if self._lock_key_disabled():
            _log(f"key {LOCK_KEY_INDEX} unlock blocked (bridge offline)")
            self._render_frame(lock_key_pressed=True)
            return
        lock_changed = self.locked
        if self.locked:
            self._apply_unlock_state()
        position = self.last_position or Position(ok=False)
        self._render_frame(
            lock_key_pressed=True,
            control_keys=self._take_dirty_controls() if lock_changed else None,
            touchscreen=(position, self.bridge_connected) if lock_changed else None,
        )

    def _on_lock_key_released(self) -> None:
        now = time.time()
        self.pressed_keys.discard(LOCK_KEY_INDEX)
        _log(f"key {LOCK_KEY_INDEX} up")
        lock_changed = False
        locked_on_release = False
        if not self._lock_gesture_started_locked:
            press_sec = now - self._lock_key_touch_started_at
            if press_sec < self._lock_min_press_sec:
                _log(
                    f"key {LOCK_KEY_INDEX} release ignored "
                    f"(bounce {press_sec * 1000:.0f}ms)"
                )
            elif now < self._lock_gesture_cooldown_until:
                _log(f"key {LOCK_KEY_INDEX} release ignored (gesture cooldown)")
            elif self._idle_warning_active:
                self._reset_idle_timer()
                _log("machine controls idle timer extended")
            else:
                lock_changed = not self.locked
                locked_on_release = lock_changed
                if locked_on_release:
                    self._apply_lock_state()
        position = self.last_position or Position(ok=False)
        self._render_frame(
            lock_key_pressed=False,
            control_keys=self._take_dirty_controls() if lock_changed else None,
            touchscreen=(position, self.bridge_connected) if lock_changed else None,
        )
        if locked_on_release:
            self._lock_gesture_cooldown_until = now + self._lock_gesture_cooldown_sec

    def _dial_step_sizes_for(self, axis: str) -> tuple[float, ...]:
        if axis in ("X", "Y"):
            return self._dial_xy_step_sizes
        return self._dial_step_sizes

    def _jog_increment_index(self, value: float | None = None) -> int:
        target = self.jog_step_mm if value is None else float(value)
        best_index = 0
        best_diff = abs(JOG_INCREMENT_STEPS_MM[0] - target)
        for index, step in enumerate(JOG_INCREMENT_STEPS_MM):
            diff = abs(step - target)
            if diff < best_diff:
                best_index = index
                best_diff = diff
        return best_index

    def _adjust_jog_increment_local(self, steps: int) -> bool:
        if steps == 0:
            return False
        index = self._jog_increment_index()
        new_index = max(0, min(len(JOG_INCREMENT_STEPS_MM) - 1, index + steps))
        if new_index == index:
            return False
        self.jog_step_mm = JOG_INCREMENT_STEPS_MM[new_index]
        _log(
            f"jog increment -> {self.jog_step_mm:g} mm "
            "(local — restart OpenPnP to sync slider)"
        )
        return True

    def _adjust_jog_increment(self, steps: int) -> bool:
        if steps == 0:
            return False
        result = self.client.adjust_jog_increment(steps)
        if result.get("ok"):
            if result.get("jog_step_mm") is not None:
                self.jog_step_mm = float(result["jog_step_mm"])
            else:
                self._sync_jog_step(self.client.get_position())
            _log(f"jog increment -> {self.jog_step_mm:g} mm")
            return True
        error = str(result.get("error", ""))
        if "unknown command" in error.lower():
            return self._adjust_jog_increment_local(steps)
        _log(f"command failed (ADJUST_JOG_INCREMENT {steps}): {error}")
        return False

    def _is_dial_locked(self, dial_index: int) -> bool:
        return dial_index in self._dial_locked

    def _dial_lock_label(self, dial_index: int) -> str:
        if dial_index == self._jog_increment_dial_index:
            return "jog"
        if dial_index == self._speed_dial_index:
            return "speed"
        return str(dial_index)

    def _dial_lock_display_changed(self, dial_index: int) -> bool:
        return dial_index in self._lockable_dial_indices

    def _toggle_dial_lock(self, dial_index: int) -> bool:
        if dial_index not in self._lockable_dial_indices:
            return False
        label = self._dial_lock_label(dial_index)
        if dial_index in self._dial_locked:
            self._dial_locked.discard(dial_index)
            self._dial_unlock_deadline[dial_index] = (
                time.monotonic() + self._dial_unlock_timeout_sec
            )
            _log(
                f"dial {dial_index} ({label}) unlocked "
                f"({self._dial_unlock_timeout_sec:g}s)"
            )
        else:
            self._dial_locked.add(dial_index)
            self._dial_unlock_deadline.pop(dial_index, None)
            _log(f"dial {dial_index} ({label}) locked")
        return self._dial_lock_display_changed(dial_index)

    def _check_dial_auto_relock(self) -> bool:
        display_changed = False
        now = time.monotonic()
        for dial_index, deadline in list(self._dial_unlock_deadline.items()):
            if now < deadline:
                continue
            self._dial_locked.add(dial_index)
            del self._dial_unlock_deadline[dial_index]
            label = self._dial_lock_label(dial_index)
            _log(f"dial {dial_index} ({label}) auto-locked")
            if self._dial_lock_display_changed(dial_index):
                display_changed = True
        return display_changed

    def _handle_jog_increment_dial(
        self, event_type: DialEventType, value
    ) -> tuple[bool, bool]:
        changed = False
        if event_type == DialEventType.PUSH:
            if value and self._toggle_dial_lock(self._jog_increment_dial_index):
                return False, True
            return False, False

        if event_type != DialEventType.TURN or value == 0:
            return False, False
        if self._is_dial_locked(self._jog_increment_dial_index):
            return False, False
        if self.locked:
            return False, False
        steps = int(value)
        _log(f"dial {self._jog_increment_dial_index} (jog) turn {steps:+d}")
        if self._adjust_jog_increment(steps):
            changed = True
        return changed, False

    def _adjust_speed_local(self, steps: int) -> bool:
        if steps == 0:
            return False
        new_speed = max(0.0, min(100.0, self.speed_pct + steps * SPEED_STEP_PCT))
        if new_speed == self.speed_pct:
            return False
        self.speed_pct = new_speed
        self._speed_poll_hold_until = time.monotonic() + 60.0
        _log(
            f"speed -> {self.speed_pct:.0f}% "
            "(deck only — restart OpenPnP for bridge sync)"
        )
        return True

    def _adjust_speed(self, steps: int) -> bool:
        if steps == 0:
            return False
        result = self.client.adjust_speed(steps)
        if result.get("ok"):
            self._speed_poll_hold_until = 0.0
            if result.get("speed_pct") is not None:
                self.speed_pct = float(result["speed_pct"])
            else:
                self._sync_speed(self.client.get_position())
            _log(f"speed -> {self.speed_pct:.0f}%")
            return True
        error = str(result.get("error", ""))
        if error:
            _log(f"command failed (ADJUST_SPEED {steps}): {error}")
        if "unknown command" in error.lower() or error:
            return self._adjust_speed_local(steps)
        return False

    def _handle_speed_dial(
        self, event_type: DialEventType, value
    ) -> tuple[bool, bool]:
        changed = False
        if event_type == DialEventType.PUSH:
            if value and self._toggle_dial_lock(self._speed_dial_index):
                return False, True
            return False, False

        if event_type != DialEventType.TURN or value == 0:
            return False, False
        if self._is_dial_locked(self._speed_dial_index):
            return False, False
        if self.locked:
            return False, False
        steps = int(value)
        _log(f"dial {self._speed_dial_index} (speed) turn {steps:+d}")
        if self._adjust_speed(steps):
            changed = True
        return changed, False

    def _cycle_dial_step(self, axis: str) -> None:
        current = self.dial_steps_mm[axis]
        sizes = self._dial_step_sizes_for(axis)
        try:
            next_index = (sizes.index(current) + 1) % len(sizes)
        except ValueError:
            next_index = 0
        self.dial_steps_mm[axis] = sizes[next_index]
        _log(f"dial {axis} step -> {sizes[next_index]:g} mm")

    def _dial_motion_allowed(self) -> bool:
        if self.locked:
            return False
        if self.machine_enabled is False:
            return False
        if self.machine_homed is False:
            return False
        return True

    def _discard_dial_input(self) -> None:
        for axis in DIAL_AXES:
            self._pending_dial_jog[axis] = 0.0
        self._pending_dial_jog_started = None
        while True:
            try:
                self._dial_events.get_nowait()
            except queue.Empty:
                break

    def _accumulate_dial_jog(self, axis: str, delta: float) -> None:
        self._pending_dial_jog[axis] += delta
        if self._pending_dial_jog_started is None:
            self._pending_dial_jog_started = time.monotonic()

    def _has_pending_dial_jog(self) -> bool:
        return any(delta != 0.0 for delta in self._pending_dial_jog.values())

    def _should_flush_dial_jogs(self) -> bool:
        if self.locked or not self._has_pending_dial_jog():
            return False
        if self._dial_events.empty():
            return True
        if self._pending_dial_jog_started is None:
            return True
        return (time.monotonic() - self._pending_dial_jog_started) >= self._dial_jog_flush_sec

    def _flush_dial_jogs(self) -> None:
        if self.locked or not self._has_pending_dial_jog():
            return
        for axis in DIAL_AXES:
            delta = self._pending_dial_jog[axis]
            if delta == 0.0:
                continue
            self._pending_dial_jog[axis] = 0.0
            result = self.client.jog(axis, delta)
            if not result.get("ok"):
                _log(f"command failed (JOG {axis} {delta:g}): {result.get('error')}")
            else:
                _log(f"dial jog {axis} {delta:g} mm")
        self._pending_dial_jog_started = None

    def _process_dial_events(self) -> tuple[bool, bool, bool, bool, bool]:
        steps_changed = False
        cleared_warning = False
        jog_increment_changed = False
        speed_changed = False
        dial_locks_changed = False
        while True:
            try:
                dial_index, event_type, value = self._dial_events.get_nowait()
            except queue.Empty:
                break

            if dial_index == self._jog_increment_dial_index:
                if self._reset_idle_timer():
                    cleared_warning = True
                jog_changed, lock_changed = self._handle_jog_increment_dial(
                    event_type, value
                )
                if jog_changed:
                    jog_increment_changed = True
                if lock_changed:
                    dial_locks_changed = True
                continue

            if dial_index == self._speed_dial_index:
                if self._reset_idle_timer():
                    cleared_warning = True
                speed_dial_changed, lock_changed = self._handle_speed_dial(
                    event_type, value
                )
                if speed_dial_changed:
                    speed_changed = True
                if lock_changed:
                    dial_locks_changed = True
                continue

            if dial_index >= len(DIAL_AXES):
                continue

            axis = DIAL_AXES[dial_index]
            if self._reset_idle_timer():
                cleared_warning = True

            if event_type == DialEventType.PUSH:
                if value:
                    self._cycle_dial_step(axis)
                    steps_changed = True
                continue

            if event_type != DialEventType.TURN or value == 0:
                continue

            if not self._dial_motion_allowed():
                continue

            step = self.dial_steps_mm[axis]
            delta = float(value) * step
            if axis == "C":
                delta = -delta
            self._accumulate_dial_jog(axis, delta)

        return (
            steps_changed,
            cleared_warning,
            jog_increment_changed,
            speed_changed,
            dial_locks_changed,
        )

    def _lock_key_disabled(self) -> bool:
        return self.locked and not self.bridge_connected

    def _apply_lock_state(self) -> None:
        if self.locked:
            return
        self.locked = True
        self.last_locked = True
        self._reset_lock_key_tracking()
        self._discard_dial_input()
        self._mark_all_controls_dirty()
        _log("machine controls locked")
        self._notify("Machine controls locked")

    def _ensure_locked_for_bridge(self) -> bool:
        if self.locked:
            return False
        self.locked = True
        self.last_locked = True
        self._reset_lock_key_tracking()
        self._discard_dial_input()
        self._mark_all_controls_dirty()
        _log("machine controls locked (bridge offline)")
        return True

    def _apply_unlock_state(self) -> None:
        if not self.locked or not self.bridge_connected:
            return
        self.locked = False
        self.last_locked = False
        if not self._lock_key_applied_down:
            self.pressed_keys.discard(LOCK_KEY_INDEX)
        self._discard_dial_input()
        self._reset_idle_timer()
        self._mark_all_controls_dirty()
        _log("machine controls unlocked")
        self._notify("Machine controls unlocked")

    def _command_timeout(self, command: str) -> float:
        if (
            command == "HOME"
            or command.startswith("PARK")
            or command.startswith("MOVE_")
        ):
            return 180.0
        if command.startswith("TOGGLE_ACTUATOR") or command.startswith("READ_ACTUATOR"):
            return 20.0
        return 2.0

    def _handle_press(self, key: int) -> None:
        if self.locked:
            if is_lock_blocked_key(key):
                self._render_key(key, pressed=True)
                self._notify("Controls are locked — tap UNLOCK first")
            return

        target_kwargs = self._openpnp_target_kwargs()
        if not is_key_allowed(
            key, self.machine_enabled, self.machine_homed, **target_kwargs
        ):
            self._render_key(key, pressed=True)
            if is_tool_to_cam_key(key) or is_cam_to_tool_key(key):
                if not is_openpnp_target_action_enabled(
                    key, self.machine_enabled, self.selected_tool_kind, **target_kwargs
                ):
                    if self.machine_enabled is not True:
                        message = "Start the machine first"
                    elif is_tool_to_cam_key(key):
                        message = "Select the camera first"
                    else:
                        message = "Select a nozzle first"
                else:
                    message = "Action not available"
            elif self.machine_enabled is False:
                message = "Start the machine first"
            else:
                message = "Home the machine first"
            self._notify(message)
            return

        if is_power_key(key):
            self._render_key(key, pressed=True)
            result = self.client.send("TOGGLE_MACHINE")
            if not result.get("ok"):
                _log(f"command failed (TOGGLE_MACHINE): {result.get('error')}")
            else:
                self._render_key(POWER_KEY_INDEX, pressed=False)
            return

        action = KEY_ACTIONS.get(key)
        if action is None:
            return

        self._render_key(key, pressed=True)
        command = build_command(action, self.jog_step_mm)
        if command:
            result = self.client.send(command, timeout=self._command_timeout(command))
            if not result.get("ok"):
                _log(f"command failed ({command}): {result.get('error')}")
                if is_toggle_key(key) or is_release_triggered_key(key):
                    self._notify(result.get("error", "Command failed"))
            elif is_toggle_key(key):
                self._last_vacuum_poll = 0.0
                self._mark_controls_dirty(key)
            elif is_tool_key(key):
                if result.get("selected_tool") is not None:
                    self.selected_tool = str(result.get("selected_tool"))
                if "nozzle_tip" in result:
                    tip = result.get("nozzle_tip")
                    self.nozzle_tip = str(tip) if tip is not None else None
                self._mark_controls_dirty(TOOL_KEY_INDEX)
            elif is_job_key(key):
                self._mark_controls_dirty(JOB_KEY_INDEX, JOB_STEP_KEY_INDEX)

    def _render_lock_key(self, pressed: bool = False, *, _batch: bool = False) -> None:
        pil_image = render_lock_button(
            self.deck.key_image_format()["size"][0],
            self.deck.key_image_format()["size"][1],
            self.locked,
            pressed=pressed,
            warning_pulse=self._lock_warning_pulse(),
            disabled=self._lock_key_disabled(),
        )
        native = PILHelper.to_native_key_format(self.deck, pil_image)
        if _batch:
            self.deck.set_key_image(LOCK_KEY_INDEX, native)
        else:
            with self.deck:
                self.deck.set_key_image(LOCK_KEY_INDEX, native)

    def _render_key(self, key: int, pressed: bool = False, *, _batch: bool = False) -> None:
        if is_lock_key(key):
            self._render_lock_key(pressed=pressed, _batch=_batch)
            return

        action = KEY_ACTIONS.get(key)
        if action is None:
            return

        size = self.deck.key_image_format()["size"]
        disabled = is_key_disabled(
            key,
            self.locked,
            self.machine_enabled,
            self.machine_homed,
            bridge_connected=self.bridge_connected,
            **self._openpnp_target_kwargs(),
        )
        if is_tool_key(key):
            pil_image = render_tool_button(
                size[0],
                size[1],
                action,
                pressed=pressed,
                disabled=disabled,
                selected_tool=self.selected_tool,
                nozzle_tip=self.nozzle_tip,
            )
        elif is_toggle_key(key):
            actuator_name = actuator_name_for_key(key)
            actuator_on = (
                self.actuator_states.get(actuator_name) if actuator_name else None
            )
            vacuum_value = (
                self.actuator_readings.get(actuator_name) if actuator_name else None
            )
            pil_image = render_toggle_button(
                size[0],
                size[1],
                action,
                pressed=pressed,
                disabled=disabled,
                actuator_on=actuator_on,
                vacuum_value=vacuum_value,
            )
        elif is_icon_key(key) or is_job_key(key):
            pil_image = render_icon_button(
                size[0],
                size[1],
                action,
                pressed=pressed,
                disabled=disabled,
                machine_enabled=self.machine_enabled,
                machine_homed=self.machine_homed,
                job_state=self.job_state if is_job_key(key) else None,
            )
        else:
            pil_image = render_button_image(
                size[0],
                size[1],
                action,
                pressed=pressed,
                disabled=disabled,
            )
        native = PILHelper.to_native_key_format(self.deck, pil_image)
        if _batch:
            self.deck.set_key_image(key, native)
        else:
            with self.deck:
                self.deck.set_key_image(key, native)

    def _upload_touchscreen(self, position: Position, connected: bool) -> None:
        pil_image = render_touchscreen_status(
            self.deck.touchscreen_image_format()["size"][0],
            self.deck.touchscreen_image_format()["size"][1],
            position,
            connected,
            self.jog_step_mm,
            locked=self.locked,
            dial_steps_mm=self.dial_steps_mm,
            jog_dial_locked=self._is_dial_locked(self._jog_increment_dial_index),
            speed_dial_locked=self._is_dial_locked(self._speed_dial_index),
            display_speed_pct=self.speed_pct,
        )
        self.deck.set_touchscreen_image(
            PILHelper.to_native_touchscreen_format(self.deck, pil_image)
        )

    def _render_frame(
        self,
        *,
        lock_key_pressed: bool | None = None,
        control_keys: list[int] | None = None,
        touchscreen: tuple[Position, bool] | None = None,
    ) -> None:
        keys = control_keys or []
        if lock_key_pressed is None and not keys and touchscreen is None:
            return
        with self._deck_update():
            with self.deck:
                if lock_key_pressed is not None:
                    self._render_lock_key(pressed=lock_key_pressed, _batch=True)
                for key in keys:
                    self._render_key(key, pressed=key in self.pressed_keys, _batch=True)
                if touchscreen is not None:
                    position, connected = touchscreen
                    self._upload_touchscreen(position, connected)
                    self._last_touchscreen_render = time.monotonic()

    def _flush_deck_input(self) -> bool:
        """Process queued key edges; drain HID only while the reader is stopped."""
        if self.deck is None or not self.deck.is_open():
            return False
        if not getattr(self.deck, "run_read_thread", False):
            self._drain_deck_input()
        return self._reconcile_input()

    def _render_startup_ui(self) -> None:
        """Initial draw with frequent input drains so early taps are not lost."""
        blank = PILHelper.to_native_key_format(
            self.deck, PILHelper.create_key_image(self.deck, background="#161a22")
        )
        with self.deck:
            for key in range(self.deck.key_count()):
                if key not in KEY_ACTIONS:
                    self.deck.set_key_image(key, blank)
                if key % 6 == 0:
                    self._flush_deck_input()

        position = Position(ok=False)
        for key in iter_control_keys():
            with self.deck:
                self._render_key(key, pressed=False)
            self._flush_deck_input()

        with self.deck:
            self._upload_touchscreen(position, False)
        self._flush_deck_input()
        self._last_touchscreen_render = time.monotonic()

    def _openpnp_target_kwargs(self) -> dict:
        return {
            "selected_tool_kind": self.selected_tool_kind,
            "move_tool_to_camera_enabled": self.move_tool_to_camera_enabled,
            "move_camera_to_tool_enabled": self.move_camera_to_tool_enabled,
        }

    def _refresh_touchscreen(self) -> None:
        position = self.last_position or Position(ok=False)
        self._render_frame(touchscreen=(position, self.bridge_connected))
        self.last_locked = self.locked

    def _positions_equal(self, left: Position | None, right: Position | None) -> bool:
        if left is None or right is None:
            return left is right
        return (
            left.ok == right.ok
            and left.x == right.x
            and left.y == right.y
            and left.z == right.z
            and left.c == right.c
            and left.jog_step_mm == right.jog_step_mm
            and left.speed_pct == right.speed_pct
            and left.machine_enabled == right.machine_enabled
            and left.machine_homed == right.machine_homed
            and left.selected_tool == right.selected_tool
            and left.nozzle_tip == right.nozzle_tip
            and left.machine_status == right.machine_status
            and left.job_state == right.job_state
            and left.placements_total == right.placements_total
            and left.placements_completed == right.placements_completed
            and left.placements_remaining == right.placements_remaining
            and left.nozzle_parts == right.nozzle_parts
            and left.actuators == right.actuators
            and left.error == right.error
        )

    def _poll_vacuum_readings(self) -> bool:
        interval = float(self.config.get("vacuum_poll_interval_ms", 1000)) / 1000.0
        now = time.time()
        if now - self._last_vacuum_poll < interval:
            return False

        self._last_vacuum_poll = now
        changed = False
        if not self.machine_enabled:
            for name in self.actuator_readings:
                if self.actuator_readings[name] is not None:
                    self.actuator_readings[name] = None
                    changed = True
            return changed

        for name, state in self.actuator_states.items():
            if state is not True and self.actuator_readings.get(name) is not None:
                self.actuator_readings[name] = None
                changed = True

        on_names = [name for name, state in self.actuator_states.items() if state is True]
        if not on_names:
            return changed

        # Poll one channel per interval so two active VACs do not block the deck loop.
        name = on_names[self._vacuum_read_index % len(on_names)]
        self._vacuum_read_index += 1
        result = self.client.read_actuator(name)
        if not result.get("ok"):
            _log(f"vacuum read failed ({name}): {result.get('error')}")
            return changed

        value = result.get("value")
        text = str(value).strip() if value is not None else None
        if text == "":
            text = None
        if self.actuator_readings.get(name) != text:
            self.actuator_readings[name] = text
            changed = True

        return changed

    def _sync_jog_step(self, position: Position) -> None:
        if position.ok and position.jog_step_mm is not None:
            self.jog_step_mm = float(position.jog_step_mm)
            return
        try:
            fresh = load_config()
            self.jog_step_mm = float(fresh.get("jog_step_mm", self.jog_step_mm))
        except OSError:
            pass

    def _sync_speed(self, position: Position) -> None:
        if time.monotonic() < self._speed_poll_hold_until:
            return
        if position.ok and position.speed_pct is not None:
            self.speed_pct = float(position.speed_pct)
            return
        try:
            fresh = load_config()
            self.speed_pct = float(fresh.get("default_speed_pct", self.speed_pct))
        except OSError:
            pass

    def run(self) -> None:
        self.open_deck()
        self._attach_deck_callbacks()
        timeout_ms = int(self.config.get("deck_input_timeout_ms", SPEC_POLL_INTERVAL_MS))
        if hasattr(self.deck, "_INPUT_READ_TIMEOUT_MS"):
            self.deck._INPUT_READ_TIMEOUT_MS = timeout_ms
        _log(f"deck input reader configured (hid_read_timeout={timeout_ms}ms)")
        _log("rendering deck layout")
        self._render_startup_ui()
        self.deck._setup_reader(self.deck._read)
        _log("deck input reader active")
        warmup_deadline = time.monotonic() + 0.25
        while time.monotonic() < warmup_deadline:
            self._reconcile_input()
            time.sleep(0.01)
        _log("deck ready for input")
        self.last_locked = self.locked
        self.last_jog_step_mm = self.jog_step_mm
        self.last_speed_pct = self.speed_pct
        if not self.locked:
            self._reset_idle_timer()
        status_poll_seconds = self.config.get("poll_interval_ms", 250) / 1000.0
        event_poll_seconds = self.config.get("event_poll_interval_ms", 16) / 1000.0
        last_status_poll = 0.0

        try:
            while self.deck.is_open():
                pending_lock_key: bool | None = None
                pending_touchscreen: tuple[Position, bool] | None = None

                self._poll_deck_input()
                cleared_warning = self._reconcile_input()
                (
                    dial_steps_changed,
                    dial_cleared_warning,
                    jog_increment_changed,
                    speed_changed,
                    dial_locks_changed,
                ) = self._process_dial_events()
                if self._check_dial_auto_relock():
                    dial_locks_changed = True
                if self._should_flush_dial_jogs():
                    self._flush_dial_jogs()
                if (cleared_warning or dial_cleared_warning) and not self.locked:
                    pending_lock_key = self._lock_key_applied_down
                if (
                    dial_steps_changed
                    or jog_increment_changed
                    or speed_changed
                    or dial_locks_changed
                ):
                    pending_touchscreen = (
                        self.last_position or Position(ok=False),
                        self.bridge_connected,
                    )
                    if dial_steps_changed:
                        self.last_dial_steps_mm = dict(self.dial_steps_mm)
                    if jog_increment_changed:
                        self.last_jog_step_mm = self.jog_step_mm
                    if speed_changed:
                        self.last_speed_pct = self.speed_pct
                if self._check_idle_lock():
                    pending_lock_key = self._lock_key_applied_down
                elif self._idle_lock_needs_lock_rerender():
                    pending_lock_key = self._lock_key_applied_down

                now = time.monotonic()
                if now - last_status_poll >= status_poll_seconds:
                    last_status_poll = now
                    connected = self.client.ping()
                    readings_changed = False
                    if connected:
                        position = self.client.get_position()
                        self._sync_jog_step(position)
                        self._sync_speed(position)
                        if position.ok:
                            if position.machine_enabled is not None:
                                self.machine_enabled = bool(position.machine_enabled)
                            if position.machine_homed is not None:
                                self.machine_homed = bool(position.machine_homed)
                            if position.selected_tool is not None:
                                self.selected_tool = str(position.selected_tool)
                            if position.selected_tool_kind is not None:
                                self.selected_tool_kind = str(position.selected_tool_kind)
                            if position.selected_tool_kind == "nozzle":
                                tip = position.nozzle_tip
                                self.nozzle_tip = str(tip) if tip else None
                            elif position.selected_tool_kind is not None:
                                self.nozzle_tip = None
                            if position.move_tool_to_camera_enabled is not None:
                                self.move_tool_to_camera_enabled = bool(
                                    position.move_tool_to_camera_enabled
                                )
                            if position.move_camera_to_tool_enabled is not None:
                                self.move_camera_to_tool_enabled = bool(
                                    position.move_camera_to_tool_enabled
                                )
                            if position.job_state is not None:
                                self.job_state = str(position.job_state)
                            if position.actuators:
                                for name, state in position.actuators.items():
                                    if name in self.actuator_states:
                                        self.actuator_states[name] = state
                        readings_changed = self._poll_vacuum_readings()
                        self.notified_disconnect = False
                    else:
                        position = Position(ok=False, error="bridge offline")
                        self._sync_jog_step(position)
                        if not self.notified_disconnect:
                            self._notify("OpenPnP bridge offline")
                            self.notified_disconnect = True

                    bridge_lock_changed = False
                    if not connected:
                        bridge_lock_changed = self._ensure_locked_for_bridge()

                    bridge_changed = connected != self.bridge_connected
                    if bridge_changed or bridge_lock_changed:
                        self._mark_controls_dirty(LOCK_KEY_INDEX)
                        if bridge_lock_changed:
                            self._mark_all_controls_dirty()

                    lock_changed = self.last_locked != self.locked
                    jog_changed = self.last_jog_step_mm != self.jog_step_mm
                    speed_changed_status = self.last_speed_pct != self.speed_pct
                    machine_changed = self.last_machine_enabled != self.machine_enabled
                    homed_changed = self.last_machine_homed != self.machine_homed
                    tool_changed = self.last_selected_tool != self.selected_tool
                    tool_kind_changed = (
                        self.last_selected_tool_kind != self.selected_tool_kind
                    )
                    target_actions_changed = (
                        tool_kind_changed
                        or self.last_move_tool_to_camera_enabled
                        != self.move_tool_to_camera_enabled
                        or self.last_move_camera_to_tool_enabled
                        != self.move_camera_to_tool_enabled
                    )
                    nozzle_tip_changed = self.last_nozzle_tip != self.nozzle_tip
                    job_changed = self.last_job_state != self.job_state
                    actuators_changed = self.actuator_states != self.last_actuator_states
                    position_changed = not self._positions_equal(
                        position, self.last_position
                    )
                    status_dial_steps_changed = (
                        self.dial_steps_mm != self.last_dial_steps_mm
                    )
                    if lock_changed or machine_changed or homed_changed:
                        self._mark_all_controls_dirty()
                    elif jog_changed:
                        self._mark_all_controls_dirty()
                    if tool_changed or nozzle_tip_changed:
                        self._mark_controls_dirty(TOOL_KEY_INDEX)
                    if target_actions_changed:
                        self._mark_controls_dirty(
                            TOOL_TO_CAM_KEY_INDEX, CAM_TO_TOOL_KEY_INDEX
                        )
                    if job_changed:
                        self._mark_controls_dirty(
                            JOB_KEY_INDEX, JOB_STEP_KEY_INDEX
                        )
                    if actuators_changed or readings_changed:
                        self._mark_controls_dirty(VAC1_KEY_INDEX, VAC2_KEY_INDEX)

                    touchscreen_urgent = (
                        bridge_changed
                        or bridge_lock_changed
                        or lock_changed
                        or jog_changed
                        or speed_changed_status
                        or status_dial_steps_changed
                        or machine_changed
                        or homed_changed
                        or tool_changed
                        or nozzle_tip_changed
                        or job_changed
                        or actuators_changed
                        or readings_changed
                    )
                    if (
                        connected != self.bridge_connected
                        or position_changed
                        or touchscreen_urgent
                    ) and self._touchscreen_render_due(urgent=touchscreen_urgent):
                        pending_touchscreen = (position, connected)

                    self.bridge_connected = connected
                    self.last_position = position
                    self.last_dial_steps_mm = dict(self.dial_steps_mm)
                    self.last_locked = self.locked
                    self.last_jog_step_mm = self.jog_step_mm
                    self.last_speed_pct = self.speed_pct
                    self.last_machine_enabled = self.machine_enabled
                    self.last_machine_homed = self.machine_homed
                    self.last_selected_tool = self.selected_tool
                    self.last_selected_tool_kind = self.selected_tool_kind
                    self.last_move_tool_to_camera_enabled = (
                        self.move_tool_to_camera_enabled
                    )
                    self.last_move_camera_to_tool_enabled = (
                        self.move_camera_to_tool_enabled
                    )
                    self.last_nozzle_tip = self.nozzle_tip
                    self.last_job_state = self.job_state
                    self.last_actuator_states = dict(self.actuator_states)
                    self.last_actuator_readings = dict(self.actuator_readings)

                controls = self._take_dirty_controls()
                if controls or pending_touchscreen or pending_lock_key is not None:
                    self._render_frame(
                        lock_key_pressed=pending_lock_key,
                        control_keys=controls,
                        touchscreen=pending_touchscreen,
                    )
                    if self._reconcile_input():
                        cleared_warning = True

                time.sleep(event_poll_seconds)
        except KeyboardInterrupt:
            pass
        finally:
            if self.deck is not None:
                self.deck.reset()
                self.deck.close()


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(root))
    config = load_config()
    controller = StreamDeckController(config)
    controller.run()


if __name__ == "__main__":
    main()