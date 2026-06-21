import asyncio
import threading

import pytest

from noid.core.bus import Bus


@pytest.fixture
def bus() -> Bus:
    return Bus()


# ============================================================
# subscribe / publish — exact topics
# ============================================================

async def test_exact_topic(bus: Bus) -> None:
    received: list = []
    bus.subscribe("test/topic", lambda t, m: received.append((t, m)))
    await bus.publish("test/topic", {"value": 42})
    assert received == [("test/topic", {"value": 42})]


async def test_no_match(bus: Bus) -> None:
    received: list = []
    bus.subscribe("test/topic", lambda t, m: received.append(t))
    await bus.publish("other/topic", {})
    assert received == []


async def test_multiple_handlers_same_topic(bus: Bus) -> None:
    log: list = []
    bus.subscribe("t", lambda t, m: log.append("a"))
    bus.subscribe("t", lambda t, m: log.append("b"))
    await bus.publish("t", {})
    assert log == ["a", "b"]


# ============================================================
# wildcards
# ============================================================

async def test_wildcard_multilevel_hash(bus: Bus) -> None:
    received: list = []
    bus.subscribe("news/#", lambda t, m: received.append(t))
    await bus.publish("news/disease", {"value": "dengue"})
    await bus.publish("news/drug", {"value": "vaccine"})
    await bus.publish("news/dinosaur/brazil", {"value": "dino"})
    await bus.publish("report/dinosaur", {"value": "survey"})  # should NOT match
    assert received == ["news/disease", "news/drug", "news/dinosaur/brazil"]


async def test_wildcard_single_level_plus(bus: Bus) -> None:
    received: list = []
    bus.subscribe("+/dinosaur", lambda t, m: received.append(t))
    await bus.publish("news/dinosaur", {})        # match
    await bus.publish("report/dinosaur", {})      # match
    await bus.publish("news/disease", {})         # no match
    await bus.publish("news/dinosaur/brazil", {}) # no match (two extra levels)
    assert received == ["news/dinosaur", "report/dinosaur"]


async def test_wildcard_hash_alone(bus: Bus) -> None:
    received: list = []
    bus.subscribe("#", lambda t, m: received.append(t))
    await bus.publish("anything", {})
    await bus.publish("a/b/c", {})
    assert received == ["anything", "a/b/c"]


async def test_exact_not_caught_by_wrong_wildcard(bus: Bus) -> None:
    received: list = []
    bus.subscribe("news/disease", lambda t, m: received.append(t))
    await bus.publish("news/drug", {})
    assert received == []


# ============================================================
# dict-style subscribe
# ============================================================

async def test_dict_subscribe(bus: Bus) -> None:
    log: list = []
    bus.subscribe(
        {
            "topic/a": lambda t, m: log.append("a"),
            "topic/b": lambda t, m: log.append("b"),
        }
    )
    await bus.publish("topic/a", {})
    await bus.publish("topic/b", {})
    assert log == ["a", "b"]


# ============================================================
# unsubscribe
# ============================================================

async def test_unsubscribe_exact(bus: Bus) -> None:
    log: list = []

    def handler(t, m):
        log.append(t)

    bus.subscribe("t", handler)
    await bus.publish("t", {})
    bus.unsubscribe("t", handler)
    await bus.publish("t", {})
    assert len(log) == 1


async def test_unsubscribe_wildcard(bus: Bus) -> None:
    log: list = []

    def handler(t, m):
        log.append(t)

    bus.subscribe("news/#", handler)
    await bus.publish("news/a", {})
    bus.unsubscribe("news/#", handler)
    await bus.publish("news/b", {})
    assert log == ["news/a"]


async def test_unsubscribe_only_removes_target_handler(bus: Bus) -> None:
    log: list = []
    h1 = lambda t, m: log.append("h1")
    h2 = lambda t, m: log.append("h2")
    bus.subscribe("t", h1)
    bus.subscribe("t", h2)
    bus.unsubscribe("t", h1)
    await bus.publish("t", {})
    assert log == ["h2"]


# ============================================================
# async handlers
# ============================================================

async def test_async_handler(bus: Bus) -> None:
    received: list = []

    async def handler(t, m):
        await asyncio.sleep(0)
        received.append(t)

    bus.subscribe("test/topic", handler)
    await bus.publish("test/topic", {"v": 1})
    assert received == ["test/topic"]


async def test_async_handler_wildcard(bus: Bus) -> None:
    received: list = []

    async def handler(t, m):
        await asyncio.sleep(0)
        received.append(t)

    bus.subscribe("sensor/#", handler)
    await bus.publish("sensor/temp", {})
    await bus.publish("sensor/humidity", {})
    assert received == ["sensor/temp", "sensor/humidity"]


# ============================================================
# provide / connect / withhold / invoke
# ============================================================

class _Callback:
    def __init__(self):
        self.calls: list = []

    def connection_ready(self, c_interface, component_id, provider):
        self.calls.append((c_interface, component_id, provider))


class _Provider:
    def __init__(self, return_value=None):
        self.calls: list = []
        self._rv = return_value

    def handle_invoke(self, c_interface, notice, message):
        self.calls.append((c_interface, notice, message))
        return self._rv


async def test_provide_then_connect(bus: Bus) -> None:
    provider = _Provider()
    callback = _Callback()
    bus.provide("itf:data", "comp1", provider)
    bus.connect("itf:data", "comp1", callback)
    assert callback.calls == [("itf:data", "comp1", provider)]


async def test_connect_then_provide(bus: Bus) -> None:
    provider = _Provider()
    callback = _Callback()
    bus.connect("itf:data", "comp1", callback)
    assert callback.calls == []
    bus.provide("itf:data", "comp1", provider)
    assert callback.calls == [("itf:data", "comp1", provider)]


async def test_provide_duplicate_returns_false(bus: Bus) -> None:
    provider = _Provider()
    assert bus.provide("itf:data", "comp1", provider) is True
    assert bus.provide("itf:data", "comp1", provider) is False


async def test_withhold_removes_provider(bus: Bus) -> None:
    provider = _Provider()
    bus.provide("itf:data", "comp1", provider)
    assert bus.withhold("itf:data", "comp1") is True
    assert bus.withhold("itf:data", "comp1") is False


async def test_invoke(bus: Bus) -> None:
    provider = _Provider(return_value=42)
    bus.provide("itf:calc", "calc1", provider)
    result = await bus.invoke("itf:calc", "calc1", "compute", {"x": 10})
    assert result == 42
    assert provider.calls == [("itf:calc", "compute", {"x": 10})]


async def test_invoke_missing_returns_none(bus: Bus) -> None:
    result = await bus.invoke("itf:calc", "missing", "compute", {})
    assert result is None


async def test_invoke_async_provider() -> None:
    class _AsyncProvider:
        async def handle_invoke(self, c_interface, notice, message):
            await asyncio.sleep(0)
            return "async-result"

    b = Bus()
    b.provide("itf:x", "p1", _AsyncProvider())
    result = await b.invoke("itf:x", "p1", "go", {})
    assert result == "async-result"


async def test_multiple_pending_callbacks(bus: Bus) -> None:
    provider = _Provider()
    cb1, cb2 = _Callback(), _Callback()
    bus.connect("itf:data", "comp1", cb1)
    bus.connect("itf:data", "comp1", cb2)
    bus.provide("itf:data", "comp1", provider)
    assert len(cb1.calls) == 1
    assert len(cb2.calls) == 1


# ============================================================
# singleton Bus.i
# ============================================================

def test_bus_singleton_exists() -> None:
    assert isinstance(Bus.i, Bus)


# ============================================================
# thread safety
# ============================================================

async def test_concurrent_subscriptions(bus: Bus) -> None:
    """Multiple threads subscribing simultaneously must not corrupt state."""
    barrier = threading.Barrier(8)

    def add_sub(n):
        barrier.wait()
        bus.subscribe(f"thr/{n}", lambda t, m: None)

    threads = [threading.Thread(target=add_sub, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(bus._listeners) == 8


async def test_subscribe_from_thread_publish_from_loop(bus: Bus) -> None:
    """Handler subscribed from another thread must receive messages."""
    received: list = []
    lock = threading.Lock()

    def subscribe_worker():
        bus.subscribe("thread/topic", lambda t, m: (lock.acquire(), received.append(t), lock.release()))

    t = threading.Thread(target=subscribe_worker)
    t.start()
    t.join()

    await bus.publish("thread/topic", {})
    assert received == ["thread/topic"]


async def test_concurrent_wildcard_subscriptions(bus: Bus) -> None:
    """Concurrent wildcard subscriptions must all be registered."""
    barrier = threading.Barrier(6)

    def add_wildcard(n):
        barrier.wait()
        bus.subscribe(f"thr/{n}/#", lambda t, m: None)

    threads = [threading.Thread(target=add_wildcard, args=(i,)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(bus._listeners_rgx) == 6
