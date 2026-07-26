from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

from .config import Settings
from .schemas import NodeResponse


class V8Unavailable(RuntimeError):
    pass


class AsteraBootstrap:
    def __init__(self, settings: Settings):
        self.settings = settings

    def ensure(self) -> str:
        if self.settings.astera_path:
            return self.settings.astera_path
        destination = self.settings.data_root / "runtime" / "astera_v8" / self.settings.astera_commit
        marker = destination / "src" / "kagura-engine.js"
        if marker.exists():
            return str(destination)
        git = shutil.which("git")
        if not git:
            return ""
        temp = destination.with_name(destination.name + ".tmp")
        shutil.rmtree(temp, ignore_errors=True)
        temp.parent.mkdir(parents=True, exist_ok=True)
        try:
            subprocess.run(
                [git, "clone", "--filter=blob:none", "--no-checkout", self.settings.astera_repo, str(temp)],
                check=True,
                timeout=120,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            subprocess.run(
                [git, "-C", str(temp), "checkout", "--detach", self.settings.astera_commit],
                check=True,
                timeout=60,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            os.replace(temp, destination)
            return str(destination)
        except Exception:
            shutil.rmtree(temp, ignore_errors=True)
            return ""


class V8Supervisor:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.process: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()
        self.astera_path = ""

    async def start(self) -> None:
        async with self._lock:
            if self.process and self.process.returncode is None:
                return
            version = subprocess.run(
                [self.settings.node_binary, "--version"], capture_output=True, text=True, timeout=5, check=True
            ).stdout.strip()
            major = int(version.removeprefix("v").split(".")[0])
            if major < 22:
                raise V8Unavailable(f"Node.js 22+ required, got {version}")
            self.astera_path = await asyncio.to_thread(AsteraBootstrap(self.settings).ensure)
            self.settings.node_socket.unlink(missing_ok=True)
            script = Path(__file__).resolve().parent.parent / "v8" / "server.mjs"
            env = os.environ.copy()
            env.update(
                {
                    "CUSTOMER_AI_NODE_SOCKET": str(self.settings.node_socket),
                    "CUSTOMER_AI_ASTERA_PATH": self.astera_path,
                    "CUSTOMER_AI_ASTERA_COMMIT": self.settings.astera_commit,
                }
            )
            self.process = await asyncio.create_subprocess_exec(
                self.settings.node_binary,
                f"--max-old-space-size={self.settings.node_memory_mb}",
                str(script),
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            for _ in range(50):
                if self.settings.node_socket.exists():
                    return
                if self.process.returncode is not None:
                    break
                await asyncio.sleep(0.1)
            raise V8Unavailable("Node V8 socket did not become ready")

    async def stop(self) -> None:
        if not self.process:
            return
        self.process.terminate()
        try:
            await asyncio.wait_for(self.process.wait(), timeout=5)
        except TimeoutError:
            self.process.kill()
            await self.process.wait()
        self.process = None
        self.settings.node_socket.unlink(missing_ok=True)

    async def request(self, phase: str, payload: dict[str, Any], *, timeout: float = 15.0) -> dict[str, Any]:
        if not self.process or self.process.returncode is not None:
            await self.start()
        request_id = str(uuid.uuid4())
        request = {
            "request_id": request_id,
            "phase": phase,
            "deadline_at": int((time.time() + timeout) * 1000),
            "payload": payload,
        }
        try:
            reader, writer = await asyncio.open_unix_connection(str(self.settings.node_socket))
            writer.write((json.dumps(request, ensure_ascii=False) + "\n").encode())
            await writer.drain()
            raw = await asyncio.wait_for(reader.readline(), timeout=timeout)
            writer.close()
            await writer.wait_closed()
            response = NodeResponse.model_validate_json(raw)
            if not response.ok:
                raise V8Unavailable(response.error_code or "node_error")
            return response.result
        except (ConnectionError, OSError, TimeoutError, ValueError) as exc:
            await self.stop()
            raise V8Unavailable(str(exc)) from exc
