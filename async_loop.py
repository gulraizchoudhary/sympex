"""Dedicated asyncio loop for BLE operations."""
import asyncio
import threading
from typing import Any

class AsyncLoopThread:
    """Own exactly one asyncio event loop in exactly one background thread."""

    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name="PolarBLELoop",
            daemon=True,
        )
        self._thread.start()
        self._ready.wait(timeout=5.0)

    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)
        self._ready.set()
        self.loop.run_forever()

        # Best-effort cleanup if the loop is stopped during shutdown.
        pending = asyncio.all_tasks(self.loop)
        for task in pending:
            task.cancel()
        if pending:
            try:
                self.loop.run_until_complete(
                    asyncio.gather(*pending, return_exceptions=True)
                )
            except Exception:
                pass
        self.loop.close()

    def submit(self, coro: Any):
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    def stop(self) -> None:
        if self.loop.is_running():
            self.loop.call_soon_threadsafe(self.loop.stop)
