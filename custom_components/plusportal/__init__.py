"""The PlusPortal integration.

Reads metered electricity consumption out of a PlusPortal customer portal and
feeds it into Home Assistant's long-term statistics, so it appears in the
Energy dashboard under the timestamps it was actually measured at.
"""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .coordinator import PlusPortalConfigEntry, PlusPortalCoordinator

PLATFORMS: list[Platform] = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: PlusPortalConfigEntry) -> bool:
    """Set up PlusPortal from a config entry."""
    coordinator = PlusPortalCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload_on_option_change))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: PlusPortalConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_shutdown_client()
    return unloaded


async def _async_reload_on_option_change(hass: HomeAssistant, entry: PlusPortalConfigEntry) -> None:
    """Reload when the tariff or polling interval changes."""
    await hass.config_entries.async_reload(entry.entry_id)
