# Beurer BLE HA

<img src="custom_components/beurer_ble/brand/logo@2x.png" alt="Beurer BLE" width="420">

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/docs/faq/custom_repositories)

**Deutsch** · [English below](#english)

Home-Assistant-Integration für **Bluetooth-Waagen und Blutdruckmessgeräte von Beurer und Sanitas**. Sie spricht direkt per Bluetooth mit den Geräten, über den Bluetooth-Adapter von HA oder über ESPHome-Bluetooth-Proxys. Es braucht keine Cloud, keine App und keine eigene ESP-Firmware.

Die Waagen-Protokolle folgen den nachgebauten Umsetzungen von [openScale](https://github.com/oliexdev/openScale). Blutdruckmessgeräte nutzen das Standard-Bluetooth-Profil für Blutdruck.

> **Status:** Beta. Die BF700 ist mit einer echten Waage getestet: Verbindung, Benutzer anlegen und Live-Wiegungen. Die anderen Gerätefamilien sind bisher nur gegen nachgebaute Geräte getestet.

## Unterstützte Geräte

| Familie | Geräte | Hinweis |
|---|---|---|
| Beurer/Sanitas-Protokoll | BF700, BF710, BF800, Runtastic Libra, Sanitas SBF70, SilverCrest SBF75, Crane | Benutzer und gespeicherte Messungen werden ohne Code gelesen. Benutzer lassen sich aus HA anlegen. |
| Standard-Waagenprofil | BF105, BF720, BF915, BF950, BF500, BF600, BF850, BF1000 (Grundwerte), Sanitas SBF72, SBF73, SBF76, SBF77 | Gespeicherte Daten brauchen pro Person einen **Freigabecode** (siehe unten). |
| Blutdruck | BM 85 und andere Beurer BM/BC mit Bluetooth, dazu jedes Gerät mit dem Standard-Blutdruckdienst (0x1810) | Liest alle gespeicherten Messungen. Die vertauschten Puls-Bytes des BM 85 werden berücksichtigt. |

Nicht unterstützt sind die BF450 (abweichendes Datenformat) und die Segmentwerte der BF1000.

## Installation

- **Marketplace / HACS:** Dieses Repository als benutzerdefiniertes Repository hinzufügen (Kategorie *Integration*) und installieren.
- **Manuell:** `custom_components/beurer_ble` nach `/config/custom_components/` kopieren.

Danach Home Assistant neu starten, auf die Waage steigen bzw. eine Messung machen und das gefundene Gerät unter *Einstellungen → Geräte & Dienste* bestätigen.

## So funktioniert es

Sobald ein Gerät aufwacht, weil jemand auf die Waage steigt oder eine Blutdruckmessung fertig ist, sendet es per Bluetooth, und Home Assistant verbindet sich.

- **Waagen:** Die Integration stellt die Uhr, liest Batterie und Benutzerliste und holt die gespeicherten Messungen. Danach bleibt sie 30 s verbunden, falls noch gewogen wird, holt noch einmal ab und trennt.
- **Blutdruckmessgeräte:** Die Integration empfängt alle gespeicherten Messungen. Das Gerät trennt danach von selbst.
- **Am Gerät wird nichts gelöscht.** Die Beurer-App HealthManager funktioniert weiter.
- **Zwischen zwei Abrufen** wartet die Integration mindestens 90 s.

## Entitäten

Sensoren entstehen pro Person (Benutzer auf der Waage bzw. Speicher am Blutdruckgerät), sobald diese Person einen Wert hat. Sie heißen nach der Person, z. B. `COD Gewicht` oder `Benutzer 1 Blutdruck systolisch`.

- **Waagen:**
  - Gewicht, Körperfett, Körperwasser, Muskelanteil
  - Knochenmasse, Muskelmasse, fettfreie Masse (wenn das Modell sie liefert)
  - BMI und Grundumsatz (wenn das Modell sie liefert)
  - Impedanz (Diagnose)
  - letzte Wiegung
- **Blutdruckmessgeräte:**
  - Blutdruck systolisch und diastolisch
  - Puls
  - Mitteldruck (standardmäßig deaktiviert)
  - letzte Blutdruckmessung
  - Beim systolischen Wert stehen als Attribute zusätzlich unregelmäßiger Puls, Bewegung und Manschettensitz.
- **Jedes Gerät:** Batteriestand (Diagnose).

Jede neue Messung löst außerdem das Ereignis `beurer_ble_measurement` mit allen Werten und der zugeordneten `ha_person` aus. Das eignet sich für Automationen oder um mehrere auf einmal übertragene Messungen zu protokollieren, z. B. den Blutdruckverlauf.

## Messwerte Personen in Home Assistant zuordnen

Unter *Einstellungen → Geräte & Dienste → Beurer BLE → Konfigurieren* erscheint jede Person, die auf dem Gerät gefunden wurde, z. B. `COD [101]` oder `Benutzer 1 [1]`. Jeder lässt sich eine HA-Person (`person.…`) zuordnen.

**Was die Zuordnung bewirkt:**
- Jede HA-Person bekommt ein eigenes Gerät mit ihrem Namen, z. B. „Christoph“. Waage und Blutdruckgerät legen ihre Sensoren dort ab, etwa `sensor.christoph_gewicht` und `sensor.christoph_blutdruck_systolisch`.
- Bei einer geänderten Zuordnung legt die Integration neue Entitäten an und entfernt die alten. Deren Verlauf bleibt in der Datenbank, hängt aber nicht mehr an der Person.
- Der Batteriestand bleibt beim Gerät selbst.

**Gast-Wiegungen nach Gewicht zuordnen** (Waagen, standardmäßig an): Erkennt die Waage niemanden und meldet einen Gast, bekommt die Wiegung die Person, deren letztes Gewicht höchstens 3 kg abweicht. Der nächste Kandidat muss mindestens 2 kg weiter weg liegen, sonst bleibt es eine Gast-Wiegung.

Wer gemessen hat, entscheidet weiterhin das Gerät. Home Assistant übersetzt nur dessen Benutzer in eine HA-Person. Wer am Blutdruckgerät den falschen Speicher wählt, landet also auch in HA bei der falschen Person.

## Benutzer auf der Waage aus Home Assistant anlegen (BF700-Familie)

Nach einem Zurücksetzen hat die Waage keine Benutzer mehr. Dann die Aktion **„Benutzer auf Waage anlegen“** (`beurer_ble.create_scale_user`, unter *Entwicklerwerkzeuge → Aktionen*) aufrufen mit:
- der Waage
- dem Kürzel (bis 3 Zeichen)
- Geburtsdatum, Größe, Geschlecht und Aktivität (1–5)
- optional einer HA-Person

Danach auf die Waage steigen. Beim nächsten Verbinden legt die Integration den Benutzer an und ordnet ihn, falls angegeben, gleich der HA-Person zu.

Einen neuen Benutzer erkennt die Waage erst nach einer **Referenzmessung**. Solange die fehlt, fordert die Integration sie bei jeder Verbindung an und wartet bis zu 2 Minuten:
1. Draufsteigen, wiegen lassen und absteigen.
2. Wenn die Waage dazu auffordert, barfuß wieder draufsteigen und stehen bleiben, bis Fett, Wasser und Muskeln angezeigt werden.

Wiegungen ohne bekannten Benutzer, z. B. auf einer leeren Waage, werden als Gast-Wiegung gespeichert.

## Freigabecodes (nur Standard-Waagen)

Diese Waagen geben gespeicherte Daten einer Person nur nach einem vierstelligen Freigabecode heraus. Den Code hat die App festgelegt, die die Person auf der Waage angelegt hat.

Die Codes unter *Einstellungen → Geräte & Dienste → Beurer BLE → Konfigurieren* als `Platz:Code` eintragen, z. B. `1:1234, 2:5678`. Die Platznummern stehen nach der ersten Verbindung im Debug-Log.

Ohne Code kommen nur die Messungen an, die gemacht werden, während Home Assistant verbunden ist.

## Hinweise

- Die Geräte erlauben immer nur **eine** Verbindung. HealthManager auf dem Handy schließen, sonst schnappt es sich das Gerät zuerst.
- Manche Geräte verlangen eine Kopplung. Verlangt ein Gerät Verschlüsselung, versucht die Integration einmal selbst zu koppeln.
- Debug-Log einschalten:
  ```yaml
  logger:
    logs:
      custom_components.beurer_ble: debug
  ```

## Tests

```bash
python3 tests/test_session.py custom_components/beurer_ble
```

Testet alle Gerätefamilien gegen nachgebaute Geräte. Braucht weder Home Assistant noch bleak.

## Lizenz

MIT. Das Protokollwissen stammt aus openScale (GPL-3.0), der Code hier ist eine eigenständige Python-Umsetzung.

---

## English

Home Assistant custom integration for **Beurer / Sanitas Bluetooth scales and blood pressure monitors**. It talks to the devices directly over Bluetooth, through the local adapter or ESPHome Bluetooth proxies. It needs no cloud, no app and no ESP firmware of its own.

The scale protocols follow the reverse-engineered implementations in [openScale](https://github.com/oliexdev/openScale). Blood pressure monitors use the standard Bluetooth Blood Pressure profile.

> **Status:** Beta. The BF700 has been tested with a real scale: connection, creating a user and live weighings. The other device families are tested against simulated devices only.

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

## Creating a user on the scale from Home Assistant (BF700 family)

After a reset the scale has no users. Call the action **`beurer_ble.create_scale_user`** (*Developer tools → Actions*) with:
- the scale
- initials (up to 3 characters)
- birthday, height, gender and activity
- optionally an HA person

Then step on the scale. The integration creates the user at the next connection and, if a person was given, maps the user to that HA person.

The scale only recognises a new user after a **reference measurement**. Until it has one, the integration requests it at every connection and waits up to 2 minutes:
1. Step on, get weighed and step off.
2. When the scale asks for it, step on again barefoot and stay until fat, water and muscle are shown.

Weighings without a known user (for example on an empty scale) are kept as a guest weighing.

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
