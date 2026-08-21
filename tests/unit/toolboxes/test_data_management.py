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
        "create_data_view", "create_short_url", "import_saved_objects",
        "delete_data_view", "delete_short_url", "overwrite_saved_objects",
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
