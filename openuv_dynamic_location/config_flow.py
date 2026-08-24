"""Config flow for OpenUV Dynamic Location."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONFIG_ENTRY_VERSION,
    CONF_DEBOUNCE_SECONDS,
    CONF_DISTANCE_KM,
    CONF_LATITUDE_ENTITY,
    CONF_LONGITUDE_ENTITY,
    CONF_OPENUV_ENTRY_ID,
    DEFAULT_DEBOUNCE_SECONDS,
    DEFAULT_DISTANCE_KM,
    DEFAULT_LATITUDE_ENTITY,
    DEFAULT_LONGITUDE_ENTITY,
    DOMAIN,
    OPENUV_DOMAIN,
)


def _schema(defaults: dict[str, Any]) -> vol.Schema:
    """Build the shared schema for the user and options steps."""

    return vol.Schema(
        {
            vol.Required(
                CONF_LATITUDE_ENTITY,
                default=defaults.get(
                    CONF_LATITUDE_ENTITY, DEFAULT_LATITUDE_ENTITY
                ),
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            ),
            vol.Required(
                CONF_LONGITUDE_ENTITY,
                default=defaults.get(
                    CONF_LONGITUDE_ENTITY, DEFAULT_LONGITUDE_ENTITY
                ),
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            ),
            vol.Required(
                CONF_DISTANCE_KM,
                default=defaults.get(
                    CONF_DISTANCE_KM, DEFAULT_DISTANCE_KM
                ),
            ): vol.All(
                vol.Coerce(float),
                vol.Range(min=0.1, max=1000.0),
            ),
            vol.Required(
                CONF_DEBOUNCE_SECONDS,
                default=defaults.get(
                    CONF_DEBOUNCE_SECONDS, DEFAULT_DEBOUNCE_SECONDS
                ),
            ): vol.All(
                vol.Coerce(float),
                vol.Range(min=0.0, max=60.0),
            ),
            vol.Optional(
                CONF_OPENUV_ENTRY_ID,
                default=defaults.get(CONF_OPENUV_ENTRY_ID),
            ): selector.ConfigEntrySelector(
                selector.ConfigEntrySelectorConfig(
                    integration=OPENUV_DOMAIN,
                )
            ),
        }
    )


class OpenUvDynamicLocationConfigFlow(
    config_entries.ConfigFlow,
    domain=DOMAIN,
):
    """Handle configuration of OpenUV Dynamic Location."""

    VERSION = CONFIG_ENTRY_VERSION

    async def async_step_user(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> config_entries.ConfigFlowResult:
        """Handle the initial configuration step."""

        if user_input is not None:
            await self.async_set_unique_id(
                f"{user_input[CONF_LATITUDE_ENTITY]}:"
                f"{user_input[CONF_LONGITUDE_ENTITY]}:"
                f"{user_input.get(CONF_OPENUV_ENTRY_ID) or 'auto'}"
            )

            self._abort_if_unique_id_configured()

            return self.async_create_entry(
                title="OpenUV Dynamic Location",
                data=user_input,
            )

        return self.async_show_form(
            step_id="user",
            data_schema=_schema({}),
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> OpenUvDynamicLocationOptionsFlow:
        """Get the options flow for this handler."""

        return OpenUvDynamicLocationOptionsFlow()


class OpenUvDynamicLocationOptionsFlow(config_entries.OptionsFlow):
    """Handle options for OpenUV Dynamic Location.

    This lets the GPS entities, movement threshold, debounce time,
    and target OpenUV instance be changed after setup, without
    deleting and re-adding the integration.
    """

    async def async_step_init(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> config_entries.ConfigFlowResult:
        """Manage the options."""

        if user_input is not None:
            return self.async_create_entry(data=user_input)

        current = {
            **self.config_entry.data,
            **self.config_entry.options,
        }

        return self.async_show_form(
            step_id="init",
            data_schema=_schema(current),
        )
