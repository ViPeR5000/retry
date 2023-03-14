"""Retry integration."""
from __future__ import annotations

import datetime
import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_SERVICE, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import InvalidStateError, ServiceNotFound
from homeassistant.helpers import config_validation as cv, event, template
from homeassistant.helpers.service import async_extract_referenced_entity_ids
import homeassistant.util.dt as dt_util

from .const import ATTR_RETRIES, DOMAIN, LOGGER, SERVICE

EXPONENTIAL_BACKOFF_BASE = 2

SERVICE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_SERVICE): cv.string,
        vol.Required(ATTR_RETRIES, default=7): cv.positive_int,
    },
    extra=vol.ALLOW_EXTRA,
)


async def async_setup_entry(hass: HomeAssistant, _: ConfigEntry) -> bool:
    """Set up domain."""

    async def async_call(service_call: ServiceCall) -> None:
        """Call service with background retries."""
        data = service_call.data.copy()

        retry_service = template.Template(data[ATTR_SERVICE], hass).async_render(
            parse_result=False
        )
        domain, service = retry_service.lower().split(".")
        del data[ATTR_SERVICE]
        if not hass.services.has_service(domain, service):
            raise ServiceNotFound(domain, service)

        max_retries = data[ATTR_RETRIES]
        del data[ATTR_RETRIES]

        schema = hass.services.async_services()[domain][service].schema
        if schema:
            schema(data)

        retries = 1
        delay = 1
        call = f"{domain}.{service}(data={data})"
        LOGGER.debug("Calling: %s", call)

        async def async_check_entities_avaliability() -> None:
            """Verify that all entities are avaliable."""
            entities = async_extract_referenced_entity_ids(hass, service_call)
            for entity in entities.referenced | entities.indirectly_referenced:
                state = hass.states.get(entity)
                if state is None or state.state == STATE_UNAVAILABLE:
                    raise InvalidStateError(f"{entity} is not avaliable")

        @callback
        async def async_retry(*_) -> bool:
            """One service call attempt."""
            nonlocal max_retries
            nonlocal retries
            nonlocal delay
            try:
                await hass.services.async_call(
                    domain, service, data, True, service_call.context
                )
                await async_check_entities_avaliability()
                LOGGER.debug("Succeeded: %s", call)
                return
            except Exception as ex:  # pylint: disable=broad-except
                LOGGER.warning(
                    "%s attempt #%d failed: (%s) %s",
                    call,
                    retries,
                    ex.__class__.__name__,
                    ex,
                )
            if retries == max_retries:
                LOGGER.error("Failed: %s", call)
                return
            next_retry = dt_util.now() + datetime.timedelta(seconds=delay)
            delay *= EXPONENTIAL_BACKOFF_BASE
            retries += 1
            event.async_track_point_in_time(hass, async_retry, next_retry)

        await async_retry()

    hass.services.async_register(DOMAIN, SERVICE, async_call, SERVICE_SCHEMA)
    return True


async def async_unload_entry(hass: HomeAssistant, _: ConfigEntry) -> bool:
    """Unload a config entry."""
    hass.services.async_remove(DOMAIN, SERVICE)
    return True