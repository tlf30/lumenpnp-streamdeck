# Stream Deck OpenPnP

Stream Deck + XL controller for LumenPNP / OpenPnP.

## After install

```bash
streamdeck-setup-user
systemctl --user enable --now streamdeck
streamdeck-test-bridge
```

Replug the Stream Deck after the first install so udev permissions apply.

## Configuration

Edit `~/.config/streamdeck/config.yaml`:

- `notifications_enabled` — desktop popups on/off
- `lock_idle_timeout_sec` — auto-lock delay (0 to disable)
- `lock_idle_warning_sec` — warning flash before auto-lock

Restart after changes:

```bash
systemctl --user restart streamdeck
```

## OpenPnP bridge

`streamdeck-setup-user` links the bridge into `~/.openpnp2/scripts/Events/Startup.py`.
Restart OpenPnP after bridge updates.