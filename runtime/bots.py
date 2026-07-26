from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Awaitable, Callable

LOG = logging.getLogger("customer-ai.bots")
Routine = Callable[[], Awaitable[object]]


@dataclass(frozen=True, slots=True)
class BotContract:
    bot_id: str
    interval_seconds: int
    purpose: str
    side_effect: str = "none"


class RoutineBotSupervisor:
    """Runs deterministic routine bots. Bots never call the language engine."""

    def __init__(self) -> None:
        self._routines: dict[str, tuple[BotContract, Routine]] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._stopping = asyncio.Event()
        self._last_status: dict[str, dict[str, object]] = {}

    def register(self, contract: BotContract, routine: Routine) -> None:
        if not contract.bot_id.startswith("$bot."):
            raise ValueError("bot id must start with '$bot.'")
        if contract.bot_id in self._routines:
            raise ValueError(f"duplicate bot id: {contract.bot_id}")
        if contract.interval_seconds < 10:
            raise ValueError("bot interval must be at least 10 seconds")
        self._routines[contract.bot_id] = (contract, routine)

    async def start(self) -> None:
        self._stopping.clear()
        for bot_id, (contract, routine) in self._routines.items():
            if bot_id in self._tasks and not self._tasks[bot_id].done():
                continue
            self._tasks[bot_id] = asyncio.create_task(self._loop(contract, routine), name=f"customer-ai:{bot_id}")

    async def stop(self) -> None:
        self._stopping.set()
        for task in self._tasks.values():
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        self._tasks.clear()

    async def run_once(self, bot_id: str) -> object:
        contract, routine = self._routines[bot_id]
        return await self._run(contract, routine)

    def status(self) -> dict[str, object]:
        return {
            "registered": sorted(self._routines),
            "running": sorted(bot_id for bot_id, task in self._tasks.items() if not task.done()),
            "last": self._last_status,
        }

    async def _loop(self, contract: BotContract, routine: Routine) -> None:
        while not self._stopping.is_set():
            await self._run(contract, routine)
            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=contract.interval_seconds)
            except TimeoutError:
                continue

    async def _run(self, contract: BotContract, routine: Routine) -> object:
        started = asyncio.get_running_loop().time()
        try:
            result = await routine()
            self._last_status[contract.bot_id] = {"ok": True, "duration_ms": int((asyncio.get_running_loop().time() - started) * 1000)}
            return result
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._last_status[contract.bot_id] = {"ok": False, "duration_ms": int((asyncio.get_running_loop().time() - started) * 1000), "error": type(exc).__name__}
            LOG.warning("routine bot failed: %s: %s", contract.bot_id, exc)
            return None
