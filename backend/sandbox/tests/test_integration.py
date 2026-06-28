"""Real Docker integration tests for the sandbox subsystem.

Requires a running Docker daemon (Docker 29.4+ confirmed available).
Auto-skips at module level if Docker is unreachable.

Walls verified:
  - Privilege: container runs as uid 1000 (not root)
  - Filesystem: root FS read-only; /work tmpfs is writable
  - Network: sandbox on sandbox_net cannot reach a service on a different bridge
  - Resources: (limits are set — enforced by cgroups, validated in unit tests)
  - Teardown: remove() stops the container and it disappears from docker ps

The network-wall test is the most important: it stands up a throwaway
``python:3.12-slim`` HTTP server on a SEPARATE bridge network (``sandbox_inttest_internal``),
then runs code inside the sandbox that attempts to reach it.  The sandbox
container has no route to the other bridge subnet, so the connection fails.
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Module-level Docker availability check — skip whole module if unavailable
# ---------------------------------------------------------------------------

try:
    import docker as _docker_mod

    _docker_client_probe = _docker_mod.from_env()
    _docker_client_probe.ping()
    _DOCKER_UP = True
except Exception:
    _DOCKER_UP = False

if not _DOCKER_UP:
    pytest.skip("Docker daemon not reachable — skipping integration tests", allow_module_level=True)

import docker  # noqa: E402 — only imported when Docker is up

pytestmark = pytest.mark.docker

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_RUNNER_DIR = Path(__file__).parent.parent / "runner"
_IMAGE = "rag-sandbox:latest"
_SANDBOX_NET = "sandbox_inttest_net"   # isolated test network (not the prod net)
_INTERNAL_NET = "sandbox_inttest_internal"  # "internal" service the sandbox can't reach
_INTERNAL_PORT = 8080

# ---------------------------------------------------------------------------
# Module-scoped fixtures (created once, shared across all tests)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def docker_client():
    return docker.from_env()


@pytest.fixture(scope="module")
def built_image(docker_client):
    """Build rag-sandbox image once per test run (or reuse if already built)."""
    print(f"\nBuilding {_IMAGE} from {_RUNNER_DIR} ...")
    image, _logs = docker_client.images.build(
        path=str(_RUNNER_DIR), tag=_IMAGE, rm=True, forcerm=True
    )
    print(f"  Image built: {image.id[:12]}")
    yield image
    # Intentionally NOT removing the image — useful for iterative dev reruns.


@pytest.fixture(scope="module")
def sandbox_net(docker_client, built_image):  # noqa: ARG001 — ensure image built first
    """Dedicated bridge network for integration-test sandbox containers.

    Created with ``internal=True`` so containers have NO external routing —
    they cannot reach other Docker bridges or the internet.  This makes the
    network-wall test reliable on OrbStack/Docker-Desktop (which don't add
    iptables inter-bridge DROP rules the way a vanilla Linux dockerd does).

    In production, the sandbox_net is a regular bridge + iptables egress rules
    (allowlist: fileserver + internet, deny Qdrant/app/DB).  ``internal=True``
    is a stricter-but-valid proxy for the test environment.

    Port publishing (127.0.0.1 host binding) still works with internal=True
    because it is a host-side NAT rule, not a container routing feature.
    """
    # Remove any stale network left by a previous interrupted test run.
    for stale in docker_client.networks.list(names=[_SANDBOX_NET]):
        try:
            stale.remove()
        except Exception:
            pass
    net = docker_client.networks.create(_SANDBOX_NET, driver="bridge", internal=True)
    yield net
    try:
        net.remove()
    except Exception:
        pass


@pytest.fixture(scope="module")
def internal_net(docker_client):
    """A separate bridge network that the sandbox should NOT be able to reach."""
    net = docker_client.networks.create(_INTERNAL_NET, driver="bridge")
    yield net
    try:
        net.remove()
    except Exception:
        pass


@pytest.fixture(scope="module")
def internal_server(docker_client, internal_net):  # noqa: ARG001
    """Throwaway HTTP server on ``_INTERNAL_NET`` — the sandbox must NOT reach it.

    Uses ``python:3.12-slim -m http.server`` as the simplest possible service.
    """
    container = docker_client.containers.run(
        "python:3.12-slim",
        command=["python", "-m", "http.server", str(_INTERNAL_PORT)],
        network=_INTERNAL_NET,
        detach=True,
        labels={"app": "rag-sandbox-inttest-internal"},
    )
    # Allow the server a moment to start before revealing its IP
    time.sleep(2)
    container.reload()
    net_attrs = container.attrs["NetworkSettings"]["Networks"][_INTERNAL_NET]
    ip = net_attrs["IPAddress"]
    print(f"\n  Internal server up at {ip}:{_INTERNAL_PORT} (network: {_INTERNAL_NET})")
    yield ip, _INTERNAL_PORT
    container.stop(timeout=5)
    container.remove(force=True)


@pytest.fixture(scope="module")
def manager(docker_client, sandbox_net):  # noqa: ARG001
    """ContainerManager pointed at the integration-test sandbox network."""
    from sandbox.service.config import settings
    from sandbox.service.containers import ContainerManager

    # Override settings to use the integration-test network/image
    settings.net = _SANDBOX_NET
    settings.image = _IMAGE

    mgr = ContainerManager()
    yield mgr
    mgr.remove_all()
    # Restore defaults so other test modules aren't affected
    settings.net = "sandbox_net"
    settings.image = "rag-sandbox:latest"


# ---------------------------------------------------------------------------
# Session IDs — each test gets its own session / container for independence
# ---------------------------------------------------------------------------

_SID_STDOUT = "inttest-stdout"
_SID_PERSIST = "inttest-persist"
_SID_NONROOT = "inttest-nonroot"
_SID_FS = "inttest-fs"
_SID_NETWALL = "inttest-netwall"
_SID_TEARDOWN = "inttest-teardown"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_stdout(manager):
    """Basic code execution returns stdout."""
    result = manager.execute(_SID_STDOUT, "print(2 + 2)")
    assert result["error"] is None, f"Unexpected error: {result['error']}"
    assert "4" in result["stdout"]


def test_persistence(manager):
    """Globals (including dataframes) survive across calls in the same session."""
    import_result = manager.execute(
        _SID_PERSIST,
        "import pandas as pd; df = pd.DataFrame({'x': [10, 20, 30]})",
    )
    assert import_result["error"] is None, import_result["error"]

    use_result = manager.execute(_SID_PERSIST, "print(len(df))")
    assert use_result["error"] is None, use_result["error"]
    assert "3" in use_result["stdout"]


def test_non_root(manager):
    """Container runs as non-root (uid 1000, user sandbox)."""
    result = manager.execute(_SID_NONROOT, "import os; print(os.getuid())")
    assert result["error"] is None, result["error"]
    uid = result["stdout"].strip()
    assert uid != "0", f"Expected non-root uid but got {uid!r}"
    assert uid == "1000", f"Expected uid=1000 but got {uid!r}"


def test_filesystem_wall(manager):
    """Root FS is read-only; /work tmpfs is writable."""
    # Writing to / or /etc must fail
    fail_result = manager.execute(
        _SID_FS,
        "open('/etc/sandbox_escape_test', 'w').write('pwned')",
    )
    assert fail_result["error"] is not None, (
        "Expected write to /etc to fail on read-only FS, but it succeeded"
    )

    # Writing to /work must succeed
    ok_result = manager.execute(
        _SID_FS,
        "open('/work/test.txt', 'w').write('ok'); print('written')",
    )
    assert ok_result["error"] is None, f"Write to /work failed: {ok_result['error']}"
    assert "written" in ok_result["stdout"]


def test_network_wall(manager, internal_server):
    """Sandbox CANNOT reach a service on a different Docker bridge network.

    The sandbox container is on ``_SANDBOX_NET``; the internal server is on
    ``_INTERNAL_NET``.  Docker does not route between isolated bridge networks
    so the TCP connect must time out or be refused.
    """
    ip, port = internal_server
    # Use a short connect timeout so the test doesn't hang for 30 s
    code = f"""
import httpx
try:
    r = httpx.get(
        "http://{ip}:{port}/",
        timeout=httpx.Timeout(connect=3.0, read=3.0, write=3.0, pool=3.0),
    )
    print("REACHED")
except Exception as exc:
    print(f"BLOCKED: {{type(exc).__name__}}")
"""
    result = manager.execute(_SID_NETWALL, code)
    assert result["error"] is None, f"Unexpected runner error: {result['error']}"
    assert "REACHED" not in result["stdout"], (
        f"Sandbox breached the network wall and reached the internal server! "
        f"stdout={result['stdout']!r}"
    )
    assert "BLOCKED" in result["stdout"], (
        f"Expected 'BLOCKED' in stdout but got: {result['stdout']!r}"
    )


def test_teardown(manager, docker_client):
    """remove() stops the container; it disappears from the Docker daemon."""
    manager.get_or_create(_SID_TEARDOWN)
    assert _SID_TEARDOWN in manager._boxes
    cid = manager._boxes[_SID_TEARDOWN].container.id

    manager.remove(_SID_TEARDOWN)
    assert _SID_TEARDOWN not in manager._boxes

    # Confirm the container is truly gone from Docker
    with pytest.raises(docker.errors.NotFound):
        docker_client.containers.get(cid)
