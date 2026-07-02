"""Per-conversation container lifecycle via docker-py.

``ContainerManager`` owns creation, health-waiting, routing, idle reaping,
and teardown.  The four isolation walls are applied in ``get_or_create``.

``_wait_ready`` uses two strategies for reaching the runner:
  1. Published host port (standard Docker on Linux, non-internal networks).
  2. Direct container IP (OrbStack; also works for ``--internal`` networks where
     OrbStack routes container IPs to the host even without port publishing).
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

import docker
import docker.errors
import httpx

from .config import settings

log = logging.getLogger(__name__)


@dataclass
class _Box:
    """Tracks one live sandbox container."""

    container: docker.models.containers.Container
    endpoint: str  # "http://host:port" — either published port or direct container IP
    last_used: float = field(default_factory=time.monotonic)


class ContainerManager:
    """Manages per-session sandbox containers."""

    def __init__(self) -> None:
        self._client: docker.DockerClient = docker.from_env()
        self._boxes: dict[str, _Box] = {}

    # ------------------------------------------------------------------
    # Network
    # ------------------------------------------------------------------

    def ensure_network(self) -> None:
        """Create the ``sandbox_net`` bridge network if it does not exist."""
        if not self._client.networks.list(names=[settings.net]):
            self._client.networks.create(settings.net, driver="bridge")
            log.info("Created docker network %s", settings.net)

    # ------------------------------------------------------------------
    # Container lifecycle
    # ------------------------------------------------------------------

    def get_or_create(self, session_id: str) -> _Box:
        """Return the live box for *session_id*, creating one if absent."""
        box = self._boxes.get(session_id)
        if box is not None:
            box.last_used = time.monotonic()
            return box

        container = self._client.containers.run(
            settings.image,
            detach=True,
            # Wall: network — isolated bridge, no route to Qdrant/app/DB
            network=settings.net,
            # Wall: privilege — non-root user, drop all capabilities.
            # NOTE: `no-new-privileges` is intentionally NOT set. On some
            # kernel/runc combinations it makes the runner's execve fail with
            # EPERM ("operation not permitted"). It's redundant here anyway:
            # the container already runs as a non-root user with cap_drop=ALL,
            # so there is no setuid path left to escalate through.
            user="sandbox",
            cap_drop=["ALL"],
            # Wall: resources — mem/CPU/pids caps
            mem_limit=settings.mem,
            nano_cpus=int(settings.cpus * 1e9),
            pids_limit=settings.pids,
            # Wall: filesystem — read-only root; /work + /tmp are writable tmpfs
            # uid/gid=1000 ensures the sandbox user owns both mounts.
            read_only=True,
            tmpfs={"/work": "size=512m,uid=1000,gid=1000", "/tmp": "size=128m,uid=1000,gid=1000"},
            # Publish port to 127.0.0.1 so only the service reaches the runner
            # (works on standard Linux Docker; OrbStack falls back to direct IP).
            ports={"8000/tcp": ("127.0.0.1", None)},
            labels={"app": "rag-sandbox", "session": session_id},
        )

        endpoint = self._wait_ready(container)
        box = _Box(container=container, endpoint=endpoint)
        self._boxes[session_id] = box
        log.info("Started sandbox container %s for session %s at %s",
                 container.id[:12], session_id, endpoint)
        return box

    def _wait_ready(
        self, container: docker.models.containers.Container,
        timeout: float = 30.0, interval: float = 0.5,
    ) -> str:
        """Poll ``/health`` until the runner is up; return the HTTP endpoint URL.

        Tries two strategies:
        1. Published host port — ``http://127.0.0.1:{port}`` — standard Linux Docker.
        2. Direct container IP — ``http://{ip}:8000`` — OrbStack / internal networks
           (OrbStack routes container IPs to the Mac host even without port publishing).
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            container.reload()
            if container.status == "exited":
                raise RuntimeError(
                    f"Container {container.id[:12]} exited unexpectedly; "
                    f"logs: {container.logs(tail=20).decode(errors='replace')}"
                )

            # Strategy 1: published host port
            bindings = container.ports.get("8000/tcp") or []
            if bindings:
                port = int(bindings[0]["HostPort"])
                candidate = f"http://127.0.0.1:{port}"
                try:
                    r = httpx.get(f"{candidate}/health", timeout=2.0)
                    if r.status_code == 200:
                        return candidate
                except Exception:
                    pass

            # Strategy 2: direct container IP (OrbStack / --internal networks)
            container.reload()
            nets = container.attrs.get("NetworkSettings", {}).get("Networks", {})
            for net_attrs in nets.values():
                ip = net_attrs.get("IPAddress", "")
                if ip:
                    candidate = f"http://{ip}:8000"
                    try:
                        r = httpx.get(f"{candidate}/health", timeout=2.0)
                        if r.status_code == 200:
                            return candidate
                    except Exception:
                        pass

            time.sleep(interval)

        raise TimeoutError(
            f"Container {container.id[:12]} did not become ready within {timeout}s"
        )

    # ------------------------------------------------------------------
    # Code execution
    # ------------------------------------------------------------------

    def execute(self, session_id: str, code: str) -> dict:
        """Route *code* to the session's container and return the runner JSON."""
        box = self.get_or_create(session_id)
        r = httpx.post(
            f"{box.endpoint}/run",
            json={"code": code},
            timeout=float(settings.run_timeout + 10),
        )
        r.raise_for_status()
        box.last_used = time.monotonic()
        return r.json()

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def remove(self, session_id: str) -> None:
        """Stop and remove the container for *session_id* (safe if absent)."""
        box = self._boxes.pop(session_id, None)
        if box is None:
            return
        try:
            box.container.stop(timeout=5)
            box.container.remove(force=True)
            log.info("Removed sandbox container for session %s", session_id)
        except docker.errors.NotFound:
            pass  # already gone
        except Exception:
            log.exception("Error removing container for session %s", session_id)

    def remove_all(self) -> None:
        """Remove all managed containers (called on shutdown)."""
        for sid in list(self._boxes):
            self.remove(sid)

    def reap_idle(self) -> None:
        """Remove containers that have been idle longer than ``idle_ttl``."""
        now = time.monotonic()
        stale = [
            sid for sid, box in self._boxes.items()
            if now - box.last_used > settings.idle_ttl
        ]
        for sid in stale:
            log.info("Reaping idle container for session %s", sid)
            self.remove(sid)
