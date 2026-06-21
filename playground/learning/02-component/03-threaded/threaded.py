"""
Threaded components — each component runs in its own event loop / thread.

The bus is shared; messages flow across thread boundaries transparently.
The cross-thread dispatcher (OidBase._make_thread_dispatcher) routes each
incoming message to the correct event loop using asyncio.run_coroutine_threadsafe
or loop.call_soon_threadsafe.

Scenario:
  - Ticker runs in thread A: emits a 'tick' notice every 0.1 s
  - Counter runs in thread B: counts received ticks, stops after 5
"""
import asyncio
import sys
import os
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))

from noid.core.bus import Bus
from noid.core.component import Noid, OidComponent


bus = Bus()
stop_signal = threading.Event()


# --- Ticker: publishes a tick every 0.1 s ---

@Noid.component({
    "id": "ex:ticker",
    "publish": "tick~ticker/tick",
})
class TickerOid(OidComponent):
    async def run(self) -> None:
        for i in range(5):
            await asyncio.sleep(0.1)
            print(f"  [ticker / thread {threading.current_thread().name}] emitting tick {i + 1}")
            await self._notify("tick", {"n": i + 1})


# --- Counter: counts incoming ticks ---

@Noid.component({
    "id": "ex:counter",
    "subscribe": "ticker/tick~on_tick",
    "receive": ["on_tick"],
})
class CounterOid(OidComponent):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._count = 0

    async def handle_on_tick(self, notice: str, message: dict) -> None:
        self._count += 1
        print(
            f"  [counter / thread {threading.current_thread().name}] "
            f"tick #{message['n']}  total={self._count}"
        )
        if self._count >= 5:
            stop_signal.set()


# --- Main ---

def main() -> None:
    ticker = TickerOid(bus=bus)
    counter = CounterOid(bus=bus)

    print("Starting components in separate threads …")
    counter.start_in_thread()
    ticker.start_in_thread()

    # Drive the ticker's run() coroutine from the main thread
    future = asyncio.run_coroutine_threadsafe(ticker.run(), ticker._loop)
    future.result(timeout=5)

    stop_signal.wait(timeout=5)

    ticker.stop_thread()
    counter.stop_thread()
    ticker.join_thread(timeout=2)
    counter.join_thread(timeout=2)

    print(f"\nDone. Counter received {counter._count} tick(s).")


if __name__ == "__main__":
    main()
