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
CONF_PERSONS = "persons"  # {device person id: HA person entity}
CONF_AUTO_ASSIGN = "auto_assign"  # assign guest weighings by weight

# Guest weighing goes to the person with the closest last weight if it is within
# MAX_DIFF and the next candidate is at least MARGIN further away.
AUTO_ASSIGN_MAX_DIFF = 3.0
AUTO_ASSIGN_MARGIN = 2.0
