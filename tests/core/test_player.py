"""Tests for NoidPlayer — scene loading, component lifecycle, and wiring."""
import asyncio
import json
import pathlib
import threading

import pytest

from noid.core.bus import Bus
from noid.core.component import Noid, OidComponent
from noid.core.player import NoidPlayer


def fresh_bus() -> Bus:
    return Bus()


# ---------------------------------------------------------------------------
# Minimal test components
# ---------------------------------------------------------------------------

@Noid.component({"id": "pl:sink", "receive": ["msg"]})
class SinkOid(OidComponent):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.log: list = []

    def handle_msg(self, notice, message):
        self.log.append(message)


@Noid.component({"id": "pl:source"})
class SourceOid(OidComponent):
    async def emit(self, notice, message):
        await self._notify(notice, message)


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def test_load_from_dict() -> None:
    player = NoidPlayer(bus=fresh_bus())
    player.load({"title": "Dict scene", "components": [{"type": "pl:sink"}]})
    assert player.title == "Dict scene"
    assert len(player._components) == 1
    assert isinstance(player._components[0], OidComponent)


def test_load_from_json_string() -> None:
    player = NoidPlayer(bus=fresh_bus())
    player.load(json.dumps({"components": [{"type": "pl:sink"}]}))
    assert len(player._components) == 1


def test_load_from_file(tmp_path: pathlib.Path) -> None:
    scene_file = tmp_path / "scene.json"
    scene_file.write_text(json.dumps({"title": "File scene", "components": [{"type": "pl:sink"}]}))
    player = NoidPlayer(bus=fresh_bus())
    player.load(scene_file)
    assert player.title == "File scene"
    assert len(player._components) == 1


def test_register_section_creates_component_type() -> None:
    player = NoidPlayer(bus=fresh_bus())
    player.load({
        "register": [{"id": "pl:dyn-test", "properties": {"x": {"default": 1}}}],
        "components": [{"type": "pl:dyn-test"}],
    })
    assert "pl:dyn-test" in Noid._oid_reg
    assert len(player._components) == 1


def test_interfaces_section_registers_interface() -> None:
    player = NoidPlayer(bus=fresh_bus())
    player.load({"interfaces": [{"id": "itf:pl-test", "operations": {"go": {}}}]})
    assert Noid.get_interface("itf:pl-test") is not None


def test_component_id_assigned() -> None:
    player = NoidPlayer(bus=fresh_bus())
    player.load({"components": [{"type": "pl:sink", "id": "sink-a"}]})
    assert player._components[0].component_id == "sink-a"


def test_component_properties_applied() -> None:
    @Noid.component({"id": "pl:prop-test", "properties": {"prefix": {"default": "[x]"}}})
    class PropTest(OidComponent):
        pass

    player = NoidPlayer(bus=fresh_bus())
    player.load({"components": [{"type": "pl:prop-test", "properties": {"prefix": ">>>"}}]})
    assert player._components[0].prefix == ">>>"


def test_import_python_file(tmp_path: pathlib.Path) -> None:
    """Scene can import a .py file that registers a component."""
    comp_file = tmp_path / "mycomps.py"
    comp_file.write_text(
        "import sys, os\n"
        "sys.path.insert(0, os.path.join(os.path.dirname(__file__), "
        "    *(['..'] * 6)))\n"
        "from noid.core.component import Noid, OidComponent\n"
        "@Noid.component({'id': 'pl:file-import-test'})\n"
        "class _C(OidComponent): pass\n"
    )
    scene_file = tmp_path / "scene.json"
    scene_file.write_text(json.dumps({
        "imports": ["./mycomps.py"],
        "components": [{"type": "pl:file-import-test"}],
    }))
    player = NoidPlayer(bus=fresh_bus())
    player.load(scene_file)
    assert "pl:file-import-test" in Noid._oid_reg
    assert len(player._components) == 1


# ---------------------------------------------------------------------------
# Lifecycle: start / stop
# ---------------------------------------------------------------------------

async def test_start_wires_components() -> None:
    bus = fresh_bus()
    received = []
    bus.subscribe("ping", lambda t, m: received.append(m))

    @Noid.component({"id": "pl:start-test", "subscribe": "ping~msg", "receive": ["msg"]})
    class StartTest(OidComponent):
        def handle_msg(self, notice, msg):
            received.append(msg)

    player = NoidPlayer(bus=bus)
    player.load({"components": [{"type": "pl:start-test"}]})
    await player.start()
    await bus.publish("ping", {"v": 1})
    assert {"v": 1} in received
    await player.stop()


async def test_stop_unsubscribes() -> None:
    bus = fresh_bus()
    received = []

    @Noid.component({"id": "pl:stop-test", "subscribe": "ping~msg", "receive": ["msg"]})
    class StopTest(OidComponent):
        def handle_msg(self, notice, msg):
            received.append(msg)

    player = NoidPlayer(bus=bus)
    player.load({"components": [{"type": "pl:stop-test"}]})
    await player.start()
    await bus.publish("ping", {"v": 1})
    assert len(received) == 1

    await player.stop()
    await bus.publish("ping", {"v": 2})
    assert len(received) == 1  # no new calls after stop


# ---------------------------------------------------------------------------
# run() — player/done stops the loop
# ---------------------------------------------------------------------------

async def test_run_stops_on_player_done() -> None:
    bus = fresh_bus()

    @Noid.component({"id": "pl:done-src"})
    class DoneSrc(OidComponent):
        async def start(self) -> None:
            await super().start()
            await self._notify("done", {})

    player = NoidPlayer(bus=bus)
    player.load({"components": [{"type": "pl:done-src", "publish": "done~player/done"}]})
    await asyncio.wait_for(player.run(), timeout=2.0)


async def test_run_timeout() -> None:
    player = NoidPlayer(bus=fresh_bus())
    player.load({"components": []})
    await player.run(timeout=0.05)   # should not hang


# ---------------------------------------------------------------------------
# Pub/sub wiring via JSON scene
# ---------------------------------------------------------------------------

async def test_pubsub_wiring_via_scene() -> None:
    bus = fresh_bus()
    received = []

    @Noid.component({"id": "pl:wired-src", "publish": "out~wired/val"})
    class WiredSrc(OidComponent):
        async def emit(self):
            await self._notify("out", {"x": 7})

    @Noid.component({"id": "pl:wired-dst", "receive": ["in"]})
    class WiredDst(OidComponent):
        def handle_in(self, notice, msg):
            received.append(msg)

    player = NoidPlayer(bus=bus)
    player.load({
        "components": [
            {"type": "pl:wired-src"},
            {"type": "pl:wired-dst", "subscribe": "wired/val~in"},
        ]
    })
    await player.start()
    await player._components[0].emit()
    assert received == [{"x": 7}]
    await player.stop()


# ---------------------------------------------------------------------------
# Auto-relay: pure-JSON component forwards without a Python handler
# ---------------------------------------------------------------------------

async def test_auto_relay_via_register() -> None:
    """A registered component with no Python handler auto-relays via publish mapping."""
    bus = fresh_bus()
    received = []
    bus.subscribe("out/relayed", lambda t, m: received.append(m))

    player = NoidPlayer(bus=bus)
    player.load({
        "register": [{
            "id":        "pl:relay-auto",
            "subscribe": "in/raw~forward",
            "publish":   "forward~out/relayed",
        }],
        "components": [{"type": "pl:relay-auto"}],
    })
    await player.start()
    await bus.publish("in/raw", {"v": 99})
    assert received == [{"v": 99}]
    await player.stop()


# ---------------------------------------------------------------------------
# Connect wiring via JSON scene
# ---------------------------------------------------------------------------

async def test_connect_wiring_via_scene() -> None:
    bus = fresh_bus()
    stored = []

    Noid.c_interface({"id": "itf:pl-store", "operations": {"put": {}}})

    @Noid.component({"id": "pl:store-comp", "provide": ["itf:pl-store"]})
    class StoreComp(OidComponent):
        def handle_put(self, notice, msg):
            stored.append(msg.get("value"))
            return {"ok": True}

    @Noid.component({"id": "pl:send-comp"})
    class SendComp(OidComponent):
        async def start(self) -> None:
            await super().start()
            if self._connected.get("itf:pl-store"):
                await self._invoke("itf:pl-store", "put", {"value": "hello"})

    player = NoidPlayer(bus=bus)
    player.load({
        "components": [
            {"id": "s1", "type": "pl:store-comp"},
            {"type": "pl:send-comp", "connect": "itf:pl-store#s1"},
        ]
    })
    await player.start()
    assert stored == ["hello"]
    await player.stop()


# ---------------------------------------------------------------------------
# Threaded component in player
# ---------------------------------------------------------------------------

def test_threaded_component_in_player() -> None:
    bus = fresh_bus()
    received = []
    done = threading.Event()

    @Noid.component({"id": "pl:thr-sink", "subscribe": "ping~msg", "receive": ["msg"]})
    class ThrSink(OidComponent):
        def handle_msg(self, notice, msg):
            received.append(msg)
            done.set()

    player = NoidPlayer(bus=bus)
    player.load({"components": [{"type": "pl:thr-sink", "threaded": True}]})
    # threaded=True → start_in_thread() already called during load()

    asyncio.run(bus.publish("ping", {"v": 42}))
    assert done.wait(timeout=2), "threaded handler never called"
    assert received == [{"v": 42}]

    asyncio.run(player.stop())
