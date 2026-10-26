import contextlib
import os
import uuid

import kibana
import pytest

from kibana_mcp.adapters.kibana.gateway import KibanaPyGateway
from tests import _secret_scan
from tests._stack_env import require_stack_env

pytestmark = pytest.mark.contract


@pytest.fixture(autouse=True)
def secret_scan(monkeypatch):
    """Every tool result a fastmcp Client receives here is scanned for
    credentials (D59, tests/_secret_scan.py): a hit raises at the call, and
    fails the test here too in case the test swallowed the raise. Yields the
    list of leaks (the canary test clears its own deliberate one)."""
    leaks = _secret_scan.install(monkeypatch)
    yield leaks
    if leaks:
        pytest.fail("\n".join(leaks), pytrace=False)


@pytest.fixture(scope="session")
def gateway():
    require_stack_env()
    with KibanaPyGateway.connect(
        os.environ["KIBANA_URL"], os.environ["KIBANA_TEST_API_KEY"]
    ) as gw:
        yield gw


# Kibana's internal security routes (the ones its UI calls) want this header.
INTERNAL = {"x-elastic-internal-origin": "Kibana"}


@contextlib.contextmanager
def _restricted_key(kibana_privileges):
    """A throwaway role, user and API key the user creates for itself (the
    superuser test key cannot: Elasticsearch gives a key made by a key no
    privileges), so the key holds a snapshot of the role's privileges; yields
    (encoded key, role name). Needs no Elasticsearch admin credentials: only
    the test key and Kibana's own security routes. Everything is removed
    afterwards, the key invalidated before its user is deleted."""
    tag = uuid.uuid4().hex[:8]
    role, user, password = (
        f"mcp-for-kibana-ct-role-{tag}", f"mcp-for-kibana-ct-user-{tag}", uuid.uuid4().hex)
    key_id = None
    with contextlib.closing(
            kibana.Kibana(os.environ["KIBANA_URL"], api_key=os.environ["KIBANA_TEST_API_KEY"])
    ) as admin:
        try:
            admin.security.create_or_update_role(
                name=role, elasticsearch={"cluster": ["manage_own_api_key"]},
                kibana=kibana_privileges)
            admin.perform_request("POST", f"/internal/security/users/{user}", headers=INTERNAL,
                                  body={"username": user, "password": password, "roles": [role]})
            with contextlib.closing(
                    kibana.Kibana(os.environ["KIBANA_URL"], basic_auth=(user, password))) as own:
                made = own.perform_request(
                    "POST", "/internal/security/api_key", headers=INTERNAL,
                    body={"name": f"mcp-for-kibana-ct-key-{tag}", "role_descriptors": {}}).body
            key_id = made["id"]
            yield made["encoded"], role
        finally:
            # Each step runs whatever the one before it raised (ExitStack runs
            # them all, then re-raises), so nothing is left behind.
            with contextlib.ExitStack() as cleanup:
                cleanup.callback(_quietly, admin.security.delete_role, name=role)
                cleanup.callback(_quietly, admin.perform_request, "DELETE",
                                 f"/internal/security/users/{user}", headers=INTERNAL)
                if key_id is not None:
                    cleanup.callback(_invalidate, admin, key_id)


def _invalidate(admin, key_id):
    """Invalidate the key or fail: Kibana answers 200 with per-key `errors`,
    and a key outlives its deleted owner, so a silent miss would leave a live
    key on the shared stack."""
    body = admin.perform_request(
        "POST", "/internal/security/api_key/invalidate", headers=INTERNAL,
        body={"apiKeys": [{"id": key_id, "name": "ct"}], "isAdmin": True}).body
    done = [k.get("id") for k in body.get("itemsInvalidated") or ()]
    assert key_id in done and not body.get("errors"), (
        f"restricted_key could not invalidate API key {key_id}: {body.get('errors')} — "
        "invalidate it with POST /_security/api_key/_invalidate before the next run")


def _quietly(fn, *args, **kw):
    with contextlib.suppress(kibana.NotFoundError):
        fn(*args, **kw)


@pytest.fixture(scope="session")
def restricted_key():
    """The one way the contract tier gets a restricted credential (D58):
    `with restricted_key(kibana_privileges) as (key, role):`, where
    kibana_privileges is a Kibana role's `kibana` list, e.g.
    `[{"base": ["read"], "spaces": ["*"]}]` or
    `[{"feature": {"fleetv2": ["all"]}, "spaces": ["default"]}]`."""
    require_stack_env()
    return _restricted_key
