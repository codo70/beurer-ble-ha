# HA openScale Beurer

Home Assistant custom integration for **Beurer / Sanitas Bluetooth scales and blood pressure monitors**. It talks to the devices directly over Bluetooth, through the local adapter or ESPHome Bluetooth proxies. It needs no cloud, no app and no ESP firmware of its own.

The scale protocols follow the reverse-engineered implementations in [openScale](https://github.com/oliexdev/openScale). Blood pressure monitors use the standard Bluetooth Blood Pressure profile.

> **Status:** All three device families are tested against simulated devices only. None has been tested against real hardware yet.

## Supported devices

| Family | Devices | Notes |
|---|---|---|
| Beurer/Sanitas protocol | BF700, BF710, BF800, Runtastic Libra, Sanitas SBF70, SilverCrest SBF75, Crane | Users and stored measurements are read without any code. |
| Standard scale profile | BF105, BF720, BF915, BF950, BF500, BF600, BF850, BF1000 (basic values), Sanitas SBF72, SBF73, SBF76, SBF77 | Stored data needs a **consent code** per user (see below). |
| Blood pressure | BM 85 and other Beurer BM/BC monitors with Bluetooth, plus any monitor with the standard Blood Pressure service (0x1810) | Reads all stored readings. Handles the BM 85's swapped pulse bytes. |

Not supported: BF450 (non-standard data layout) and the BF1000 segment values.

## How it works

When a device wakes up (you step on the scale or finish a blood pressure reading), it advertises and Home Assistant connects.

- **Scales:** the integration sets the clock, reads the battery and the user list, and downloads the stored measurements. It then listens for 30 s in case a weighing is still running, fetches once more and disconnects.
- **Blood pressure monitors:** the integration receives all stored readings. The monitor then disconnects on its own.
- **Nothing is deleted on the device,** so the Beurer HealthManager app keeps working.
- **Between syncs** the integration waits at least 90 s.

## Entities

Sensors are created per person (scale user or monitor memory) as soon as that person has a value. Each sensor is named after the person, for example `COD Gewicht` or `Benutzer 1 Systolisch`.

- **Scales:**
  - weight, body fat, body water and muscle share
  - bone mass, muscle mass and fat-free mass (on models that report them)
  - BMI and BMR (on models that report them)
  - impedance (diagnostic)
  - last measurement
- **Blood pressure monitors:**
  - systolic and diastolic pressure
  - pulse
  - mean arterial pressure (disabled by default)
  - last measurement
  - The systolic sensor's attributes also show irregular pulse, body movement and cuff fit.
- **Every device:** a battery sensor (diagnostic).

Every new measurement also fires the event `beurer_ble_measurement` with all its values and the mapped `ha_person`. Use it in automations, or to log readings that arrive several at a time, for example blood pressure history.

## Assigning measurements to Home Assistant persons

Under *Settings → Devices & services → Beurer BLE → Configure* every person found on the device appears, for example `COD [1]` or `Benutzer 1 [1]`. Pick an HA `person` for each one.

**Effects of a mapping:**
- Each HA person gets a device of their own, named after the person, for example "Christoph". The scale and the blood pressure monitor both put their sensors there, such as `sensor.christoph_gewicht` and `sensor.christoph_blutdruck_systolisch`.
- When you change a mapping, the integration creates new entities and removes the old ones. The old ones' history stays in the recorder but is no longer linked.
- The battery sensor stays on the physical device.

**Assigning guest weighings by weight** (scales, on by default): the scale may not recognise a user and report a guest. The integration then gives the weighing to the person whose last weight is within 3 kg. The next candidate must be at least 2 kg further away, otherwise the weighing stays a guest weighing.

The device still decides who measured. Home Assistant only translates its user into an HA person, so choosing the wrong memory on the monitor still files the reading under the wrong person.

## Consent codes (standard-profile scales only)

These scales release stored data for a user only after a 4-digit consent code. The app that created the user on the scale chose that code.

Enter the codes under *Settings → Devices & services → Beurer BLE → Configure* as `slot:code`, for example `1:1234, 2:5678`. The slot numbers appear in the debug log after the first connection.

Without a code you still get the measurements taken while Home Assistant is connected.

## Installation

- **Marketplace / HACS:** add this repository as a custom repository (category *Integration*).
- **Manual:** copy `custom_components/beurer_ble` to `/config/custom_components/`.

Then restart Home Assistant, wake the device and confirm it under *Settings → Devices & services*.

## Notes

- Devices accept only **one** connection at a time. Close HealthManager on your phone, otherwise it may grab the device first.
- Some devices require pairing. The integration tries to pair once on its own if the device demands encryption.
- Debug logging:
  ```yaml
  logger:
    logs:
      custom_components.beurer_ble: debug
  ```

## Tests

```bash
python3 tests/test_session.py custom_components/beurer_ble
```

This runs every family against fake devices. It needs neither Home Assistant nor bleak.

## License

MIT. The protocol knowledge comes from openScale (GPL-3.0); the code here is an independent Python implementation.
