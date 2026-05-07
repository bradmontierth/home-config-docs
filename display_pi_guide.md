# Display Pi Guide

The kitchen display Raspberry Pi is available on the local network as:

```bash
ssh display-pi
```

Expected SSH config entry:

```sshconfig
Host display-pi
  HostName 192.168.10.92
  User pi
  IdentityFile ~/.ssh/id_ed25519_display_pi
  IdentitiesOnly yes
```

## Device role

`display-pi` is a Raspberry Pi connected to a touchscreen display in the kitchen.

It runs a Home Assistant dashboard in kiosk mode.

The primary purpose of this device is to provide a dedicated always-on kitchen control panel for Home Assistant.

## Mental model

Treat this host as a local dashboard appliance:

```text
display-pi
        ↓
touchscreen display
        ↓
browser / kiosk mode
        ↓
Home Assistant dashboard
```

Home Assistant itself is not running on this Pi. This Pi is the display/client device that shows the Home Assistant UI.
