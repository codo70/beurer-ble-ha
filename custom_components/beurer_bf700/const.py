"""Constants for the Beurer BF700 integration."""

from __future__ import annotations

DOMAIN = "beurer_bf700"

SERVICE_UUID = "0000ffe0-0000-1000-8000-00805f9b34fb"
CHAR_UUID = "0000ffe1-0000-1000-8000-00805f9b34fb"

# Do not reconnect sooner than this after a sync finished (seconds).
COOLDOWN = 90
# After the first download stay connected this long and listen for a
# measurement that finishes while we are connected, then download again.
LISTEN_TIME = 30

SIGNAL_UPDATE = f"{DOMAIN}_update_{{}}"
SIGNAL_NEW_USER = f"{DOMAIN}_new_user_{{}}"
