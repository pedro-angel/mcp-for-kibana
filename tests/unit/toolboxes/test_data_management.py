import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError

from kibana_mcp.toolboxes.base import ToolboxDeps
from kibana_mcp.toolboxes.data_management.toolbox import DataManagementToolbox
from tests.fakes import FakeGateway


@pytest.fixture()
def gateway():
    return FakeGateway()


@pytest.fixture()
def mcp(gateway):
    server = FastMCP("test")
    deps = ToolboxDeps(gateway_factory=lambda space=None: gateway, public_kibana_url="http://kb:5601")
    DataManagementToolbox().register(server, deps)
    return server


async def test_exposes_exactly_the_expected_tools(mcp):
    async with Client(mcp) as client:
        names = {t.name for t in await client.list_tools()}
    assert names == {
        "list_data_views", "describe_data_view", "resolve_short_url", "export_saved_objects",
        "create_data_view", "update_data_view", "create_short_url", "import_saved_objects",
        "delete_data_view", "delete_short_url", "overwrite_saved_objects",
        "find_saved_objects", "get_saved_object", "get_default_data_view",
        "upsert_runtime_field", "set_default_data_view", "delete_runtime_field",
    }


async def test_data_view_tools_moved_here_still_work(mcp):
    async with Client(mcp) as client:
        result = await client.call_tool("list_data_views", {})
        assert result.data[0]["index_pattern"] == "kibana_sample_data_flights"
        detail = await client.call_tool("describe_data_view", {"data_view": "flights"})
        assert detail.data["time_field"] == "timestamp"
        assert detail.data["fields"]["AvgTicketPrice"] == "number"


async def test_create_then_delete_data_view(mcp, gateway):
    async with Client(mcp) as client:
        created = await client.call_tool(
            "create_data_view", {"index_pattern": "logs-*", "name": "logs", "time_field": "@timestamp"}
        )
        did = created.data["id"]
        assert created.data["index_pattern"] == "logs-*"
        assert did in gateway.data_views
        deleted = await client.call_tool("delete_data_view", {"view_id": did})
        assert deleted.data == {"id": did, "deleted": True}
        assert did not in gateway.data_views


async def test_create_data_view_time_field_optional(mcp, gateway):
    async with Client(mcp) as client:
        created = await client.call_tool("create_data_view", {"index_pattern": "events-*"})
    assert gateway.data_views[created.data["id"]].time_field is None


async def test_create_data_view_wrong_time_field_is_a_tool_error_and_creates_nothing(mcp, gateway):
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match=r"time field '@timestamp' is not a date field .*"
                                            r"\(no data view was created\)"):
            await client.call_tool("create_data_view", {
                "index_pattern": "kibana_sample_data_flights", "name": "flights 2",
                "time_field": "@timestamp",
            })
        created = await client.call_tool("create_data_view", {
            "index_pattern": "kibana_sample_data_flights", "name": "flights 3",
            "time_field": "timestamp",
        })
    assert set(gateway.data_views) == {"dv1", created.data["id"]}


async def test_create_data_view_description_states_the_time_field_check(mcp):
    async with Client(mcp) as client:
        tools = {t.name: t for t in await client.list_tools()}
    text = tools["create_data_view"].description
    assert "must be a date field" in text and "no data view is" in text
    assert "update_data_view" in text


async def test_short_url_create_resolve_delete(mcp, gateway):
    async with Client(mcp) as client:
        created = await client.call_tool(
            "create_short_url",
            {"locator_id": "LEGACY_SHORT_URL_LOCATOR", "params": {"url": "/app/dashboards"}},
        )
        sid, slug = created.data["id"], created.data["slug"]
        assert created.data["locator_id"] == "LEGACY_SHORT_URL_LOCATOR"
        resolved = await client.call_tool("resolve_short_url", {"slug": slug})
        assert resolved.data["id"] == sid
        deleted = await client.call_tool("delete_short_url", {"short_url_id": sid})
        assert deleted.data == {"id": sid, "deleted": True}


async def test_create_short_url_rejects_unknown_locator(mcp, gateway):
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match="unsupported locator_id"):
            await client.call_tool(
                "create_short_url", {"locator_id": "DASHBOARD_APP_LOCATOR", "params": {"url": "/x"}}
            )
    assert gateway.short_urls == {}  # nothing created on validation failure


@pytest.mark.parametrize("bad_url", ["http://evil.example", "//evil.example/x", "/\\evil.example", "app/x"])
async def test_create_short_url_rejects_offsite_or_non_path_url(mcp, gateway, bad_url):
    # Includes the protocol-relative '//' and '/\' open-redirect vectors.
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match="same-origin"):
            await client.call_tool(
                "create_short_url",
                {"locator_id": "LEGACY_SHORT_URL_LOCATOR", "params": {"url": bad_url}},
            )
    assert gateway.short_urls == {}


async def test_tier_tags(mcp):
    async with Client(mcp) as client:
        tools = {t.name: t for t in await client.list_tools()}
    assert tools["list_data_views"].annotations.readOnlyHint is True
    assert tools["create_data_view"].annotations.readOnlyHint is False
    assert tools["create_data_view"].annotations.destructiveHint is False
    assert tools["delete_data_view"].annotations.destructiveHint is True
    assert tools["update_data_view"].annotations.readOnlyHint is False
    assert tools["update_data_view"].annotations.destructiveHint is False


# --- Task 6: space parameter on all 10 data-management tools ---


async def test_create_data_view_in_space_threads_and_echoes(gateway):
    calls = []

    def factory(space=None):
        calls.append(space)
        return gateway

    server = FastMCP("test")
    deps = ToolboxDeps(gateway_factory=factory, public_kibana_url="http://kb:5601")
    DataManagementToolbox().register(server, deps)
    async with Client(server) as client:
        result = await client.call_tool(
            "create_data_view",
            {"index_pattern": "kibana_sample_data_flights", "space": "sales"},
        )
    assert calls == ["sales"] and result.data["space"] == "sales"


async def test_create_short_url_rejects_space_prefixed_path_when_space_set(mcp):
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match="without the `/s/<space>` prefix"):
            await client.call_tool("create_short_url", {
                "locator_id": "LEGACY_SHORT_URL_LOCATOR",
                "params": {"url": "/s/sales/app/dashboards#/view/x"},
                "space": "sales",
            })


async def test_create_short_url_space_prefixed_path_without_space_still_passes(mcp, gateway):
    # default-path pin: today's behavior, byte-identical
    async with Client(mcp) as client:
        result = await client.call_tool("create_short_url", {
            "locator_id": "LEGACY_SHORT_URL_LOCATOR",
            "params": {"url": "/s/sales/app/dashboards#/view/x"},
        })
    assert "space" not in result.data


# --- saved_objects export/import (#37) ---

from kibana_mcp.core.errors import KibanaRejected  # noqa: E402


def _mcp_with_export(gateway, tmp_path):
    server = FastMCP("test")
    deps = ToolboxDeps(
        gateway_factory=lambda space=None: gateway, public_kibana_url="http://kb:5601",
        export_dir=tmp_path
    )
    DataManagementToolbox().register(server, deps)
    return server


async def test_export_returns_handle_and_writes_file_not_content(gateway, tmp_path):
    mcp = _mcp_with_export(gateway, tmp_path)
    async with Client(mcp) as client:
        r = await client.call_tool(
            "export_saved_objects", {"objects": [{"type": "index-pattern", "id": "dv1"}]}
        )
    handle = r.data["handle"]
    assert handle.startswith("so-")
    assert r.data["exported_count"] == 1
    assert (tmp_path / f"{handle}.ndjson").exists()  # NDJSON on disk
    assert "attributes" not in str(r.data)  # ...NOT in the model-facing response


async def test_export_requires_exactly_one_selector(gateway, tmp_path):
    mcp = _mcp_with_export(gateway, tmp_path)
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match="exactly one"):
            await client.call_tool("export_saved_objects", {})


async def test_export_then_import_roundtrip(gateway, tmp_path):
    mcp = _mcp_with_export(gateway, tmp_path)
    async with Client(mcp) as client:
        exp = await client.call_tool(
            "export_saved_objects", {"objects": [{"type": "index-pattern", "id": "dv1"}]}
        )
        imp = await client.call_tool("import_saved_objects", {"handle": exp.data["handle"]})
    assert imp.data["success"] is True
    assert imp.data["objects"][0]["destination_id"] == "dv1-copy"  # clone -> new id
    assert gateway.last_import_overwrite is False  # the write tool never overwrites


async def test_export_then_overwrite_restores_in_place(gateway, tmp_path):
    mcp = _mcp_with_export(gateway, tmp_path)
    async with Client(mcp) as client:
        exp = await client.call_tool(
            "export_saved_objects", {"objects": [{"type": "index-pattern", "id": "dv1"}]}
        )
        imp = await client.call_tool("overwrite_saved_objects", {"handle": exp.data["handle"]})
    assert imp.data["success"] is True
    # in-place: destination id == source id (not a clone)
    assert imp.data["objects"][0]["destination_id"] == imp.data["objects"][0]["source_id"] == "dv1"
    assert gateway.last_import_overwrite is True


async def test_overwrite_failure_message_is_content_free(gateway, tmp_path):
    def boom(content, overwrite):
        raise KibanaRejected("Kibana rejected the payload", detail="attributes: {token: s3cret}")

    gateway.import_saved_objects = boom
    mcp = _mcp_with_export(gateway, tmp_path)
    async with Client(mcp) as client:
        exp = await client.call_tool(
            "export_saved_objects", {"objects": [{"type": "index-pattern", "id": "dv1"}]}
        )
        with pytest.raises(ToolError) as ei:
            await client.call_tool("overwrite_saved_objects", {"handle": exp.data["handle"]})
    assert "s3cret" not in str(ei.value)  # detail (object bytes) NOT leaked
    assert "restore failed" in str(ei.value)


async def test_overwrite_bogus_handle_is_tool_error(gateway, tmp_path):
    mcp = _mcp_with_export(gateway, tmp_path)
    async with Client(mcp) as client:
        with pytest.raises(ToolError):
            await client.call_tool("overwrite_saved_objects", {"handle": "../etc/passwd"})


async def test_import_bogus_handle_is_tool_error(gateway, tmp_path):
    mcp = _mcp_with_export(gateway, tmp_path)
    async with Client(mcp) as client:
        with pytest.raises(ToolError):
            await client.call_tool("import_saved_objects", {"handle": "../etc/passwd"})


async def test_import_failure_message_is_content_free(gateway, tmp_path):
    # A Kibana rejection's `detail` can echo object bytes; the tool must not forward it.
    def boom(content, overwrite):
        raise KibanaRejected("Kibana rejected the payload", detail="attributes: {token: s3cret}")

    gateway.import_saved_objects = boom
    mcp = _mcp_with_export(gateway, tmp_path)
    async with Client(mcp) as client:
        exp = await client.call_tool(
            "export_saved_objects", {"objects": [{"type": "index-pattern", "id": "dv1"}]}
        )
        with pytest.raises(ToolError) as ei:
            await client.call_tool("import_saved_objects", {"handle": exp.data["handle"]})
    assert "s3cret" not in str(ei.value)  # detail (object bytes) NOT leaked
    assert "import failed" in str(ei.value)


# --- update_data_view: edit a data view in place (write tier) ---


# The write-tier tag itself is pinned per tier in test_server_assembly.


async def test_update_data_view_changes_only_what_it_is_given(mcp, gateway):
    async with Client(mcp) as client:
        renamed = await client.call_tool("update_data_view", {"view_id": "dv1", "name": "Flights"})
        assert renamed.data == {
            "id": "dv1", "name": "Flights", "index_pattern": "kibana_sample_data_flights",
            "time_field": "timestamp", "field_count": 5,
        }
        repointed = await client.call_tool(
            "update_data_view", {"view_id": "dv1", "index_pattern": "kibana_sample_data_fl*"}
        )
    assert repointed.data["name"] == "Flights"  # the earlier rename is kept
    assert repointed.data["time_field"] == "timestamp"
    assert gateway.data_views["dv1"].index_pattern == "kibana_sample_data_fl*"
    # The port receives None for every field the caller omitted.
    assert gateway.data_view_updates[-1] == {
        "view_id": "dv1", "name": None, "index_pattern": "kibana_sample_data_fl*",
        "time_field": None,
    }


async def test_update_data_view_without_fields_is_refused_before_the_gateway(gateway):
    calls = []

    def factory(space=None):
        calls.append(space)
        return gateway

    server = FastMCP("test")
    DataManagementToolbox().register(
        server, ToolboxDeps(gateway_factory=factory, public_kibana_url="http://kb:5601")
    )
    async with Client(server) as client:
        with pytest.raises(
            ToolError, match="provide at least one field to update: name, index_pattern or time_field"
        ):
            await client.call_tool("update_data_view", {"view_id": "dv1", "space": "sales"})
        with pytest.raises(ToolError, match=r"`index_pattern` is blank — pass a value, or omit it"):
            await client.call_tool("update_data_view", {"view_id": "dv1", "index_pattern": "  "})
    assert calls == []  # guards run before the factory: no space check, no Kibana call
    assert gateway.data_view_updates == []


@pytest.mark.parametrize("field", ["name", "index_pattern", "time_field"])
async def test_update_data_view_rejects_empty_strings_at_the_schema(mcp, gateway, field):
    # An empty title is not harmless: Kibana stores it and the view then
    # matches every index (probed on 9.5.4).
    async with Client(mcp) as client:
        with pytest.raises(ToolError):
            await client.call_tool("update_data_view", {"view_id": "dv1", field: ""})
    assert gateway.data_view_updates == []


async def test_update_data_view_time_field_rejection_surfaces_as_tool_error(mcp, gateway):
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match="time field 'Carrier' is not a date field"):
            await client.call_tool("update_data_view", {"view_id": "dv1", "time_field": "Carrier"})
    assert gateway.data_views["dv1"].time_field == "timestamp"


async def test_update_data_view_unknown_id_names_list_data_views(mcp):
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match=r"call list_data_views to find it"):
            await client.call_tool("update_data_view", {"view_id": "flights", "name": "x"})


async def test_update_data_view_in_space_threads_and_echoes(gateway):
    calls = []

    def factory(space=None):
        calls.append(space)
        return gateway

    server = FastMCP("test")
    DataManagementToolbox().register(
        server, ToolboxDeps(gateway_factory=factory, public_kibana_url="http://kb:5601")
    )
    async with Client(server) as client:
        scoped = await client.call_tool(
            "update_data_view", {"view_id": "dv1", "time_field": "timestamp", "space": "sales"}
        )
        unscoped = await client.call_tool("update_data_view", {"view_id": "dv1", "name": "n"})
    assert calls == ["sales", None]
    assert scoped.data["space"] == "sales"
    assert "space" not in unscoped.data


# --- runtime fields, the default data view, saved-objects find/get (D48) ---


def _recording(gateway):
    calls = []

    def factory(space=None):
        calls.append(space)
        return gateway

    server = FastMCP("test")
    DataManagementToolbox().register(
        server, ToolboxDeps(gateway_factory=factory, public_kibana_url="http://kb:5601")
    )
    return server, calls


async def test_new_tools_tiers_and_hints(mcp):
    async with Client(mcp) as client:
        tools = {t.name: t for t in await client.list_tools()}
    for name in ("find_saved_objects", "get_saved_object", "get_default_data_view"):
        assert tools[name].annotations.readOnlyHint is True, name
    for name in ("upsert_runtime_field", "set_default_data_view"):
        hints = tools[name].annotations
        assert (hints.readOnlyHint, hints.destructiveHint, hints.idempotentHint) == (
            False, False, True,
        ), name
    hints = tools["delete_runtime_field"].annotations
    assert (hints.readOnlyHint, hints.destructiveHint) == (False, True)


async def test_upsert_runtime_field_creates_then_replaces_and_returns_previous(mcp, gateway):
    async with Client(mcp) as client:
        created = await client.call_tool("upsert_runtime_field", {
            "view_id": "dv1", "name": "hour", "type": "long",
            "script": "emit(doc['timestamp'].value.getHour())",
        })
        replaced = await client.call_tool("upsert_runtime_field", {
            "view_id": "dv1", "name": "hour", "type": "keyword", "script": "emit('x')",
        })
    assert created.data == {
        "view_id": "dv1", "created": True, "previous": None,
        "field": {"name": "hour", "type": "long",
                  "script": "emit(doc['timestamp'].value.getHour())"},
    }
    assert replaced.data["created"] is False
    assert replaced.data["previous"] == created.data["field"]
    assert replaced.data["field"] == {"name": "hour", "type": "keyword", "script": "emit('x')"}
    assert gateway.runtime_fields["dv1"]["hour"].type == "keyword"


async def test_upsert_runtime_field_type_is_an_enum_without_composite(mcp):
    async with Client(mcp) as client:
        tools = {t.name: t for t in await client.list_tools()}
        schema = tools["upsert_runtime_field"].inputSchema["properties"]["type"]
        assert schema["enum"] == ["keyword", "long", "double", "date", "ip", "boolean", "geo_point"]
        with pytest.raises(ToolError):
            await client.call_tool("upsert_runtime_field", {
                "view_id": "dv1", "name": "c", "type": "composite",
            })


async def test_upsert_runtime_field_blank_script_or_name_is_refused_before_the_gateway(gateway):
    server, calls = _recording(gateway)
    async with Client(server) as client:
        with pytest.raises(ToolError, match=r"`script` is blank — pass Painless source, or omit"):
            await client.call_tool("upsert_runtime_field", {
                "view_id": "dv1", "name": "h", "type": "long", "script": "  ", "space": "x",
            })
        with pytest.raises(ToolError, match=r"`name` is blank"):
            await client.call_tool("upsert_runtime_field", {
                "view_id": "dv1", "name": " ", "type": "long",
            })
    assert calls == [] and gateway.runtime_fields == {}


async def test_upsert_runtime_field_description_warns_kibana_does_not_compile(mcp):
    async with Client(mcp) as client:
        tools = {t.name: t for t in await client.list_tools()}
    flat = " ".join(tools["upsert_runtime_field"].description.split())
    assert "Kibana does NOT compile the script" in flat
    assert "delete_runtime_field" in flat and "previous" in flat and "custom label" in flat


async def test_upsert_runtime_field_mapped_name_is_a_tool_error(mcp, gateway):
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match=r"'Carrier' is already a field of data view 'dv1'"):
            await client.call_tool("upsert_runtime_field", {
                "view_id": "dv1", "name": "Carrier", "type": "keyword",
            })
    assert gateway.runtime_fields.get("dv1", {}) == {}


async def test_delete_runtime_field_returns_the_definition_and_misses_are_errors(mcp, gateway):
    async with Client(mcp) as client:
        await client.call_tool("upsert_runtime_field", {
            "view_id": "dv1", "name": "hour", "type": "long", "script": "emit(1)",
        })
        deleted = await client.call_tool("delete_runtime_field", {"view_id": "dv1", "name": "hour"})
        with pytest.raises(ToolError, match=r"data view 'dv1' has no runtime field 'hour'"):
            await client.call_tool("delete_runtime_field", {"view_id": "dv1", "name": "hour"})
        with pytest.raises(ToolError, match=r"call list_data_views to find it"):
            await client.call_tool("delete_runtime_field", {"view_id": "flights", "name": "h"})
    assert deleted.data == {
        "view_id": "dv1", "deleted": True,
        "field": {"name": "hour", "type": "long", "script": "emit(1)"},
    }


async def test_delete_runtime_field_refuses_a_mapped_field_and_a_composite_subfield(mcp, gateway):
    from kibana_mcp.core.models import RuntimeField

    gateway.runtime_fields["dv1"] = {
        "comp": RuntimeField(name="comp", type="composite", script="emit(1)", fields={"a": "keyword"})
    }
    mapped = next(iter(gateway.data_views["dv1"].fields))
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match=r"subfield of the composite runtime field 'comp'"):
            await client.call_tool("delete_runtime_field", {"view_id": "dv1", "name": "comp.a"})
        with pytest.raises(ToolError, match=r"only runtime fields can be deleted"):
            await client.call_tool("delete_runtime_field", {"view_id": "dv1", "name": mapped})
    assert set(gateway.runtime_fields["dv1"]) == {"comp"}


async def test_delete_runtime_field_returns_a_composites_fields(mcp, gateway):
    from kibana_mcp.core.models import RuntimeField

    gateway.runtime_fields["dv1"] = {
        "comp": RuntimeField(name="comp", type="composite", script="emit(1)", fields={"a": "keyword"})
    }
    async with Client(mcp) as client:
        deleted = await client.call_tool("delete_runtime_field", {"view_id": "dv1", "name": "comp"})
    assert deleted.data["field"]["fields"] == {"a": "keyword"}


async def test_get_and_set_default_data_view_round_trip(mcp, gateway):
    async with Client(mcp) as client:
        unset = await client.call_tool("get_default_data_view", {})
        changed = await client.call_tool("set_default_data_view", {"view_id": "dv1"})
        now = await client.call_tool("get_default_data_view", {})
        cleared = await client.call_tool("set_default_data_view", {"view_id": None})
        with pytest.raises(ToolError, match=r"data view 'bogus' not found .*list_data_views"):
            await client.call_tool("set_default_data_view", {"view_id": "bogus"})
    assert unset.data == {"id": None, "name": None, "index_pattern": None, "exists": False}
    assert changed.data == {
        "id": "dv1", "name": "flights", "index_pattern": "kibana_sample_data_flights",
        "exists": True, "previous_id": None, "previous_exists": False,
    }
    assert now.data == {
        k: v for k, v in changed.data.items() if k not in ("previous_id", "previous_exists")
    }
    assert cleared.data == {
        "id": None, "name": None, "index_pattern": None, "exists": False, "previous_id": "dv1",
        "previous_exists": True,
    }
    assert gateway.default_data_view_id is None


async def test_set_default_data_view_over_a_dangling_default_says_null_undoes_it(mcp, gateway):
    # Kibana keeps a deleted default's id; the tool will not store it again, so
    # previous_exists=false tells the caller to undo with view_id=null instead.
    gateway.default_data_view_id = "deleted-1"
    async with Client(mcp) as client:
        changed = await client.call_tool("set_default_data_view", {"view_id": "dv1"})
        with pytest.raises(ToolError, match=r"data view 'deleted-1' not found"):
            await client.call_tool("set_default_data_view", {"view_id": "deleted-1"})
        undone = await client.call_tool("set_default_data_view", {"view_id": None})
        tools = {t.name: t for t in await client.list_tools()}
    assert changed.data["previous_id"] == "deleted-1"
    assert changed.data["previous_exists"] is False
    assert undone.data["id"] is None and undone.data["previous_exists"] is True
    assert "view_id=null undoes it" in tools["set_default_data_view"].description


async def test_get_default_data_view_reports_a_dangling_id(mcp, gateway):
    gateway.default_data_view_id = "deleted-1"
    async with Client(mcp) as client:
        got = await client.call_tool("get_default_data_view", {})
    assert got.data == {"id": "deleted-1", "name": None, "index_pattern": None, "exists": False}


async def test_set_default_data_view_requires_view_id_but_accepts_null(mcp):
    async with Client(mcp) as client:
        tools = {t.name: t for t in await client.list_tools()}
        assert tools["set_default_data_view"].inputSchema["required"] == ["view_id"]
        with pytest.raises(ToolError):
            await client.call_tool("set_default_data_view", {})


async def test_find_saved_objects_returns_summaries_and_passes_the_filters(mcp, gateway):
    async with Client(mcp) as client:
        found = await client.call_tool("find_saved_objects", {
            "types": ["dashboard"], "search": "global*",
            "has_reference": {"type": "index-pattern", "id": "dv1"}, "page": 1, "per_page": 5,
        })
    assert found.data == {
        "total": 1, "page": 1, "per_page": 5,
        "saved_objects": [{
            "type": "dashboard", "id": "dash-1", "title": "[Flights] Global Flight Dashboard",
            "name": None, "updated_at": "2026-10-25T00:00:00.000Z", "namespaces": ["default"],
            "managed": False,
        }],
    }
    assert gateway.saved_object_finds == [{
        "types": ["dashboard"], "search": "global*",
        "has_reference": {"type": "index-pattern", "id": "dv1"}, "page": 1, "per_page": 5,
    }]


async def test_find_saved_objects_empty_and_past_the_end_carry_a_note(mcp):
    async with Client(mcp) as client:
        empty = await client.call_tool("find_saved_objects", {"types": ["action"]})
        past = await client.call_tool("find_saved_objects", {"types": ["dashboard"], "page": 3})
    assert empty.data["total"] == 0 and "check the type names" in empty.data["note"]
    assert past.data["saved_objects"] == []
    assert past.data["note"] == "page 3 is past the last page (1 results, 20 per page)"


@pytest.mark.parametrize(("args", "message"), [
    ({"types": ["*"]}, r"not blank and not '\*'"),
    ({"types": [" "]}, r"not blank and not '\*'"),
    ({"types": ["dashboard"], "search": "  "}, r"`search` is blank"),
    ({"types": ["dashboard"], "has_reference": {"id": "x"}}, r"must be exactly"),
    ({"types": ["dashboard"], "has_reference": {"type": "lens", "id": " "}}, r"must be exactly"),
    ({"types": ["dashboard"], "page": 101, "per_page": 100}, r"past the first 10,000 results"),
])
async def test_find_saved_objects_guards_run_before_the_gateway(gateway, args, message):
    server, calls = _recording(gateway)
    async with Client(server) as client:
        with pytest.raises(ToolError, match=message):
            await client.call_tool("find_saved_objects", {**args, "space": "x"})
    assert calls == [] and gateway.saved_object_finds == []


async def test_find_saved_objects_schema_bounds(mcp):
    async with Client(mcp) as client:
        for bad in ({"types": []}, {"types": ["dashboard"], "per_page": 101},
                    {"types": ["dashboard"], "page": 0}):
            with pytest.raises(ToolError):
                await client.call_tool("find_saved_objects", bad)


async def test_get_saved_object_caps_long_attributes_and_lists_references(mcp):
    async with Client(mcp) as client:
        got = await client.call_tool("get_saved_object", {"type": "dashboard", "id": "dash-1"})
        small = await client.call_tool("get_saved_object", {"type": "index-pattern", "id": "dv1"})
    assert got.data == {
        "type": "dashboard", "id": "dash-1", "namespaces": ["default"],
        "created_at": "2026-10-25T00:00:00.000Z", "updated_at": "2026-10-25T00:00:00.000Z",
        "managed": False, "attributes": {"title": "[Flights] Global Flight Dashboard"},
        "references": [{"type": "index-pattern", "id": "dv1"}],
        "omitted_attributes": {"panelsJSON": 5000},
    }
    assert "omitted_attributes" not in small.data


async def test_get_saved_object_description_states_the_cap(mcp):
    from kibana_mcp.core.saved_objects import ATTRIBUTE_CHARS_SHOWN

    async with Client(mcp) as client:
        tools = {t.name: t for t in await client.list_tools()}
    assert f"{ATTRIBUTE_CHARS_SHOWN:,} characters" in tools["get_saved_object"].description


@pytest.mark.parametrize("type_", ["fleet-proxy", "ingest-outputs", "synthetics-param"])
async def test_get_saved_object_refuses_a_type_outside_the_allowlist_before_the_gateway(
    gateway, type_
):
    server, calls = _recording(gateway)
    async with Client(server) as client:
        with pytest.raises(ToolError) as exc:
            await client.call_tool("get_saved_object", {"type": type_, "id": "x", "space": "x"})
    assert f"does not read '{type_}' objects" in str(exc.value)
    assert "can keep credentials in plain attributes" in str(exc.value)
    assert calls == []


@pytest.mark.parametrize(("type_", "named", "not_named"), [
    ("ingest-outputs", "list_outputs", "export_saved_objects"),
    ("fleet-proxy", "no tool reads", "export_saved_objects"),
    ("synthetics-param", "list_synthetic_params", "export_saved_objects"),
    ("action", "list_connectors", "find_saved_objects"),
    ("alert", "list_alert_rules", "find_saved_objects"),
])
def test_unreadable_type_message_names_only_ways_that_work_for_the_type(
    type_, named, not_named
):
    # Kibana 400s an export of Fleet's and Synthetics' types and answers find an
    # empty page for hidden ones (probed on 9.5.4), so neither is offered there.
    from kibana_mcp.core.saved_objects import unreadable_type_message

    message = unreadable_type_message(type_)
    assert named in message and not_named not in message


def test_unreadable_type_message_states_when_find_and_export_apply_to_another_type():
    from kibana_mcp.core.saved_objects import unreadable_type_message

    message = unreadable_type_message("some-plugin-type")
    assert "unless the type is hidden" in message
    assert "if Kibana exports the type" in message


async def test_get_saved_object_description_lists_every_readable_type(mcp):
    import re

    from kibana_mcp.core.saved_objects import READABLE_TYPES

    async with Client(mcp) as client:
        tools = {t.name: t for t in await client.list_tools()}
    text = " ".join(tools["get_saved_object"].description.split())
    listed = re.search(r"Only analytics content is read — (.*?) — because", text)
    assert listed is not None
    names = [n.split(" (")[0] for n in listed.group(1).split(", ")]
    assert names == list(READABLE_TYPES)


async def test_find_saved_objects_search_matches_the_title_or_name_only(mcp, gateway):
    from dataclasses import replace

    gateway.saved_objects[1] = replace(gateway.saved_objects[1], attributes={
        **gateway.saved_objects[1].attributes, "description": "JetBeats flights"})
    async with Client(mcp) as client:
        by_description = await client.call_tool("find_saved_objects", {
            "types": ["dashboard"], "search": "JetBeats"})
        by_name = await client.call_tool("find_saved_objects", {
            "types": ["index-pattern"], "search": "flights"})
        tools = {t.name: t for t in await client.list_tools()}
    assert by_description.data["total"] == 0
    assert by_name.data["total"] == 1
    description = " ".join(tools["find_saved_objects"].description.split())
    assert "words of the title or name, no other attribute" in description


async def test_get_saved_object_miss_and_blank_args(gateway):
    server, calls = _recording(gateway)
    async with Client(server) as client:
        with pytest.raises(ToolError, match=r"find_saved_objects\(types=\['dashboard'\]\)"):
            await client.call_tool("get_saved_object", {"type": "dashboard", "id": "nope"})
        with pytest.raises(ToolError, match=r"must not be blank"):
            await client.call_tool("get_saved_object", {"type": " ", "id": "x", "space": "x"})
    assert calls == [None]  # the blank call never reached the factory
