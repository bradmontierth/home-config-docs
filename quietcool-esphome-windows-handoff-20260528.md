# QuietCool ESPHome Windows Handoff - 2026-05-28

## Current State

Device: `quiet-cool-dkv1`

Hardware inferred from active config:

- ESP32 board: `esp32dev`
- CC1101 SPI pins: CLK `GPIO18`, MOSI `GPIO23`, MISO `GPIO19`
- CC1101 control pins: CS `GPIO22`, GDO0 `GPIO13`, GDO2 `GPIO12`
- Remote ID to preserve: `[0xA7, 0x39, 0xC2, 0x51, 0x0D, 0xFE, 0x84]`
- Last known IP: `192.168.40.78`

The device is currently running a **minimal recovery firmware** only. API, web server, OTA, Wi-Fi, and logs work. QuietCool RF control is temporarily disabled in the live YAML to keep the device reachable.

Verified after recovery:

- `192.168.40.78:6053` open
- `192.168.40.78:80` open
- ESPHome logs connect successfully
- Wi-Fi signal in logs was strong, around `-31 dB`

Do not assume the current live firmware can control the fan. It cannot.

## What Was Attempted

Goal was to flash PR #17 from:

```yaml
external_components:
  - source:
      type: git
      url: https://github.com/ccrome/quiet-cool-rf-remote.git
      ref: refs/pull/17/head
    components: [quiet_cool]
```

with:

```yaml
fan:
  - platform: quiet_cool
    id: qc_fan
    name: QuietCool Fan
    cs_pin: 22
    gdo0_pin: 13
    gdo2_pin: 12
    remote_id: [0xA7, 0x39, 0xC2, 0x51, 0x0D, 0xFE, 0x84]
    center_freq_mhz: 433.897
    deviation_khz: 10
    preamble_byte: 0x55
    fsctrl0_override: 0x00
    verify_ack: true
    ack_timeout_ms: 800
    max_send_attempts: 3
    sck_pin: 18
    miso_pin: 19
    mosi_pin: 23
```

This config validates and compiles under ESPHome `2025.8.3`, with only a deprecation warning:

```text
Using `fan.FAN_SCHEMA` is deprecated and will be removed in ESPHome 2025.11.0
```

The OTA upload succeeded, but after reboot the device landed in OTA-only/safe-mode behavior:

- Ping worked.
- OTA port `3232` worked.
- API port `6053` refused.
- Web port `80` refused.

Then a rebuilt `main` component config with `center_freq_mhz: 433.897` and `deviation_khz: 10` was tried. It also landed in OTA-only/safe-mode. This strongly suggests the QuietCool component initialization is crashing or triggering failed boot under the current rebuilt toolchain, not merely the PR #17 ACK options.

## Important Build Note

The original YAML had:

```yaml
esphome:
  platformio_options:
    platform: espressif32@6.5.0
    platform_packages:
      - framework-arduinoespressif32@~2.0.14
```

That no longer builds in current containers:

```text
UnknownPackageError: Could not find the package with '...framework-arduinoespressif32 @ ~2.0.14'
```

Removing that override lets ESPHome `2025.8.3` compile using Arduino framework `3.2.1`, but the QuietCool component firmware then fails to boot normally on device. This is why serial logs from USB are the next step.

## Files Left On Pi

Original backup before changes:

```text
/home/pi/esphome/config/quiet-cool-dkv1.yaml.backup_before_pr17_ack_20260528_172826
```

Failed PR #17 config snapshot:

```text
/home/pi/esphome/config/quiet-cool-dkv1.pr17-ack-failed-safe-mode-20260528.yaml
```

Failed `main` rebuilt config snapshot:

```text
/home/pi/esphome/config/quiet-cool-dkv1.main-433897-failed-safe-mode-20260528.yaml
```

Current live config:

```text
/home/pi/esphome/config/quiet-cool-dkv1.yaml
```

Current live config is minimal recovery. It should be copied aside before Windows-side flashing if you want a known recovery reference.

## Windows-Side Agent Task

Use the laptop USB serial connection to capture boot logs while flashing the RF firmware. The main unknown is the crash reason during QuietCool component setup.

Recommended sequence:

1. Install or use ESPHome locally on Windows.
2. Connect ESP32 over USB.
3. Identify serial port, for example `COM3`.
4. Build/flash the PR #17 config over serial, not OTA.
5. Keep serial logs open through boot.
6. Capture the first panic/backtrace or watchdog reset reason.

Example commands, adjust `COM3`:

```powershell
esphome compile quiet-cool-dkv1-pr17.yaml
esphome upload quiet-cool-dkv1-pr17.yaml --device COM3
esphome logs quiet-cool-dkv1-pr17.yaml --device COM3
```

If using Docker on Windows, pass the serial device through only if that is already set up. Native ESPHome Python is usually easier for serial.

## PR #17 Candidate YAML Body

Use the existing Wi-Fi, OTA, API, logger, and web server settings from the Pi config. The important RF sections are:

```yaml
spi:
  clk_pin: 18
  mosi_pin: 23
  miso_pin: 19

external_components:
  - source:
      type: git
      url: https://github.com/ccrome/quiet-cool-rf-remote.git
      ref: refs/pull/17/head
    components: [quiet_cool]

fan:
  - platform: quiet_cool
    id: qc_fan
    name: QuietCool Fan
    cs_pin: 22
    gdo0_pin: 13
    gdo2_pin: 12
    remote_id: [0xA7, 0x39, 0xC2, 0x51, 0x0D, 0xFE, 0x84]
    center_freq_mhz: 433.897
    deviation_khz: 10
    preamble_byte: 0x55
    fsctrl0_override: 0x00
    verify_ack: true
    ack_timeout_ms: 800
    max_send_attempts: 3
    sck_pin: 18
    miso_pin: 19
    mosi_pin: 23

button:
  - platform: template
    name: "QC On (Low)"
    on_press:
      - fan.turn_on:
          id: qc_fan
          speed: 1

  - platform: template
    name: "QC On (Medium)"
    on_press:
      - fan.turn_on:
          id: qc_fan
          speed: 2

  - platform: template
    name: "QC On (High)"
    on_press:
      - fan.turn_on:
          id: qc_fan
          speed: 3

  - platform: template
    name: "QC Off"
    on_press:
      - fan.turn_off: qc_fan
```

Do not change `remote_id`.

## What To Look For In Serial Logs

Key successful signs:

```text
CC1101 VERSION READ: 0x14
CC1101 detected!
Setting center frequency to 433.897
CC1101 ready
api: Server
web_server: Address
```

Key failure signs to capture:

- `Guru Meditation Error`
- `LoadProhibited`
- `StoreProhibited`
- `IllegalInstruction`
- watchdog timeout
- repeated safe-mode boot count
- crash during `QuietCoolFan::setup()`
- crash during `ELECHOUSE_cc1101.Init()`
- crash during `setSpiPin`, `setMHZ`, `SpiWriteReg`, or `awaitAck`

If a backtrace appears, preserve the entire boot log from reset through crash.

## Likely Debug Direction

Because both PR #17 and rebuilt `main` failed normal boot, suspect one of:

- Arduino framework 3.2.1 incompatibility with the external component or bundled CC1101 driver.
- SPI/GPIO initialization behavior changed versus the old pinned Arduino 2.x environment.
- Component init blocks/crashes before API/web start.
- CC1101 absent/wiring issue causing repeated failed setup under new code path, though the old device had previously been usable enough for OTA.

PR #17 ACK itself is less likely to be the immediate boot-loop trigger because ACK receive is only used after sending a command; the device failed before API/web came up.

## Attic Pairing Plan After Firmware Boots Normally

Only go to the attic after the Windows-side flash shows:

- API or serial logs are stable after boot.
- CC1101 detected.
- No reboot loop for at least 60 seconds.
- Home Assistant or ESPHome can trigger the buttons.

Attempt 1 settings:

```yaml
center_freq_mhz: 433.897
deviation_khz: 10
preamble_byte: 0x55
fsctrl0_override: 0x00
verify_ack: true
```

In attic:

1. Put QuietCool hub into Pair Mode.
2. Press `QC Off` or `QC On (Low)` 2-3 times.
3. Watch logs for `awaitAck: got ACK` or `send OK`.
4. If fan responds or ACK appears, leave attic and test from desk:
   - low
   - medium
   - high
   - off

Fallback RF attempts, one attic Pair run per firmware:

```yaml
fsctrl0_override: 0xF3
```

then:

```yaml
fsctrl0_override: 0x08
```

Do not change `remote_id` during these attempts.

If ACK appears but fan behavior is wrong, stop frequency tuning and inspect packet behavior or try PR #12. If no ACK and no fan response after the three variants, capture the physical remote with SDR/CC1101 RX rather than broad frequency sweeping.
