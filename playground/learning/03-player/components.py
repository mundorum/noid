"""
Example noid components for the 03-player playground.

This module is imported by scene files via the "imports" section:

    "imports": ["./components.py"]

The path is resolved relative to the scene file by NoidPlayer, so every
example in 03-player/*/scene.json can reference the same shared file.

Components defined here mirror the spirit of JS oid UI components
(button-oid, console-oid, file-oid) but live purely on the server/desktop:

  ex:timer    — emits 'tick' at a fixed interval; 'done' after N ticks
  ex:logger   — prints any received message to stdout
  ex:counter  — counts received messages and prints a running total
  ex:store    — provides itf:transfer; stores received values in memory
  ex:sender   — connects to itf:transfer and sends its 'value' on start
"""
import asyncio
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))

from noid.core.component import Noid, OidComponent


# ---------------------------------------------------------------------------
# ex:timer
#
# Properties:
#   interval  float  seconds between ticks    (default 1.0)
#   count     int    ticks before stopping;   (default 5)
#                    0 = run forever
#   label     str    tag included in messages (default "timer")
#
# Notices produced (map with "publish" in the scene):
#   tick   {"n": <int>, "label": <str>}
#   done   {}
# ---------------------------------------------------------------------------

@Noid.component({
    "id": "ex:timer",
    "properties": {
        "interval": {"default": 1.0},
        "count":    {"default": 5},
        "label":    {"default": "timer"},
    },
})
class TimerOid(OidComponent):
    async def start(self) -> None:
        await super().start()
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        task = getattr(self, "_task", None)
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        await super().stop()

    async def _run(self) -> None:
        n = 0
        while True:
            await asyncio.sleep(self.interval)
            n += 1
            await self._notify("tick", {"n": n, "label": self.label})
            if self.count > 0 and n >= self.count:
                await self._notify("done", {})
                break


# ---------------------------------------------------------------------------
# ex:logger
#
# Properties:
#   prefix  str  tag printed before each message (default "[log]")
#
# Receives (must be wired from the scene via "subscribe"):
#   log  — any message
# ---------------------------------------------------------------------------

@Noid.component({
    "id": "ex:logger",
    "properties": {"prefix": {"default": "[log]"}},
    "receive": ["log"],
})
class LoggerOid(OidComponent):
    def handle_log(self, notice: str, message: dict) -> None:
        print(f"{self.prefix} {message}")


# ---------------------------------------------------------------------------
# ex:counter
#
# Properties:
#   prefix  str  tag printed before each count line (default "[count]")
#
# Receives (must be wired from the scene via "subscribe"):
#   count  — increments internal counter; prints running total
# ---------------------------------------------------------------------------

@Noid.component({
    "id": "ex:counter",
    "properties": {"prefix": {"default": "[count]"}},
    "receive": ["count"],
})
class CounterOid(OidComponent):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._total = 0

    def handle_count(self, notice: str, message: dict) -> None:
        self._total += 1
        print(f"{self.prefix} #{self._total}  {message}")


# ---------------------------------------------------------------------------
# Interface for the connect examples
# ---------------------------------------------------------------------------

Noid.c_interface({
    "id": "itf:transfer",
    "operations": {"send": {}},
})


# ---------------------------------------------------------------------------
# ex:store
#
# Provides the itf:transfer interface; logs and stores each received value.
# The component_id (set via "id" in the scene) is required for connect wiring.
# ---------------------------------------------------------------------------

@Noid.component({
    "id": "ex:store",
    "provide": ["itf:transfer"],
})
class StoreOid(OidComponent):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._values: list = []

    def handle_send(self, notice: str, message: dict) -> dict:
        self._values.append(message.get("value"))
        print(f"[store] received → {message.get('value')!r}  "
              f"(total stored: {len(self._values)})")
        return {"stored": True}


# ---------------------------------------------------------------------------
# ex:sender
#
# Properties:
#   value  any  value to forward to the connected provider (default "hello")
#
# On start(), sends its value to a connected itf:transfer provider, then
# publishes 'done' so the player can stop when wired with
#   "publish": "done~player/done"
# ---------------------------------------------------------------------------

@Noid.component({
    "id": "ex:sender",
    "properties": {"value": {"default": "hello"}},
})
class SenderOid(OidComponent):
    async def start(self) -> None:
        await super().start()
        if self._connected.get("itf:transfer"):
            await self._invoke("itf:transfer", "send", {"value": self.value})
        await self._notify("done", {})
