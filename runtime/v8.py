from __future__ import annotations

import asyncio
import hashlib
import json
import os
import platform
import shutil
import subprocess
import tarfile
import time
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from .config import Settings
from .schemas import NodeResponse


class V8Unavailable(RuntimeError):
    pass


NODE_VERSION = "22.16.0"
NODE_DISTRIBUTIONS = {
    "x86_64": ("linux-x64", "f4cb75bb036f0d0eddf6b79d9596df1aaab9ddccd6a20bf489be5abe9467e84e"),
    "amd64": ("linux-x64", "f4cb75bb036f0d0eddf6b79d9596df1aaab9ddccd6a20bf489be5abe9467e84e"),
    "aarch64": ("linux-arm64", "eab80cb88f8fda1e65f5e8d0420c9809bdb320b03fd34976ab7161b6e703b910"),
    "arm64": ("linux-arm64", "eab80cb88f8fda1e65f5e8d0420c9809bdb320b03fd34976ab7161b6e703b910"),
}


def _node_major(binary: str) -> int:
    try:
        version = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=5, check=True).stdout.strip()
        return int(version.removeprefix("v").split(".")[0])
    except (OSError, ValueError, subprocess.SubprocessError):
        return 0


class NodeBootstrap:
    def __init__(self, settings: Settings):
        self.settings = settings

    def resolve(self) -> str:
        if _node_major(self.settings.node_binary) >= 22:
            return self.settings.node_binary
        machine = platform.machine().lower()
        if machine not in NODE_DISTRIBUTIONS:
            raise V8Unavailable(f"unsupported Node.js platform: {machine}")
        flavor, expected_sha = NODE_DISTRIBUTIONS[machine]
        filename = f"node-v{NODE_VERSION}-{flavor}.tar.xz"
        cache_dir = self.settings.data_root / "runtime" / "node"
        cache_dir.mkdir(parents=True, exist_ok=True)
        archive = cache_dir / filename
        if not archive.exists() or self._sha256(archive) != expected_sha:
            temp_archive = archive.with_suffix(".download")
            temp_archive.unlink(missing_ok=True)
            url = f"https://nodejs.org/dist/v{NODE_VERSION}/{filename}"
            with urllib.request.urlopen(url, timeout=120) as response, temp_archive.open("wb") as handle:
                shutil.copyfileobj(response, handle)
            if self._sha256(temp_archive) != expected_sha:
                temp_archive.unlink(missing_ok=True)
                raise V8Unavailable("downloaded Node.js archive checksum mismatch")
            os.replace(temp_archive, archive)
        local_root = Path("/tmp") / f"customer-ai-node-v{NODE_VERSION}-{flavor}"
        binary = local_root / "bin" / "node"
        if _node_major(str(binary)) >= 22:
            return str(binary)
        temp_root = local_root.with_name(local_root.name + f".tmp-{os.getpid()}")
        shutil.rmtree(temp_root, ignore_errors=True)
        temp_root.mkdir(parents=True, exist_ok=True)
        with tarfile.open(archive, mode="r:xz") as package:
            members = package.getmembers()
            prefix = f"node-v{NODE_VERSION}-{flavor}/"
            for member in members:
                if not member.name.startswith(prefix) or ".." in Path(member.name).parts:
                    raise V8Unavailable("unsafe Node.js archive path")
            package.extractall(temp_root, filter="data")
        extracted = temp_root / f"node-v{NODE_VERSION}-{flavor}"
        shutil.rmtree(local_root, ignore_errors=True)
        os.replace(extracted, local_root)
        shutil.rmtree(temp_root, ignore_errors=True)
        binary.chmod(0o755)
        if _node_major(str(binary)) < 22:
            raise V8Unavailable("bootstrapped Node.js runtime failed validation")
        return str(binary)

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()


class V8Supervisor:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.process: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()
        self.node_binary = settings.node_binary

    async def start(self) -> None:
        async with self._lock:
            if self.process and self.process.returncode is None:
                return
            self.node_binary = await asyncio.to_thread(NodeBootstrap(self.settings).resolve)
            self.settings.node_socket.unlink(missing_ok=True)
            script = Path(__file__).resolve().parent.parent / "v8" / "server.mjs"
            env = os.environ.copy()
            env["CUSTOMER_AI_NODE_SOCKET"] = str(self.settings.node_socket)
            self.process = await asyncio.create_subprocess_exec(
                self.node_binary,
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
