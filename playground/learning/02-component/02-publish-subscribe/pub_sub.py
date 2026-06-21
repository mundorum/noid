"""
Two components exchanging messages through the bus.

Mirrors the JS playground pattern:
    <sensor-oid publish="change~sensor/reading">
    <display-oid subscribe="sensor/reading~show">

The sensor publishes a reading notice that gets mapped to 'sensor/reading' on the bus.
The display subscribes to 'sensor/reading' and maps it to its internal 'show' notice.

This demonstrates the full  notice → publish → subscribe → notice  flow.
"""
import asyncio
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))

from noid.core.bus import Bus
from noid.core.component import Noid, OidComponent


bus = Bus()


# --- Sensor: publishes a reading when triggered ---

@Noid.component({
    "id": "ex:sensor",
    "properties": {"value": {"default": 0}},
    "publish": "reading~sensor/reading",   # notice 'reading' → topic 'sensor/reading'
})
class SensorOid(OidComponent):
    async def measure(self, value: float) -> None:
        self.value = value
        await self._notify("reading", {"value": self.value})


# --- Display: subscribes and prints received readings ---

@Noid.component({
    "id": "ex:display",
    "subscribe": "sensor/reading~show",    # topic 'sensor/reading' → notice 'show'
    "receive": ["show"],
})
class DisplayOid(OidComponent):
    def handle_show(self, notice: str, message: dict) -> None:
        print(f"[display] received notice={notice!r}  value={message['value']}")


# --- Main ---

async def main() -> None:
    sensor = SensorOid(bus=bus)
    display = DisplayOid(bus=bus)

    await sensor.start()
    await display.start()

    for reading in [23.1, 24.5, 22.8]:
        await sensor.measure(reading)

    await sensor.stop()
    await display.stop()


asyncio.run(main())
