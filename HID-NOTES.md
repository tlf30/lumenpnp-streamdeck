# Stream Deck + XL HID — proper approach

Notes from validated work on the LumenPNP OpenPnP deck (PID `0x00C6`).
Use this as the reference when touching input, debugging keys, or extending the driver.

## Official spec (not the Node.js plugin SDK)

We use the **Elgato USB HID protocol**, not the desktop plugin SDK.

- General reference: https://docs.elgato.com/streamdeck/hid/general/
- Stream Deck + XL: https://docs.elgato.com/streamdeck/hid/stream-deck-plus-xl/

### Input report layout (all devices)

| Offset | Field |
|--------|-------|
| `0x00` | Report ID — always `0x01` for input |
| `0x01` | Command — `0x00` keys, `0x02` touch, `0x03` dials |
| `0x02` | Payload length — UINT16 little-endian |
| `0x04` | Payload |

### + XL specifics

- **36 keys** (9×4), payload length `36` for key reports
- Each key byte: `0x00` = released, `0x01` = pressed
- **6 encoders** — dial payload length = subtype + 6
- Touch payload lengths: TAP `0x10`, PRESS `0x0A`, FLICK `0x0E`
- **Poll interval: 50 ms** (general reference recommendation)

### Feature report 0x08 (unit information)

Expected for + XL:

| Field | Value |
|-------|-------|
| rows | 4 |
| cols | 9 |
| key size | 112×112 px |
| LCD | 1280×800 px |

## Code map — single source of truth

| File | Role |
|------|------|
| `streamdeck_app/hid_spec.py` | **Authoritative parser** — `parse_input_report()`, validators, driver comparison helpers |
| `streamdeck_app/devices/streamdeck_plus_xl.py` | XL driver — `_read_control_states`, `_read`, lock-aware dispatch |
| `streamdeck_app/patch_hid_transport.py` | `hid_read_timeout` for blocking reads without starving USB writes |
| `streamdeck_app/controller.py` | App logic — lock gesture, reader pause during bulk render |
| `log-events.py` | **Spec validation test** — do not duplicate parsing elsewhere |

**Rule:** parse raw HID bytes only through `hid_spec.py`. Compare driver output with `compare_key_states()` / `driver_key_states_from_report()`.

## Key index layout (physical → HID)

Index formula: `row * 9 + col` (row 0 = top).

```
Row 0:  [0 HOME]  ...                    [8 LOCK]
Row 1:  [9 PWR]  [10 X-] [11 PkXY] [12 X+] [13 PkZ] ... [17 JOB]
Row 2:       [19 Y-] [20 Z-] ... [23 Cam→N] ... [26 STEP]
Row 3:  [27 TOOL] ...                    [34 VAC1][35 VAC2]
```

Constants live in `streamdeck_app/layout.py` (`LOCK_KEY_INDEX = 8`, etc.).

**Common mistake:** assuming the top-left LOG/test button is a different index — it is **key 0**. LOCK is **key 8** (top-right).

## Input reader — two valid modes

### 1. `xl-driver` (controller path) — default for this project

- Uses `StreamDeckPlusXL._read()` + `hid_read_timeout`
- Default timeout: **50 ms** (`hid_spec.SPEC_POLL_INTERVAL_MS`)
- Drains burst after first blocking read (timeout → 0 for queue drain)
- `seed_input_baseline()` at startup syncs key state without firing callbacks

### 2. `library` (canonical python-elgato-streamdeck)

- Uses base `StreamDeck._read()` + `read_poll_hz=20` (50 ms sleep on timeout)
- `input_read_timeout_ms = 0` — pure poll + sleep
- Useful for A/B comparison in `./log-events --mode library`

**Do not** mix custom parsers, ad-hoc offset math, or duplicate `_parse_report()` helpers in test scripts.

## Lock key gesture (controller)

Configured in `config.yaml`: `default_locked: true`.

| Starting state | DOWN | UP |
|----------------|------|-----|
| **Locked** | Unlock immediately | Stay unlocked (no re-lock) |
| **Unlocked** | Show pressed state | Lock |

Implementation: `_handle_lock_key_edge()` in `controller.py` with:

- `_lock_key_events` queue (decouple callback from render)
- `_lock_gesture_started_locked` — prevents unlock-then-relock on first tap
- `_lock_gesture_cooldown_until` — debounce after gesture completes
- Level-trigger fallback via `_update_lock_key_from_hardware()` for missed edges

**Bug that was fixed:** first tap while locked unlocked on DOWN and re-locked on UP. Fix depends on tracking gesture start state, not treating UP as always “lock”.

## Bulk USB writes vs input

Image uploads must not starve HID reads.

1. **`_deck_update()`** — pauses reader (`_setup_reader(None)`), renders, then `_drain_deck_input()` and restarts reader
2. **`patch_hid_transport`** — blocking `hid_read_timeout` reads **do not** hold `device.mutex`; writes do
3. Prefer partial key updates (`_dirty_control_keys`) over full-frame re-render on every action

## Testing workflow

Only **one process** may open the HID device at a time.

```bash
# Stop anything holding the deck
pkill -f streamdeck_app.controller
pkill -f log-events

# Spec validation test (recommended before controller changes)
./log-events                    # xl-driver, 50 ms, display flash on edge
./log-events --no-display       # HID-only, no image upload interference
./log-events --mode library     # compare canonical library reader
./log-events --strict           # exit 1 on any spec violation

# Key index discovery (stop log-events/controller first!)
.venv/bin/python identify-keys

# Run controller
./run
```

### What to look for in logs

| Log file | Contents |
|----------|----------|
| `~/.openpnp2/log/streamdeck-events.log` | Spec test — `SPEC KEY`, `EDGE`, `VIOLATION`, `DRIVER` |
| `~/.openpnp2/log/streamdeck-hid.log` | Driver-level raw + edges |
| `~/.openpnp2/log/streamdeck-controller.log` | App — `key N down/up`, lock/unlock |

**Healthy key press:**

```
SPEC KEY pressed=[8]
EDGE key 8 DOWN
EDGE key 8 UP
```

**Healthy first lock tap (started locked):**

```
key 8 down (hw)
machine controls unlocked
key 8 up (hw)
```

No `machine controls locked` immediately after that UP.

### Validated 2026-06-08

- Keys 0, 8, and middle-row keys — clean single DOWN/UP per press
- Spec parser matches driver parser — zero `DRIVER_MISMATCH`
- Unit info feature report 0x08 — matches XL geometry
- Controller lock cycle after restart — first press unlocks without re-lock on release

## Pitfalls

1. **Stale process holds device** — `identify-keys`, `log-events`, or a crashed controller blocks `./run` with `TransportError`
2. **Duplicate parsers** — causes false “missed press” diagnosis when parser and driver disagree
3. **Wrong poll rate** — 100 ms works but spec says 50 ms; use `SPEC_POLL_INTERVAL_MS`
4. **Assuming press without checking index** — always confirm `pressed=[N]` in log, not just “a report arrived”
5. **Empty `pressed=[]` report** — may be release-only if DOWN was missed during heavy render; check `_deck_update` pause/drain
6. **Elgato plugin SDK docs** — wrong API; we are on raw HID

## OpenPnP target position buttons (keys 5 and 23)

Matches `MachineControlsPanel.enableToolActions()` in OpenPnP:

| Key | Command | Enabled when |
|-----|---------|--------------|
| **5** | `MOVE_TOOL_TO_CAMERA` | Machine on **and camera selected** |
| **23** | `MOVE_CAMERA_TO_TOOL` | Machine on **and nozzle/non-camera selected** |

The controller derives this from `selected_tool_kind` polled via `GET_POSITION` (no OpenPnP restart required). After an OpenPnP restart, the bridge also reports `move_tool_to_camera_enabled` / `move_camera_to_tool_enabled` from `action.isEnabled()` directly.

## Adding or changing input handling

1. Change parsing in `hid_spec.py` only
2. Run `./log-events --no-display --strict` and press keys 0, 8, and a few others
3. Confirm `DRIVER` lines are absent (spec == driver)
4. Run `./run`, test lock first-press and full lock/unlock cycle
5. Check `streamdeck-controller.log` for single unlock/lock per gesture