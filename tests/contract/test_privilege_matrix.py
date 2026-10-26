"""The privilege matrix (D58): every privilege-dependent tool under restricted keys.

The rest of the live tier runs as the stack's superuser-snapshot key, where
every space listing is complete, so a tool that fails open or crashes for a
key that cannot see a space passes there. Here each tool that takes `space`,
`source_space`, `target_spaces` or `spaces`, and each listing guard
(tests/_privilege_matrix.py, held to the registry by
tests/unit/test_privilege_matrix_rows.py), runs through the MCP tool layer
under three keys minted by the shared `restricted_key` fixture:

- `default-only-all`: base `all` in space 'default' only;
- `read-everywhere`: base `read` in every space;
- `no-features`: no Kibana privilege at all.

Every call ends in a complete result, a curated refusal, or an auth error
that names the fix — never a crash, masked text, a write by a read-only key
or a hidden space reported as missing. The arguments name nothing real, so
no row changes anything; the matrix creates and removes its own space, data
view, roles, users and keys.
"""

import asyncio
import contextlib
import os
import uuid

import kibana
import pytest
from fastmcp import Client

from kibana_mcp.adapters.kibana.gateway import KibanaPyGateway
from kibana_mcp.config import Settings, Tier
from kibana_mcp.server import build_server
from kibana_mcp.toolboxes import TOOLBOXES
from tests._privilege_matrix import EXPORTS, LISTING_GUARDS, minimal_args, space_params
from tests._registry import registry
from tests.contract.conftest import INTERNAL

pytestmark = pytest.mark.contract

KEYS = {
    "default-only-all": [{"base": ["all"], "spaces": ["default"]}],
    "read-everywhere": [{"base": ["read"], "spaces": ["*"]}],
    "no-features": [],
}
# Each key's username starts with this: the proof a row ran as the restricted
# key, not the stack's superuser one.
_CT_USER = "mcp-for-kibana-ct-user-"
# Text that means the call crashed or was masked, never a curated outcome.
_CRASHED = ("unexpected error", "Traceback", "Error calling tool", "Internal error",
            "AttributeError", "KeyError", "TypeError")
def _admin():
    return contextlib.closing(
        kibana.Kibana(os.environ["KIBANA_URL"], api_key=os.environ["KIBANA_TEST_API_KEY"]))


def _tools() -> dict[str, dict]:
    """Every privilege-dependent tool's input schema, from the full server."""

    async def listed():
        settings = Settings(toolboxes=list(TOOLBOXES), tier=Tier("destructive"))
        async with Client(build_server(settings, lambda space=None: None)) as client:
            return await client.list_tools()

    return {t.name: t.input_schema for t in asyncio.run(listed())
            if space_params(t.input_schema) or t.name in LISTING_GUARDS}


_TOOLS = _tools()
_SPACE_TOOLS = sorted(n for n, s in _TOOLS.items() if space_params(s))
_TIERS = {name: tool.tier for name, tool in registry().items()}


@pytest.fixture(scope="module")
def matrix(restricted_key, tmp_path_factory):
    """A throwaway space (`hidden`) the default-only key cannot see, a data
    view in 'default' to copy and share, the export files the handle rows
    read, and the three keys."""
    tag = uuid.uuid4().hex[:8]
    hidden, view = f"mcp-for-kibana-ct-matrix-{tag}", f"mcp-for-kibana-ct-matrix-{tag}"
    export_dir = tmp_path_factory.mktemp("matrix-exports")
    for handle, body in EXPORTS.items():
        (export_dir / f"{handle}.ndjson").write_text(body)
    with _admin() as admin, contextlib.ExitStack() as stack:
        admin.spaces.create(id=hidden, name=hidden, description="created by the privilege matrix")
        stack.callback(admin.spaces.delete, id=hidden)
        admin.data_views.create(data_view={"title": "kibana_sample_data_flights",
                                           "name": "ct matrix", "id": view})
        stack.callback(_quietly, admin.data_views.delete, view_id=view)
        keys = {name: stack.enter_context(restricted_key(grants))[0]
                for name, grants in KEYS.items()}
        yield {"hidden": hidden, "view": view, "keys": keys, "export_dir": export_dir}


def _quietly(fn, **kw):
    with contextlib.suppress(kibana.NotFoundError):
        fn(**kw)


def _server(matrix, key_name):
    key = matrix["keys"][key_name]
    settings = Settings(toolboxes=list(TOOLBOXES), tier=Tier("destructive"))
    return build_server(
        settings,
        lambda space=None: KibanaPyGateway.connect(os.environ["KIBANA_URL"], key, space),
        export_dir=matrix["export_dir"],
    )


async def _call(matrix, key_name, tool, args):
    async with Client(_server(matrix, key_name)) as client:
        result = await client.call_tool(tool, args, raise_on_error=False)
    text = "\n".join(getattr(c, "text", "") for c in result.content)
    return result, text


def _assert_not_crashed(tool, key_name, text):
    for marker in _CRASHED:
        assert marker not in text, (
            f"{tool} under the {key_name} key crashed or was masked ({marker!r}): {text[:400]} — "
            "a restricted key must get a result, a curated refusal or an auth error naming the fix")


def _assert_names_the_fix(tool, key_name, text):
    if "rejected the API key or permissions" in text or "no privilege in" in text:
        assert "KIBANA_API_KEY" in text and "'Authorization: ApiKey <key>'" in text, (
            f"{tool} under the {key_name} key: an auth error that names no fix: {text[:400]} — "
            "it must say which key to use and both ways to send it")


@pytest.mark.parametrize("key_name", sorted(KEYS))
def test_each_matrix_key_is_the_restricted_one(matrix, key_name):
    # The bite of the whole matrix: a key that is really the superuser one (a
    # fixture falling back to KIBANA_TEST_API_KEY) would pass every row below.
    key = matrix["keys"][key_name]
    is_test_key = key == os.environ["KIBANA_TEST_API_KEY"]  # a bool: no key value in the report
    assert not is_test_key, (
        f"the {key_name} key is KIBANA_TEST_API_KEY — restricted_key must return the key the "
        "role's own user minted (tests/contract/conftest.py), never fall back to the test key")
    with contextlib.closing(kibana.Kibana(os.environ["KIBANA_URL"], api_key=key)) as client:
        me = client.perform_request("GET", "/internal/security/me", headers=INTERNAL).body
    assert me["username"].startswith(_CT_USER) and me["roles"] != ["superuser"], (
        f"the {key_name} key authenticates as {me['username']!r} with roles {me['roles']} — "
        "restricted_key must mint it as the throwaway user, from the role under test")


@pytest.mark.parametrize("key_name", sorted(KEYS))
async def test_list_spaces_shows_only_what_the_key_can_view(matrix, key_name):
    result, text = await _call(matrix, key_name, "list_spaces", {})
    _assert_not_crashed("list_spaces", key_name, text)
    if key_name == "no-features":
        assert result.is_error and "rejected the API key or permissions" in text
        _assert_names_the_fix("list_spaces", key_name, text)
        return
    seen = {s["id"] for s in result.structured_content["result"]}
    expected = {"default-only-all": {"default"}, "read-everywhere": {"default", matrix["hidden"]}}
    assert expected[key_name] <= seen, f"list_spaces under {key_name}: {sorted(seen)}"
    if key_name == "default-only-all":
        assert seen == {"default"}, (
            f"list_spaces under the default-only key shows {sorted(seen)}: the key sees more than "
            "its role grants — restricted_key must return the restricted key, not the test key")


def _hidden_args(tool, hidden):
    args = minimal_args(tool, _TOOLS[tool])
    params = space_params(_TOOLS[tool])
    args["space" if "space" in params else "source_space"] = hidden
    return args


@pytest.mark.parametrize("key_name", ["default-only-all", "no-features"])
@pytest.mark.parametrize("tool", _SPACE_TOOLS)
async def test_a_space_the_key_cannot_read_is_refused_naming_the_fix(matrix, tool, key_name):
    # Kibana answers 403 for a space the key has no privilege in (existing or
    # not), so the refusal is about the key, never "not found, create it".
    hidden = matrix["hidden"]
    result, text = await _call(matrix, key_name, tool, _hidden_args(tool, hidden))
    _assert_not_crashed(tool, key_name, text)
    assert result.is_error, f"{tool} under {key_name} in space {hidden!r} succeeded: {text[:400]}"
    assert f"raised while validating space '{hidden}'" in text, (
        f"{tool} under {key_name}: the space was not validated first: {text[:400]}")
    assert "list_spaces shows the spaces this key can view" in text and "create_space" not in text, (
        f"{tool} under {key_name}: a space the key cannot read must be reported as a privilege "
        f"problem naming list_spaces and a key that grants it, never as missing: {text[:400]}")
    _assert_names_the_fix(tool, key_name, text)


@pytest.mark.parametrize("tool", _SPACE_TOOLS)
async def test_a_read_only_key_reads_or_is_refused_never_writes(matrix, tool):
    # The space is visible to this key: reads return a result or a curated
    # error for the made-up ids; anything that writes is refused.
    key_name = "read-everywhere"
    result, text = await _call(matrix, key_name, tool, _hidden_args(tool, matrix["hidden"]))
    _assert_not_crashed(tool, key_name, text)
    _assert_names_the_fix(tool, key_name, text)
    if _TIERS[tool] != "read":
        assert result.is_error, f"{tool} (not a read tool) succeeded under a read-only key: {text[:400]}"


@pytest.mark.parametrize("tool,param", [
    ("copy_saved_objects_to_spaces", "target_spaces"),
    ("overwrite_saved_objects_in_spaces", "target_spaces"),
    ("share_saved_objects_to_spaces", "spaces"),
])
async def test_a_target_space_the_key_cannot_read_is_not_reported_missing(matrix, tool, param):
    # privilege-2: the space exists, the default-only key cannot see it.
    hidden = matrix["hidden"]
    args = {"objects": [{"type": "index-pattern", "id": matrix["view"]}], param: [hidden]}
    result, text = await _call(matrix, "default-only-all", tool, args)
    assert result.is_error, f"{tool} into a space the key cannot read succeeded: {text[:400]}"
    assert text.startswith(f"cannot write into space '{hidden}': this API key has no privilege"), (
        f"{tool}: a target space the key cannot read must be refused as a privilege problem "
        f"(spaces.get answers 403), not as missing: {text[:400]}")
    assert "create_space" not in text and "not found" not in text
    _assert_names_the_fix(tool, "default-only-all", text)
    with _admin() as admin:
        assert admin.space(hidden, validate=False).saved_objects.find(
            type=["index-pattern"]).body["total"] == 0  # nothing written


@pytest.mark.parametrize("key_name", sorted(KEYS))
async def test_copy_and_share_targets_under_each_key(matrix, key_name):
    # Each target tool into the hidden space plus a genuinely missing one:
    # never a write into the hidden space, never a missing space read as
    # "create it" when it is only hidden, and a truly missing space still
    # named as missing by a key that can see every space.
    hidden, ghost = matrix["hidden"], f"mcp-for-kibana-ct-ghost-{uuid.uuid4().hex[:8]}"
    objects = [{"type": "index-pattern", "id": matrix["view"]}]
    calls = {
        "copy_saved_objects_to_spaces": {"objects": objects, "target_spaces": [hidden]},
        "overwrite_saved_objects_in_spaces": {"objects": objects, "target_spaces": [hidden]},
        "share_saved_objects_to_spaces": {"objects": objects, "spaces": [hidden]},
        "unshare_saved_objects_from_spaces": {"objects": objects, "spaces": [hidden]},
    }
    for tool, args in calls.items():
        result, text = await _call(matrix, key_name, tool, args)
        _assert_not_crashed(tool, key_name, text)
        _assert_names_the_fix(tool, key_name, text)
        if result.is_error:
            assert "create_space" not in text, f"{tool} under {key_name}: {text[:400]}"
        else:  # only a per-space failure report: nothing copied or shared
            for space in result.structured_content.get("results", ()):
                assert not space.get("copied") and not space.get("added"), (
                    f"{tool} under {key_name} wrote into {hidden!r}: {text[:400]}")
    if key_name == "read-everywhere":
        result, text = await _call(matrix, key_name, "copy_saved_objects_to_spaces",
                                   {"objects": objects, "target_spaces": [ghost]})
        assert result.is_error and f"space '{ghost}' not found" in text and "create_space" in text
    with _admin() as admin:
        in_hidden = admin.space(hidden, validate=False).saved_objects.find(
            type=["index-pattern"]).body["total"]
        view = admin.data_views.get(view_id=matrix["view"]).body["data_view"]
    assert in_hidden == 0 and "default" in view.get("namespaces", ["default"])


@pytest.fixture(scope="module")
def tcp_installed(matrix):
    """The tcp integration installed (no package policy) for the uninstall
    rows; removed afterwards unless it was installed before."""
    with KibanaPyGateway.connect(os.environ["KIBANA_URL"], os.environ["KIBANA_TEST_API_KEY"]) as gw:
        epm = gw._client.fleet_epm
        before = epm.get_package(pkg_name="tcp").body["item"].get("status") == "installed"
        if not before:
            gw.install_package(name="tcp")
        aware = bool(gw._client.fleet.get_settings().body["item"].get(
            "use_space_awareness_migration_status"))
        try:
            yield aware
        finally:
            if not before and epm.get_package(pkg_name="tcp").body["item"].get(
                    "status") == "installed":
                epm.uninstall_package(pkg_name="tcp", force=True)


@pytest.mark.parametrize("key_name", sorted(KEYS))
async def test_uninstall_package_under_each_key(matrix, tcp_installed, key_name):
    # The listing guard: with Fleet space awareness on no key's listing is
    # provably complete, so every key is refused (or lacks the privilege);
    # with it off only a key that may uninstall in 'default' succeeds.
    result, text = await _call(matrix, key_name, "uninstall_package", {"name": "tcp"})
    _assert_not_crashed("uninstall_package", key_name, text)
    _assert_names_the_fix("uninstall_package", key_name, text)
    if tcp_installed or key_name != "default-only-all":
        assert result.is_error, f"uninstall_package under {key_name} succeeded: {text[:400]}"
    if tcp_installed and key_name != "no-features":
        assert "Fleet space awareness is on" in text, text[:400]
