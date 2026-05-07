# Home Audio Pi Guide

The whole-home audio Raspberry Pi is available on the local network as:

```bash
ssh home-audio-pi
```

Expected SSH config entry:

```sshconfig
Host home-audio-pi
  HostName 192.168.10.140
  User pi
  IdentityFile ~/.ssh/id_ed25519_home_audio_pi
  IdentitiesOnly yes
```

## Device role

`home-audio-pi` is the central audio host for the whole-home audio system.

It provides audio for **five zones** connected to an amplifier.

## Audio hardware layout

Zones 1 through 4 use an 8-channel / 4-zone Raspberry Pi DAC HAT.

The fifth zone, the **loft**, uses a Behringer USB audio DAC.

High-level layout:

```text
home-audio-pi
├── Raspberry Pi DAC HAT
│   ├── Zone 1
│   ├── Zone 2
│   ├── Zone 3
│   └── Zone 4
└── Behringer USB DAC
    └── Zone 5 / Loft
```

All zones ultimately feed into the connected amplifier.

**numbered zones 1-4/5 may not line up with zone numbers on the amp or other internal numbers. Simply 1-4 for enumeration sake here.

## Snapcast setup

Snapserver runs on `home-audio-pi`.

Each audio zone is represented by a Snapcast client.

Music Assistant discovers and controls these devices through Snapcast.

## Home Assistant and Music Assistant

Home Assistant is not running on Pi.

Home Assistant and Music Assistant are running on the Beelink mini PC.

Music Assistant brings in the whole-home audio zones from `home-audio-pi` through Snapcast.

High-level control flow:

```text
Home Assistant / Music Assistant on Beelink
        ↓
Snapcast integration
        ↓
Snapserver on home-audio-pi
        ↓
Snapcast clients / audio outputs
        ↓
DAC HAT + Behringer USB DAC
        ↓
Amplifier
        ↓
Five audio zones
```
