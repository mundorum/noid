# Transport adapters

This document describes how noid connects to web frameworks. It is a companion to [architecture.md](architecture.md).

## The problem

The noid `BusBridge` needs to relay messages between the local Python bus and a remote JS oid bus over a WebSocket connection. However, WebSocket APIs differ across web frameworks. Coupling `BusBridge` to any one framework would prevent noid from running without a server, and would force a choice on all users.

## The solution: the `Connection` protocol

The core defines a single, minimal protocol that any web framework adapter must implement:

```python
# noid/transport/base.py
from typing import Protocol, AsyncIterator

class Connection(Protocol):
    async def send(self, message: dict) -> None: ...
    def __aiter__(self) -> AsyncIterator[dict]: ...   # yields inbound messages as dicts
    async def close(self) -> None: ...
```

`BusBridge` talks only to `Connection`. It never imports FastAPI or Django.

## BusBridge

```python
# noid/core/bridge.py
class BusBridge:
    def __init__(self, bus: Bus, patterns: list[str]):
        self.bus = bus
        self.patterns = patterns

    async def serve(self, conn: Connection) -> None:
        # Forward matching local-bus messages to the remote peer
        for pattern in self.patterns:
            self.bus.subscribe(
                pattern,
                lambda topic, msg: asyncio.ensure_future(
                    conn.send({"type": "publish", "topic": topic, "message": msg})
                )
            )
        # Forward inbound messages from the remote peer to the local bus
        async for frame in conn:
            if frame.get("type") == "publish":
                await self.bus.publish(frame["topic"], frame["message"])
```

---

## FastAPI adapter

**Install:** `pip install noid[fastapi]`

```python
# noid/transport/fastapi.py
import asyncio
from fastapi import WebSocket

class FastAPIConnection:
    def __init__(self, ws: WebSocket):
        self._ws = ws

    async def send(self, message: dict) -> None:
        await self._ws.send_json(message)

    def __aiter__(self):
        return self

    async def __anext__(self) -> dict:
        return await self._ws.receive_json()

    async def close(self) -> None:
        await self._ws.close()


def mount_bus_route(app, bridge: BusBridge, path: str = "/ws/bus") -> None:
    """Register the bus WebSocket route on a FastAPI app."""
    @app.websocket(path)
    async def bus_endpoint(ws: WebSocket):
        await ws.accept()
        await bridge.serve(FastAPIConnection(ws))
```

Usage in a FastAPI project:

```python
from fastapi import FastAPI
from noid.core.bus import Bus
from noid.core.bridge import BusBridge
from noid.transport.fastapi import mount_bus_route

app = FastAPI()
bus = Bus()
bridge = BusBridge(bus, patterns=["sensor/#", "ui/+/action"])
mount_bus_route(app, bridge)
```

---

## Django / Channels adapter

**Install:** `pip install noid[django]`

Django's synchronous request/response layer cannot serve WebSocket connections on its own. The adapter uses [Django Channels](https://channels.readthedocs.io/), which adds ASGI WebSocket support alongside the existing Django app. noid component logic runs in the asyncio event loop inside the Channels consumer.

```python
# noid/transport/django.py
import asyncio
import json
from channels.generic.websocket import AsyncJsonWebsocketConsumer

class _DjangoConnection:
    """Wraps a Channels consumer as a Connection."""
    def __init__(self):
        self._queue: asyncio.Queue[dict] = asyncio.Queue()

    async def send(self, message: dict) -> None:
        # Called by BusBridge; consumer.send_json must be set externally
        await self._consumer.send_json(message)

    def __aiter__(self):
        return self

    async def __anext__(self) -> dict:
        return await self._queue.get()

    async def close(self) -> None:
        await self._consumer.close()

    async def feed(self, content: dict) -> None:
        await self._queue.put(content)


class BusBridgeConsumer(AsyncJsonWebsocketConsumer):
    """
    Django Channels consumer for noid bus bridging.
    Set `bridge` at the class or per-routing level.
    """
    bridge: BusBridge = None   # must be set before use

    async def connect(self):
        await self.accept()
        self._conn = _DjangoConnection()
        self._conn._consumer = self
        self._task = asyncio.create_task(self.bridge.serve(self._conn))

    async def receive_json(self, content):
        await self._conn.feed(content)

    async def disconnect(self, code):
        self._task.cancel()
```

Wiring into Django's ASGI router (`asgi.py`):

```python
from channels.routing import ProtocolTypeRouter, URLRouter
from django.urls import path
from noid.core.bus import Bus
from noid.core.bridge import BusBridge
from noid.transport.django import BusBridgeConsumer

bus = Bus()
bridge = BusBridge(bus, patterns=["sensor/#", "ui/+/action"])
BusBridgeConsumer.bridge = bridge

application = ProtocolTypeRouter({
    "http": get_asgi_application(),
    "websocket": URLRouter([
        path("ws/bus", BusBridgeConsumer.as_asgi()),
    ]),
})
```

### Note on Django ORM

The Django ORM is synchronous. If a noid component accesses the database, wrap the ORM calls with `sync_to_async` **inside the component implementation** — not in the core or adapter:

```python
from asgiref.sync import sync_to_async

class MySensorComponent(OidComponent):
    async def handle_query(self, message):
        result = await sync_to_async(MySensorModel.objects.filter)(active=True)
        await self.bus.publish("sensor/result", {"data": list(result.values())})
```

---

## The JS side: `<bus-bridge-oid>`

On the browser side, a JS oid component named `<bus-bridge-oid>` (part of the oid library) opens the WebSocket connection and declares which topic patterns to bridge:

```html
<bus-bridge-oid url="ws://localhost:8000/ws/bus" patterns="sensor/#,ui/+/action">
</bus-bridge-oid>
```

From that point on, the JS bus and the Python bus appear as a single unified bus to all components on both sides.
