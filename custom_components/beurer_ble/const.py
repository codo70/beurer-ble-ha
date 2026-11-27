"""Constants for the Beurer BLE integration."""

from __future__ import annotations

DOMAIN = "beurer_ble"

CONF_FAMILY = "family"
CONF_MODEL = "model"
CONF_CONSENT = "consent_codes"

# Do not reconnect sooner than this after a sync finished (seconds).
COOLDOWN = 90
# Scales: after the first download stay connected this long and listen for a
# measurement that finishes while we are connected, then download again.
LISTEN_TIME = 30

SIGNAL_UPDATE = f"{DOMAIN}_update_{{}}"
