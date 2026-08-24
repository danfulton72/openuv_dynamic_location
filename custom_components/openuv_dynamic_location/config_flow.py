"""Config flow for OpenUV Dynamic Location."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_DEBOUNCE_SECONDS,
    CONF_DISTANCE_KM,
    CONF_LATITUDE_ENTITY,
    CONF_LONGITUDE_ENTITY,
    CONF_OPENUV_ENTRY_ID,
    CONFIG_ENTRY_VERSION,
    DEFAULT_DEBOUNCE_SECONDS,
    DEFAULT_DISTANCE_KM,
    DEFAULT_LATITUDE_ENTITY,
    DEFAULT_LONGITUDE_ENTITY,
    DOMAIN,
    OPENUV_DOMAIN,
)


def _config_schema(defaults: dict[str, Any]) -> vol.Schema:
    """Build the setup/reconfigure schema."""

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
                CONF_OPENUV_ENTRY_ID,
                default=defaults.get(CONF_OPENUV_ENTRY_ID),
            ): selector.ConfigEntrySelector(
                selector.ConfigEntrySelectorConfig(integration=OPENUV_DOMAIN)
            ),
        }
    )


def _options_schema(defaults: dict[str, Any]) -> vol.Schema:
    """Build the tunable-options schema."""

    return vol.Schema(
        {
            vol.Required(
                CONF_DISTANCE_KM,
                default=defaults.get(
                    CONF_DISTANCE_KM, DEFAULT_DISTANCE_KM
                ),
            ): vol.All(vol.Coerce(float), vol.Range(min=0.1, max=1000.0)),
            vol.Required(
                CONF_DEBOUNCE_SECONDS,
                default=defaults.get(
                    CONF_DEBOUNCE_SECONDS, DEFAULT_DEBOUNCE_SECONDS
                ),
            ): vol.All(vol.Coerce(float), vol.Range(min=0.0, max=60.0)),
        }
    )


class OpenUvDynamicLocationConfigFlow(
    config_entries.ConfigFlow, domain=DOMAIN
):
    """Handle configuration."""

    VERSION = CONFIG_ENTRY_VERSION

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Handle initial setup."""

        if not self.hass.config_entries.async_entries(OPENUV_DOMAIN):
            return self.async_abort(reason="target_not_configured")

        if user_input is not None:
            target_id = user_input[CONF_OPENUV_ENTRY_ID]
            await self.async_set_unique_id(target_id)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title="OpenUV Dynamic Location",
                data=user_input,
                options={
                    CONF_DISTANCE_KM: DEFAULT_DISTANCE_KM,
                    CONF_DEBOUNCE_SECONDS: DEFAULT_DEBOUNCE_SECONDS,
                },
            )

        return self.async_show_form(
            step_id="user", data_schema=_config_schema({})
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Reconfigure GPS entities or target."""

        entry = self._get_reconfigure_entry()
        if user_input is not None:
            target_id = user_input[CONF_OPENUV_ENTRY_ID]
            for other in self._async_current_entries():
                if (
                    other.entry_id != entry.entry_id
                    and other.unique_id == target_id
                ):
                    return self.async_abort(reason="already_configured")

            self.hass.config_entries.async_update_entry(
                entry, data=user_input, unique_id=target_id
            )
            await self.hass.config_entries.async_reload(entry.entry_id)
            return self.async_abort(reason="reconfigure_successful")

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_config_schema(dict(entry.data)),
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> OpenUvDynamicLocationOptionsFlow:
        """Return the options flow."""

        return OpenUvDynamicLocationOptionsFlow()


class OpenUvDynamicLocationOptionsFlow(config_entries.OptionsFlow):
    """Handle tunable options."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Manage options."""

        if user_input is not None:
            return self.async_create_entry(data=user_input)

        return self.async_show_form(
            step_id="init",
            data_schema=_options_schema(dict(self.config_entry.options)),
        )
