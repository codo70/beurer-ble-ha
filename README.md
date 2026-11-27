# HA openScale Beurer

Home Assistant custom integration for **Beurer BF700 / BF710 / BF800** body composition scales (also Sanitas / Runtastic variants). It talks to the scale directly over Bluetooth, through the local adapter or ESPHome Bluetooth proxies. It needs no cloud, no app and no ESP firmware of its own.

The protocol follows the reverse-engineered implementation in [openScale](https://github.com/oliexdev/openScale).

> **Status:** It has only been tested against a simulated scale. It has not been tested against real hardware yet.

## How it works

1. When you step on the scale it wakes up and advertises. Home Assistant discovers it.
2. The integration connects (active connection, via any connectable proxy). It then:
   - sets the scale clock,
   - reads the battery and the user list,
   - downloads the stored measurements for each user.
3. It stays connected for 30 s so a measurement that is still running is caught too, fetches again, then disconnects. Between syncs it waits at least 90 s.
4. Measurements are **not deleted** on the scale, so the Beurer HealthManager app keeps working.

## Entities

Per scale user, named after the initials stored on the scale:

| Sensor | Unit |
|---|---|
| Weight (Gewicht) | kg |
| Body fat (Körperfett) | % |
| Body water (Körperwasser) | % |
| Muscle (Muskelanteil) | % |
| Bone mass (Knochenmasse) | kg |
| Impedance (diagnostic) | Ω |
| Last measurement | timestamp |

The scale itself also gets a battery sensor (diagnostic).

New users show up automatically after the next sync.

## Installation

- **Marketplace / HACS:** add this repository as a custom repository (category *Integration*).
- **Manual:** copy `custom_components/beurer_bf700` to `/config/custom_components/`.

Then restart Home Assistant, step on the scale and confirm the discovered device under *Settings → Devices & services*.

## Notes

- The scale accepts only **one** connection. Close HealthManager on your phone, otherwise it may grab the scale first.
- Users must be created on the scale or with the app first. The integration only reads them.
- BMI, BMR and AMR are not decoded. Calculate them with template sensors if you need them.
- Debug logging:
  ```yaml
  logger:
    logs:
      custom_components.beurer_bf700: debug
  ```

## Tests

```bash
python3 tests/test_session.py custom_components/beurer_bf700
```

This runs the session logic against a fake scale. It does not need Home Assistant.

## License

MIT
