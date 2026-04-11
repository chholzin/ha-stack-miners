"""Tests for the Stack Miners config flow — manual miner addition."""

from unittest.mock import MagicMock, patch

import pytest

# conftest stubs HA before any project imports
from custom_components.stack_miners.config_flow import (
    StackMinersConfigFlow,
    StackMinersOptionsFlow,
    _manual_miners_schema,
)
from custom_components.stack_miners.const import (
    CONF_MINERS,
    CONF_MINER_ENTITY_ID,
    CONF_MINER_NAME,
    CONF_MINER_POWER_W,
    DEFAULT_MINER_POWER_W,
    DOMAIN,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_flow(hass) -> StackMinersConfigFlow:
    flow = StackMinersConfigFlow()
    flow.hass = hass
    flow.context = {}
    # Stub abort / show_form / create_entry so we can inspect the result dict
    flow.async_abort = lambda reason: {"type": "abort", "reason": reason}
    flow.async_show_form = lambda **kw: {"type": "form", **kw}
    flow.async_create_entry = lambda title, data: {"type": "create_entry", "title": title, "data": data}
    # Required by async_set_unique_id / _abort_if_unique_id_configured
    flow._async_abort_entries_match = MagicMock(return_value=False)
    flow.async_set_unique_id = lambda uid: None
    flow._abort_if_unique_id_configured = lambda: None
    return flow


def _make_options_flow(hass, existing_miners=None) -> StackMinersOptionsFlow:
    flow = StackMinersOptionsFlow()
    flow.hass = hass

    entry = MagicMock()
    entry.data = {
        "grid_sensor_entity_id": "sensor.grid",
        "hysteresis_w": 100,
        "rolling_samples": 3,
        "min_on_time_s": 60,
        "min_off_time_s": 60,
        "miners": existing_miners or [],
    }
    entry.options = {}
    flow.config_entry = entry

    flow.async_abort = lambda reason: {"type": "abort", "reason": reason}
    flow.async_show_form = lambda **kw: {"type": "form", **kw}
    flow.async_create_entry = lambda title, data: {"type": "create_entry", "title": title, "data": data}
    return flow


# ---------------------------------------------------------------------------
# _manual_miners_schema
# ---------------------------------------------------------------------------

class TestManualMinersSchema:
    def test_schema_accepts_empty_list(self):
        schema = _manual_miners_schema()
        result = schema({"manual_miners": []})
        assert result["manual_miners"] == []

    def test_schema_accepts_entity_ids(self):
        schema = _manual_miners_schema()
        result = schema({"manual_miners": ["switch.plug_a", "switch.plug_b"]})
        assert result["manual_miners"] == ["switch.plug_a", "switch.plug_b"]

    def test_schema_uses_defaults(self):
        import voluptuous as vol
        schema = _manual_miners_schema(defaults=["switch.existing"])
        # Find the Optional key for "manual_miners" and check its default
        key = next(k for k in schema.schema if isinstance(k, vol.Optional) and k.schema == "manual_miners")
        assert key.default() == ["switch.existing"]


# ---------------------------------------------------------------------------
# Config flow: add_manual_miners step
# ---------------------------------------------------------------------------

class TestConfigFlowAddManualMiners:

    @pytest.mark.asyncio
    async def test_shows_form_when_no_input(self, hass):
        """The step renders a form when called without user_input."""
        flow = _make_flow(hass)
        flow._discovered = {}
        flow._selected_ids = []

        result = await flow.async_step_add_manual_miners(user_input=None)

        assert result["type"] == "form"
        assert result["step_id"] == "add_manual_miners"

    @pytest.mark.asyncio
    async def test_error_when_no_miners_at_all(self, hass):
        """Error is shown when both hass-miner list and manual list are empty."""
        flow = _make_flow(hass)
        flow._discovered = {}
        flow._selected_ids = []

        result = await flow.async_step_add_manual_miners(user_input={"manual_miners": []})

        assert result["type"] == "form"
        assert result["errors"] == {"base": "no_miners_selected"}

    @pytest.mark.asyncio
    async def test_proceeds_with_manual_only(self, hass):
        """Config entry is created when only manual switches are provided."""
        flow = _make_flow(hass)
        flow._data = {
            "grid_sensor_entity_id": "sensor.grid",
            "hysteresis_w": 100,
            "rolling_samples": 3,
            "min_on_time_s": 60,
            "min_off_time_s": 60,
        }
        flow._discovered = {}
        flow._selected_ids = []
        flow._miners = []
        flow._pending = []

        # Stub configure_miner to skip through immediately
        async def _fast_configure(user_input=None):
            if user_input is not None or not flow._pending:
                flow._data[CONF_MINERS] = flow._miners
                return flow.async_create_entry(title="Stack Miners", data=flow._data)
            return flow.async_show_form(step_id="configure_miner", data_schema=MagicMock())

        flow.async_step_configure_miner = _fast_configure

        result = await flow.async_step_add_manual_miners(
            user_input={"manual_miners": ["switch.my_plug"]}
        )

        # Should have moved on to configure_miner
        assert flow._selected_ids == ["switch.my_plug"]
        assert flow._pending == ["switch.my_plug"]
        # Manual miner was added to _discovered with defaults
        assert "switch.my_plug" in flow._discovered
        assert flow._discovered["switch.my_plug"]["power_w"] == DEFAULT_MINER_POWER_W

    @pytest.mark.asyncio
    async def test_merges_hass_miner_and_manual(self, hass):
        """Both hass-miner and manual selections end up in _selected_ids."""
        flow = _make_flow(hass)
        flow._discovered = {
            "switch.hass_miner": {"entity_id": "switch.hass_miner", "name": "S9", "power_w": 1400}
        }
        flow._selected_ids = ["switch.hass_miner"]
        flow._miners = []
        flow._pending = []

        # Capture what _pending looks like after the step resolves
        captured = {}

        async def _capture_configure(user_input=None):
            captured["pending"] = list(flow._pending)
            captured["selected"] = list(flow._selected_ids)
            return {"type": "form", "step_id": "configure_miner"}

        flow.async_step_configure_miner = _capture_configure

        await flow.async_step_add_manual_miners(
            user_input={"manual_miners": ["switch.smart_plug"]}
        )

        assert "switch.hass_miner" in captured["selected"]
        assert "switch.smart_plug" in captured["selected"]
        assert len(captured["selected"]) == 2

    @pytest.mark.asyncio
    async def test_no_duplicate_if_manual_overlaps_hass_miner(self, hass):
        """If the user selects a switch already in hass-miner list, it is not duplicated."""
        flow = _make_flow(hass)
        flow._discovered = {
            "switch.hass_miner": {"entity_id": "switch.hass_miner", "name": "S9", "power_w": 1400}
        }
        flow._selected_ids = ["switch.hass_miner"]
        flow._miners = []
        flow._pending = []

        captured = {}

        async def _capture_configure(user_input=None):
            captured["selected"] = list(flow._selected_ids)
            return {"type": "form", "step_id": "configure_miner"}

        flow.async_step_configure_miner = _capture_configure

        await flow.async_step_add_manual_miners(
            user_input={"manual_miners": ["switch.hass_miner"]}  # duplicate
        )

        assert captured["selected"].count("switch.hass_miner") == 1

    @pytest.mark.asyncio
    async def test_proceeds_without_manual_if_hass_miners_selected(self, hass):
        """Submitting empty manual list is fine when hass-miner miners are selected."""
        flow = _make_flow(hass)
        flow._discovered = {
            "switch.hass_miner": {"entity_id": "switch.hass_miner", "name": "S9", "power_w": 1400}
        }
        flow._selected_ids = ["switch.hass_miner"]
        flow._miners = []
        flow._pending = []

        reached_configure = False

        async def _mark_configure(user_input=None):
            nonlocal reached_configure
            reached_configure = True
            return {"type": "form", "step_id": "configure_miner"}

        flow.async_step_configure_miner = _mark_configure

        result = await flow.async_step_add_manual_miners(user_input={"manual_miners": []})

        assert reached_configure


# ---------------------------------------------------------------------------
# Config flow: select_miners skips to add_manual_miners when nothing discovered
# ---------------------------------------------------------------------------

class TestConfigFlowSelectMinersSkip:

    @pytest.mark.asyncio
    async def test_skips_select_miners_when_none_discovered(self, hass):
        """If hass-miner finds nothing, select_miners goes straight to add_manual_miners."""
        flow = _make_flow(hass)
        reached_manual = False

        async def _mark_manual(user_input=None):
            nonlocal reached_manual
            reached_manual = True
            return {"type": "form", "step_id": "add_manual_miners"}

        flow.async_step_add_manual_miners = _mark_manual

        with patch(
            "custom_components.stack_miners.config_flow._discover_miner_switches",
            return_value=[],
        ):
            await flow.async_step_select_miners()

        assert reached_manual
        assert flow._selected_ids == []

    @pytest.mark.asyncio
    async def test_shows_form_when_miners_discovered(self, hass):
        """When hass-miner entities exist, select_miners shows its form."""
        flow = _make_flow(hass)

        with patch(
            "custom_components.stack_miners.config_flow._discover_miner_switches",
            return_value=[{"entity_id": "switch.s9", "name": "S9", "power_w": 1400}],
        ):
            result = await flow.async_step_select_miners(user_input=None)

        assert result["type"] == "form"
        assert result["step_id"] == "select_miners"

    @pytest.mark.asyncio
    async def test_select_miners_proceeds_with_empty_selection(self, hass):
        """User may leave hass-miner list empty and add only manual miners."""
        flow = _make_flow(hass)
        reached_manual = False

        async def _mark_manual(user_input=None):
            nonlocal reached_manual
            reached_manual = True
            return {"type": "form", "step_id": "add_manual_miners"}

        flow.async_step_add_manual_miners = _mark_manual

        with patch(
            "custom_components.stack_miners.config_flow._discover_miner_switches",
            return_value=[{"entity_id": "switch.s9", "name": "S9", "power_w": 1400}],
        ):
            await flow.async_step_select_miners(user_input={"selected_miners": []})

        assert reached_manual
        assert flow._selected_ids == []


# ---------------------------------------------------------------------------
# Options flow: pre-population of previously saved manual miners
# ---------------------------------------------------------------------------

class TestOptionsFlowManualMiners:

    @pytest.mark.asyncio
    async def test_manual_miners_pre_populated_in_options(self, hass):
        """Previously saved manual miners appear as defaults in the options step."""
        import voluptuous as vol
        from custom_components.stack_miners.config_flow import _manual_miners_schema

        existing = [
            {"name": "S9", CONF_MINER_ENTITY_ID: "switch.hass_miner", CONF_MINER_POWER_W: 1400},
            {"name": "Plug", CONF_MINER_ENTITY_ID: "switch.smart_plug", CONF_MINER_POWER_W: 500},
        ]
        flow = _make_options_flow(hass, existing_miners=existing)
        # hass-miner only knows about switch.hass_miner
        flow._discovered = {
            "switch.hass_miner": {"entity_id": "switch.hass_miner", "name": "S9", "power_w": 1400}
        }
        flow._selected_ids = ["switch.hass_miner"]

        # Capture the schema that gets passed to async_show_form
        captured_schema = {}

        def _capture_form(**kw):
            captured_schema["schema"] = kw.get("data_schema")
            return {"type": "form", "step_id": "add_manual_miners"}

        flow.async_show_form = _capture_form

        await flow.async_step_add_manual_miners(user_input=None)

        # Verify the schema was built with switch.smart_plug as a default
        schema = captured_schema["schema"]
        assert schema is not None
        key = next(k for k in schema.schema if isinstance(k, vol.Optional) and k.schema == "manual_miners")
        assert "switch.smart_plug" in key.default()

    @pytest.mark.asyncio
    async def test_options_manual_miners_saved_correctly(self, hass):
        """New manual miners are persisted in the options entry."""
        flow = _make_options_flow(hass)
        flow._discovered = {}
        flow._selected_ids = []
        flow._miners = []
        flow._pending = []
        flow._data = {"grid_sensor_entity_id": "sensor.grid"}

        async def _fast_configure(user_input=None):
            flow._data[CONF_MINERS] = flow._miners
            return flow.async_create_entry(title="", data=flow._data)

        flow.async_step_configure_miner = _fast_configure

        result = await flow.async_step_add_manual_miners(
            user_input={"manual_miners": ["switch.new_plug"]}
        )

        assert flow._selected_ids == ["switch.new_plug"]
        assert "switch.new_plug" in flow._discovered

    @pytest.mark.asyncio
    async def test_options_skips_select_miners_when_none_discovered(self, hass):
        """Options flow also skips select_miners when hass-miner returns nothing."""
        flow = _make_options_flow(hass)
        reached_manual = False

        async def _mark_manual(user_input=None):
            nonlocal reached_manual
            reached_manual = True
            return {"type": "form", "step_id": "add_manual_miners"}

        flow.async_step_add_manual_miners = _mark_manual

        with patch(
            "custom_components.stack_miners.config_flow._discover_miner_switches",
            return_value=[],
        ):
            await flow.async_step_select_miners()

        assert reached_manual
