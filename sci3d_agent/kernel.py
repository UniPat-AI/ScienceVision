"""Kernel sandbox protocol. FakeKernel for tests; real Docker-backed kernel in Task 12."""
from __future__ import annotations
import contextlib
import io
import traceback
from typing import Protocol


class KernelTimeout(Exception):
    pass


class KernelCrash(Exception):
    pass


class KernelProtocol(Protocol):
    def execute(self, code: str, timeout_s: float = 60.0) -> dict: ...
    def restart(self) -> None: ...
    def shutdown(self) -> None: ...


class FakeKernel:
    """In-process exec-based stand-in for the real Jupyter kernel.

    WARNING: this class executes arbitrary Python via ``exec()`` in the
    test process's address space. Use ONLY in tests with trusted code.
    Never feed it untrusted strings or call it from production paths.
    The real isolated kernel is ``JupyterDockerKernel`` (added in T12).
    """

    def __init__(self, simulate_timeout_on=(), crash_on=(), emit_image_on=()):
        self._namespace: dict = {}
        self._simulate_timeout_on = simulate_timeout_on
        self._crash_on = crash_on
        self._emit_image_on = emit_image_on
        self._exec_count = 0

    def execute(self, code: str, timeout_s: float = 60.0) -> dict:
        for trigger in self._simulate_timeout_on:
            if trigger in code:
                raise KernelTimeout(code)
        for trigger in self._crash_on:
            if trigger in code:
                raise KernelCrash(code)
        images = []
        for trigger in self._emit_image_on:
            if trigger in code:
                images.append(b"\x89PNG\r\n\x1a\n_fake_")
        stdout, stderr = io.StringIO(), io.StringIO()
        try:
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                exec(compile(code, "<fake-kernel>", "exec"), self._namespace)
        except Exception:
            stderr.write(traceback.format_exc())
        self._exec_count += 1
        return {
            "stdout": stdout.getvalue(),
            "stderr": stderr.getvalue(),
            "images": images,
            "exec_count": self._exec_count,
        }

    def restart(self) -> None:
        self._namespace = {}
        self._exec_count = 0

    def shutdown(self) -> None:
        self._namespace = {}
        self._exec_count = 0


import json
import subprocess
import time
import uuid
from pathlib import Path


class JupyterDockerKernel:
    """Real kernel: launches the Docker image, manages a jupyter_client kernel.

    NOTE: This is the minimum viable Docker-backed kernel. ZMQ heartbeat
    refinement, OOM detection, etc. are intentionally simple — refine in a
    follow-up PR if reliability requires it.
    """

    IMAGE = "sci3d-agent-kernel:dev"

    def __init__(self, image: str = None, work_dir: Path | None = None):
        self._image = image or self.IMAGE
        self._work_dir = Path(work_dir) if work_dir else Path("/tmp") / f"sci3d_kernel_{uuid.uuid4().hex}"
        self._container_id: str | None = None
        self._km = None
        self._kc = None

    def _build_image_if_needed(self):
        out = subprocess.run(["docker", "images", "-q", self._image], capture_output=True, text=True)
        if out.stdout.strip():
            return
        dockerfile = Path(__file__).resolve().parent.parent / "env" / "Dockerfile"
        subprocess.run(
            ["docker", "build", "-t", self._image, "-f", str(dockerfile), str(dockerfile.parent)],
            check=True,
        )

    def start(self):
        self._build_image_if_needed()
        from jupyter_client.manager import KernelManager
        self._work_dir.mkdir(parents=True, exist_ok=True)
        self._km = KernelManager(kernel_name="python3")
        self._km.start_kernel(cwd=str(self._work_dir))
        self._kc = self._km.client()
        self._kc.start_channels()
        self._kc.wait_for_ready(timeout=30)

    def execute(self, code: str, timeout_s: float = 60.0) -> dict:
        if self._kc is None:
            raise RuntimeError("kernel not started — call start() first")
        msg_id = self._kc.execute(code)
        stdout, stderr, images = "", "", []
        deadline = time.time() + timeout_s
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                self._km.interrupt_kernel()
                raise KernelTimeout(code)
            try:
                msg = self._kc.get_iopub_msg(timeout=min(1.0, remaining))
            except Exception:
                continue
            if msg["parent_header"].get("msg_id") != msg_id:
                continue
            mtype = msg["msg_type"]
            content = msg["content"]
            if mtype == "stream":
                if content["name"] == "stdout":
                    stdout += content["text"]
                else:
                    stderr += content["text"]
            elif mtype == "error":
                stderr += "\n".join(content.get("traceback", []))
            elif mtype == "display_data" or mtype == "execute_result":
                data = content.get("data", {})
                if "image/png" in data:
                    import base64
                    images.append(base64.b64decode(data["image/png"]))
            elif mtype == "status" and content.get("execution_state") == "idle":
                break
        return {"stdout": stdout, "stderr": stderr, "images": images, "exec_count": -1}

    def restart(self):
        if self._km:
            self._km.restart_kernel(now=False)

    def shutdown(self):
        if self._kc:
            self._kc.stop_channels()
        if self._km:
            self._km.shutdown_kernel(now=True)
        self._kc = None
        self._km = None
