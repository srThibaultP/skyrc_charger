"""Keep the bulky voltage curve payload out of the recorder database.

A curve is 240 samples, twice over (mV and V), attached to a sensor that can
be refreshed every 30 seconds while a slot is working. Recording that on
every state change would grow the database by megabytes a day for data the
history graph never reads.
"""

from __future__ import annotations

from homeassistant.core import HomeAssistant, callback

EXCLUDED_ATTRIBUTES = {"samples_mv", "samples_v"}


@callback
def exclude_attributes(hass: HomeAssistant) -> set[str]:
    """Attributes the recorder should not store."""
    return EXCLUDED_ATTRIBUTES
