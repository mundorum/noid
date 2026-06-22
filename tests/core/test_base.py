"""Tests for OidBase lifecycle, handler dispatch, notice/topic mapping, and threading."""
import asyncio
import threading
import time

import pytest

from noid.core.bus import Bus
from noid.core.component import Noid, OidComponent


# ---------------------------------------------------------------------------
# Helpers shared across tests
# ---------------------------------------------------------------------------

def fresh_bus() -> Bus:
    return Bus()


# ---------------------------------------------------------------------------
# Noid.c_interface / get_interface
# ---------------------------------------------------------------------------

def test_c_interface_registers_and_retrieves() -> None:
    Noid.c_interface({"id": "itf:test-reg", "operations": {"go": {}}})
    spec = Noid.get_interface("itf:test-reg")
    assert spec is not None
    assert spec["id"] == "itf:test-reg"


def test_get_interface_missing_returns_none() -> None:
    assert Noid.get_interface("itf:does-not-exist") is None


# ---------------------------------------------------------------------------
# @Noid.component — registration and spec attachment
# ---------------------------------------------------------------------------

def test_decorator_attaches_spec() -> None:
    @Noid.component({"id": "ex:spec-test", "receive": ["ping"]})
    class PingOid(OidComponent):
        pass

    assert hasattr(PingOid, "_spec")
    assert PingOid._spec["id"] == "ex:spec-test"
    assert Noid._oid_reg["ex:spec-test"] is PingOid


def test_decorator_returns_class_unchanged() -> None:
    @Noid.component({"id": "ex:identity-test"})
    class MyOid(OidComponent):
        def my_method(self):
            return 42

    assert MyOid().my_method() == 42


# ---------------------------------------------------------------------------
# Noid.register — JSON-driven, no custom class
# ---------------------------------------------------------------------------

def test_register_creates_dynamic_class() -> None:
    klass = Noid.register({
        "id": "ex:json-comp",
        "properties": {"value": {"default": 7}},
    })
    assert issubclass(klass, OidComponent)
    assert Noid._oid_reg["ex:json-comp"] is klass


def test_register_class_name_derived_from_id() -> None:
    klass = Noid.register({"id": "ex:hello-world"})
    assert "Hello" in klass.__name__
    assert "World" in klass.__name__


# ---------------------------------------------------------------------------
# Noid.create
# ---------------------------------------------------------------------------

def test_create_returns_instance() -> None:
    Noid.register({"id": "ex:create-test"})
    comp = Noid.create("ex:create-test")
    assert isinstance(comp, OidComponent)


def test_create_unknown_id_raises() -> None:
    with pytest.raises(KeyError):
        Noid.create("ex:does-not-exist")


def test_create_passes_properties() -> None:
    @Noid.component({"id": "ex:prop-create", "properties": {"x": {"default": 0}}})
    class PropOid(OidComponent):
        pass

    comp = Noid.create("ex:prop-create", {"x": 99})
    assert comp.x == 99


# ---------------------------------------------------------------------------
# Property descriptors
# ---------------------------------------------------------------------------

def test_property_default() -> None:
    @Noid.component({"id": "ex:prop-default", "properties": {"name": {"default": "World"}}})
    class NameOid(OidComponent):
        pass

    comp = NameOid(bus=fresh_bus())
    # Default is set in _initialize; access before start returns Python default
    assert comp.name == "World"


def test_property_setter_getter() -> None:
    @Noid.component({"id": "ex:prop-rw", "properties": {"count": {"default": 0}}})
    class CountOid(OidComponent):
        pass

    comp = CountOid(bus=fresh_bus())
    comp.count = 5
    assert comp.count == 5


def test_property_readonly() -> None:
    @Noid.component({"id": "ex:prop-ro", "properties": {"tag": {"default": "v1", "readonly": True}}})
    class TagOid(OidComponent):
        pass

    comp = TagOid(bus=fresh_bus())
    assert comp.tag == "v1"
    with pytest.raises(AttributeError):
        comp.tag = "v2"


def test_properties_are_per_instance() -> None:
    @Noid.component({"id": "ex:prop-iso", "properties": {"val": {"default": 0}}})
    class IsoOid(OidComponent):
        pass

    a = IsoOid(bus=fresh_bus())
    b = IsoOid(bus=fresh_bus())
    a.val = 10
    assert b.val == 0


# ---------------------------------------------------------------------------
# Lifecycle: start / stop, handler registration
# ---------------------------------------------------------------------------

async def test_start_applies_spec_defaults() -> None:
    @Noid.component({"id": "ex:defaults-start", "properties": {"msg": {"default": "hello"}}})
    class DefOid(OidComponent):
        pass

    comp = DefOid(bus=fresh_bus())
    await comp.start()
    assert comp.msg == "hello"


async def test_start_does_not_override_ctor_property() -> None:
    @Noid.component({"id": "ex:ctor-wins", "properties": {"val": {"default": 1}}})
    class CtorOid(OidComponent):
        pass

    comp = CtorOid(bus=fresh_bus(), properties={"val": 99})
    await comp.start()
    assert comp.val == 99


async def test_stop_unsubscribes() -> None:
    bus = fresh_bus()
    log: list = []

    @Noid.component({"id": "ex:unsub-test", "subscribe": "ping~pong", "receive": ["pong"]})
    class UnsubOid(OidComponent):
        async def handle_pong(self, notice, message):
            log.append(notice)

    comp = UnsubOid(bus=bus)
    await comp.start()
    await bus.publish("ping", {})
    assert log == ["pong"]

    await comp.stop()
    await bus.publish("ping", {})
    assert len(log) == 1  # no new calls after stop


# ---------------------------------------------------------------------------
# subscribe/publish spec string wiring
# ---------------------------------------------------------------------------

async def test_subscribe_notice_mapping() -> None:
    """'topic~notice' in spec.subscribe dispatches notice to handle_notice."""
    bus = fresh_bus()
    received: list = []

    @Noid.component({"id": "ex:sub-notice", "subscribe": "sensor/temp~update", "receive": ["update"]})
    class SubNoticeOid(OidComponent):
        async def handle_update(self, notice, message):
            received.append((notice, message["v"]))

    comp = SubNoticeOid(bus=bus)
    await comp.start()
    await bus.publish("sensor/temp", {"v": 42})
    assert received == [("update", 42)]


async def test_subscribe_no_notice_passthrough() -> None:
    """'topic' (no ~) dispatches the topic itself as the notice name."""
    bus = fresh_bus()
    received: list = []

    @Noid.component({"id": "ex:sub-direct", "subscribe": "ping", "receive": ["ping"]})
    class DirectOid(OidComponent):
        def handle_ping(self, notice, message):
            received.append(notice)

    comp = DirectOid(bus=bus)
    await comp.start()
    await bus.publish("ping", {})
    assert received == ["ping"]


async def test_subscribe_wildcard() -> None:
    bus = fresh_bus()
    received: list = []

    @Noid.component({"id": "ex:sub-wildcard", "subscribe": "news/#~article", "receive": ["article"]})
    class WildOid(OidComponent):
        def handle_article(self, notice, message):
            received.append(message["title"])

    comp = WildOid(bus=bus)
    await comp.start()
    await bus.publish("news/health", {"title": "cure"})
    await bus.publish("news/tech", {"title": "ai"})
    await bus.publish("weather/sun", {"title": "ignored"})
    assert received == ["cure", "ai"]


async def test_subscribe_dict_form() -> None:
    bus = fresh_bus()
    received: list = []

    @Noid.component({"id": "ex:sub-dict", "subscribe": {"ping": "pong"}, "receive": ["pong"]})
    class DictSubOid(OidComponent):
        def handle_pong(self, notice, message):
            received.append(notice)

    comp = DictSubOid(bus=bus)
    await comp.start()
    await bus.publish("ping", {})
    assert received == ["pong"]


async def test_notify_publishes_mapped_topic() -> None:
    bus = fresh_bus()
    published: list = []

    @Noid.component({"id": "ex:notify-test", "publish": "result~output/value"})
    class NotifyOid(OidComponent):
        async def trigger(self):
            await self._notify("result", {"v": 7})

    bus.subscribe("output/value", lambda t, m: published.append(m))
    comp = NotifyOid(bus=bus)
    await comp.start()
    await comp.trigger()
    assert published == [{"v": 7}]


# ---------------------------------------------------------------------------
# receive spec → handler dispatch
# ---------------------------------------------------------------------------

async def test_receive_array_form() -> None:
    bus = fresh_bus()
    log: list = []

    @Noid.component({"id": "ex:recv-array", "receive": ["ping", "pong"]})
    class RecvOid(OidComponent):
        def handle_ping(self, notice, message):
            log.append("ping")
        def handle_pong(self, notice, message):
            log.append("pong")

    comp = RecvOid(bus=bus, subscribe="ping;pong")
    await comp.start()
    await bus.publish("ping", {})
    await bus.publish("pong", {})
    assert log == ["ping", "pong"]


async def test_receive_dict_form_custom_handler() -> None:
    bus = fresh_bus()
    log: list = []

    @Noid.component({
        "id": "ex:recv-dict",
        "receive": {"go": "my_go_handler"},
        "subscribe": "go",
    })
    class CustomOid(OidComponent):
        def my_go_handler(self, notice, message):
            log.append("custom")

    comp = CustomOid(bus=bus)
    await comp.start()
    await bus.publish("go", {})
    assert log == ["custom"]


async def test_receive_camel_case_notice() -> None:
    """'updateValue' in receive spec maps to handle_update_value method."""
    bus = fresh_bus()
    log: list = []

    @Noid.component({
        "id": "ex:camel-recv",
        "receive": ["updateValue"],
        "subscribe": "update~updateValue",
    })
    class CamelOid(OidComponent):
        def handle_update_value(self, notice, message):
            log.append(message.get("v"))

    comp = CamelOid(bus=bus)
    await comp.start()
    await bus.publish("update", {"v": 3})
    assert log == [3]


# ---------------------------------------------------------------------------
# constructor-level subscribe / publish overrides
# ---------------------------------------------------------------------------

async def test_instance_subscribe_override() -> None:
    """subscribe kwarg on OidComponent() wires extra topics at construction time."""
    bus = fresh_bus()
    log: list = []

    @Noid.component({"id": "ex:inst-sub", "receive": ["ping"]})
    class InstSubOid(OidComponent):
        def handle_ping(self, notice, message):
            log.append("ping")

    comp = InstSubOid(bus=bus, subscribe="ping")
    await comp.start()
    await bus.publish("ping", {})
    assert log == ["ping"]


# ---------------------------------------------------------------------------
# provide / connect / invoke via components
# ---------------------------------------------------------------------------

async def test_provide_and_invoke() -> None:
    # response: True at the interface level → _invoke collects all providers' answers
    Noid.c_interface({
        "id": "itf:math",
        "response": True,
        "operations": {"add": {}},
    })
    bus = fresh_bus()

    @Noid.component({
        "id": "ex:adder",
        "provide": ["itf:math"],
    })
    class AdderOid(OidComponent):
        def handle_add(self, notice, message):
            return message["a"] + message["b"]

    @Noid.component({"id": "ex:caller"})
    class CallerOid(OidComponent):
        async def do_add(self, a, b):
            return await self._invoke("itf:math", "add", {"a": a, "b": b})

    adder = AdderOid(bus=bus, component_id="adder1")
    caller = CallerOid(bus=bus)
    caller._connect("itf:math", "adder1", caller)

    await adder.start()
    await caller.start()

    result = await caller.do_add(3, 4)
    assert result == [7]  # multi-response: list of answers from all connected providers


async def test_connection_ready_recorded() -> None:
    bus = fresh_bus()

    @Noid.component({"id": "ex:provider-cr"})
    class ProviderOid(OidComponent):
        pass

    @Noid.component({"id": "ex:requester-cr"})
    class RequesterOid(OidComponent):
        pass

    provider = ProviderOid(bus=bus, component_id="prov1")
    requester = RequesterOid(bus=bus)
    await provider.start()
    await requester.start()

    requester._connect("itf:data", "prov1", requester)
    bus.provide("itf:data", "prov1", provider)

    assert "itf:data" in requester._connected
    assert "prov1" in requester._connected["itf:data"]


# ---------------------------------------------------------------------------
# connect spec-string wiring
# ---------------------------------------------------------------------------

async def test_connect_spec_string() -> None:
    bus = fresh_bus()
    connected: list = []

    @Noid.component({"id": "ex:conn-spec-prov"})
    class ProvSpec(OidComponent):
        pass

    @Noid.component({"id": "ex:conn-spec-req"})
    class ReqSpec(OidComponent):
        def connection_ready(self, c_interface, component_id, provider):
            super().connection_ready(c_interface, component_id, provider)
            connected.append((c_interface, component_id))

    prov = ProvSpec(bus=bus, component_id="srv1")
    bus.provide("itf:svc", "srv1", prov)

    req = ReqSpec(bus=bus, connect="itf:svc#srv1")
    await req.start()

    assert ("itf:svc", "srv1") in connected


# ---------------------------------------------------------------------------
# handle_get / handle_set built-in handlers
# ---------------------------------------------------------------------------

async def test_handle_get_set() -> None:
    @Noid.component({
        "id": "ex:get-set",
        "properties": {"color": {"default": "red"}},
    })
    class GSOid(OidComponent):
        pass

    comp = GSOid(bus=fresh_bus())
    await comp.start()

    comp.handle_set("set", {"property": "color", "value": "blue"})
    result = comp.handle_get("get", {"property": "color"})
    assert result == "blue"


# ---------------------------------------------------------------------------
# Threading: start_in_thread / stop_thread / join_thread
# ---------------------------------------------------------------------------

def test_start_in_thread_component_receives_messages() -> None:
    """A threaded component must receive bus messages published from another thread."""
    bus = fresh_bus()
    received: list = []
    done = threading.Event()

    @Noid.component({"id": "ex:threaded-recv", "subscribe": "ping~got", "receive": ["got"]})
    class ThreadedOid(OidComponent):
        def handle_got(self, notice, message):
            received.append(message.get("v"))
            done.set()

    comp = ThreadedOid(bus=bus)
    comp.start_in_thread()

    # publish from main thread (a different loop)
    asyncio.run(bus.publish("ping", {"v": 99}))

    assert done.wait(timeout=2), "handler never called"
    assert received == [99]

    comp.stop_thread()
    comp.join_thread(timeout=2)


def test_threaded_component_stop_and_join() -> None:
    bus = fresh_bus()

    @Noid.component({"id": "ex:threaded-lifecycle"})
    class LifeOid(OidComponent):
        pass

    comp = LifeOid(bus=bus)
    comp.start_in_thread()
    assert comp._thread.is_alive()

    comp.stop_thread()
    comp.join_thread(timeout=2)
    assert not comp._thread.is_alive()


def test_two_threaded_components_exchange_messages() -> None:
    """Two components in separate threads communicate through the shared bus."""
    bus = fresh_bus()
    result: list = []
    done = threading.Event()

    @Noid.component({"id": "ex:thr-producer", "publish": "tick~counter/tick"})
    class Producer(OidComponent):
        async def emit(self):
            await self._notify("tick", {"n": 1})

    @Noid.component({"id": "ex:thr-consumer", "subscribe": "counter/tick~on_tick", "receive": ["on_tick"]})
    class Consumer(OidComponent):
        def handle_on_tick(self, notice, message):
            result.append(message["n"])
            done.set()

    producer = Producer(bus=bus)
    consumer = Consumer(bus=bus)

    producer.start_in_thread()
    consumer.start_in_thread()

    # Trigger a publish on the producer's loop from the main thread
    asyncio.run_coroutine_threadsafe(
        producer.emit(), producer._loop
    ).result(timeout=2)

    assert done.wait(timeout=2), "consumer never received message"
    assert result == [1]

    producer.stop_thread()
    consumer.stop_thread()
    producer.join_thread(timeout=2)
    consumer.join_thread(timeout=2)


# ---------------------------------------------------------------------------
# Readiness queue: set_ready / _pending_messages
# ---------------------------------------------------------------------------

async def test_not_ready_queues_messages() -> None:
    """Messages received while not ready must be buffered, not dispatched."""
    bus = fresh_bus()
    log: list = []

    @Noid.component({"id": "ex:queue-buffer", "subscribe": "work~do", "receive": ["do"]})
    class BufferOid(OidComponent):
        def handle_do(self, notice, message):
            log.append(message["n"])

    comp = BufferOid(bus=bus)
    await comp.start()
    comp.set_ready(False)

    await bus.publish("work", {"n": 1})
    await bus.publish("work", {"n": 2})

    assert log == [], "handler must not fire while not ready"
    assert len(comp._pending_messages) == 2


async def test_set_ready_drains_queue_in_order() -> None:
    """set_ready(True) must replay buffered messages in FIFO order."""
    bus = fresh_bus()
    log: list = []

    @Noid.component({"id": "ex:queue-drain", "subscribe": "job~run", "receive": ["run"]})
    class DrainOid(OidComponent):
        def handle_run(self, notice, message):
            log.append(message["n"])

    comp = DrainOid(bus=bus)
    await comp.start()
    comp.set_ready(False)

    await bus.publish("job", {"n": 10})
    await bus.publish("job", {"n": 20})
    await bus.publish("job", {"n": 30})

    comp.set_ready(True)
    # yield to the event loop so the drain task can execute
    await asyncio.sleep(0)

    assert log == [10, 20, 30]
    assert comp._pending_messages == []


async def test_set_ready_without_pending_is_noop() -> None:
    """set_ready(True) on an empty queue must not raise or schedule extra tasks."""
    bus = fresh_bus()

    @Noid.component({"id": "ex:queue-noop"})
    class NoopOid(OidComponent):
        pass

    comp = NoopOid(bus=bus)
    await comp.start()
    comp.set_ready(False)
    comp.set_ready(True)  # must not raise


async def test_messages_after_set_ready_dispatched_directly() -> None:
    """After set_ready(True), new messages are dispatched without queuing."""
    bus = fresh_bus()
    log: list = []

    @Noid.component({"id": "ex:queue-direct", "subscribe": "ping~got", "receive": ["got"]})
    class DirectOid(OidComponent):
        def handle_got(self, notice, message):
            log.append(message.get("v"))

    comp = DirectOid(bus=bus)
    await comp.start()
    comp.set_ready(False)
    comp.set_ready(True)
    await asyncio.sleep(0)

    await bus.publish("ping", {"v": 99})
    assert log == [99]
    assert comp._pending_messages == []


async def test_async_handler_with_readiness_queue() -> None:
    """Async handlers in the drained queue must be awaited properly."""
    bus = fresh_bus()
    log: list = []

    @Noid.component({"id": "ex:queue-async", "subscribe": "work~do", "receive": ["do"]})
    class AsyncOid(OidComponent):
        async def handle_do(self, notice, message):
            await asyncio.sleep(0)
            log.append(message["n"])

    comp = AsyncOid(bus=bus)
    await comp.start()
    comp.set_ready(False)

    await bus.publish("work", {"n": 7})
    await bus.publish("work", {"n": 8})

    comp.set_ready(True)
    await asyncio.sleep(0.05)  # allow async drain tasks to complete

    assert log == [7, 8]


async def test_readiness_prevents_concurrent_async_handler() -> None:
    """
    Pattern: set_ready(False) inside an async handler gates subsequent messages
    until processing completes — prevents concurrent invocations.
    """
    bus = fresh_bus()
    log: list = []
    active = 0

    @Noid.component({"id": "ex:queue-mutex", "subscribe": "task~run", "receive": ["run"]})
    class MutexOid(OidComponent):
        async def handle_run(self, notice, message):
            nonlocal active
            self.set_ready(False)
            active += 1
            assert active == 1, "concurrent execution detected"
            await asyncio.sleep(0.01)
            log.append(message["n"])
            active -= 1
            self.set_ready(True)
            await asyncio.sleep(0)  # let drain run before returning

    comp = MutexOid(bus=bus)
    await comp.start()

    await asyncio.gather(
        bus.publish("task", {"n": 1}),
        bus.publish("task", {"n": 2}),
        bus.publish("task", {"n": 3}),
    )
    await asyncio.sleep(0.1)

    assert sorted(log) == [1, 2, 3]
