"""Run the built Docker image as the replayed server, over HTTP (D62).

`scripts/checks/image-replay.sh` sets KIBANA_MCP_SERVER_IMAGE to the image it
built; the replay then starts one container per transcript and talks to it
over streamable HTTP with `Authorization: ApiKey <seed key>` on every request —
the path every HTTP user of the published image takes, on the image's own
Python and its `uv sync --no-dev` venv. The container gets the seed's Kibana
URL and NO API key: the header is the only credential it can use.

`--network host` lets the container reach the dev stack's Kibana, which
listens on the host's loopback only. The server binds 127.0.0.1 on a free
port, so two runs never collide. A loader-parsed helper, never a test module.
"""

import contextlib
import socket
import subprocess
import time
import urllib.error
import urllib.request
import uuid

READY_S = 30


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _docker(*args, check=True):
    return subprocess.run(["docker", *args], capture_output=True, text=True, check=check)


def _answers(url):
    try:
        urllib.request.urlopen(url, timeout=2)  # noqa: S310 - a fixed loopback URL
    except urllib.error.HTTPError:
        return True  # any HTTP status: the server is listening
    except OSError:
        return False
    return True


@contextlib.contextmanager
def image_server(image, env):
    """Yield the MCP URL of a container of `image` started with `env`; the
    container is removed on every path, its logs attached to a start failure."""
    port = free_port()
    name = f"mcp-for-kibana-replay-{uuid.uuid4().hex[:8]}"
    full_env = {**env, "KIBANA_MCP_HOST": "127.0.0.1", "KIBANA_MCP_PORT": str(port)}
    args = ["run", "-d", "--name", name, "--network", "host"]
    for key, value in full_env.items():
        args += ["-e", f"{key}={value}"]
    started = _docker(*args, image, check=False)
    if started.returncode != 0:
        raise RuntimeError(
            f"docker run {image} failed: {started.stderr.strip()} — build it first: "
            f"scripts/checks/image-build.sh {image}"
        )
    try:
        deadline = time.monotonic() + READY_S
        while not _answers(f"http://127.0.0.1:{port}/"):
            running = _docker("inspect", "-f", "{{.State.Running}}", name, check=False)
            if running.stdout.strip() != "true" or time.monotonic() > deadline:
                logs = _docker("logs", name, check=False)
                raise RuntimeError(
                    f"the {image} container did not answer HTTP on :{port} within "
                    f"{READY_S}s; its logs:\n{logs.stdout}{logs.stderr}"
                )
            time.sleep(0.5)
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        _docker("rm", "-f", name, check=False)
