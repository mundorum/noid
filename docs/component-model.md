# noid Component Model

This is the reference document for the noid component model. It describes how to define, register, instantiate, and wire components in Python. See [architecture.md](architecture.md) for the broader design context.

---

## Overview: notice → publish → subscribe → notice

Components communicate exclusively through the bus. The data-flow pattern is:

```
Component A                   Bus                    Component B
    │                          │                          │
    ├── _notify("change", m) ──┤                          │
    │   (maps via publish spec)│                          │
    │                          ├── publish("sensor/val") ─┤
    │                          │   (matches subscribe)    │
    │                          │                          ├── _convert_notice
    │                          │                          │   (maps topic → notice)
    │                          │                          ├── handle_notice("update", m)
    │                          │                          ├── handle_update(notice, m)
```

The same pattern is used in the JS oid library. Python naming uses `snake_case` throughout.

---

## Defining a component

### Decorator form (custom class)

The primary way to create a component. The `@Noid.component(spec)` decorator:
- Attaches `_spec` to the class
- Adds property descriptors for each entry in `spec["properties"]`
- Registers the class in `Noid._oid_reg` under `spec["id"]`

```python
from noid.core.component import Noid, OidComponent

@Noid.component({
    "id": "ex:greeter",
    "properties": {
        "name": {"default": "World"},
    },
    "receive": ["greet"],
    "subscribe": "hello/+~greet",
    "publish": "done~greeter/done",
})
class GreeterOid(OidComponent):
    async def handle_greet(self, notice: str, message: dict) -> None:
        print(f"Hello, {self.name}!")
        await self._notify("done", {"greeted": self.name})
```

### JSON / declarative form (no custom class)

When no custom Python behaviour is needed, use `Noid.register(spec)`. A generic `OidComponent` subclass is created automatically. The spec's `subscribe`, `publish`, `receive`, and `connect` wiring still applies.

```python
Noid.register({
    "id": "ex:logger",
    "subscribe": "sensor/#~log",
    "receive": ["log"],
})
```

This mirrors the declarative approach of HTML `<oid>` elements in JS.

### Non-decorator programmatic form

Pass `"implementation"` inside the spec to register without using the decorator syntax:

```python
class GreeterOid(OidComponent):
    async def handle_greet(self, notice, message):
        ...

Noid.component({"id": "ex:greeter", "receive": ["greet"], "implementation": GreeterOid})
```

---

## Spec fields

### `id` (required)

Unique identifier for the component type. Convention: `"namespace:name"` (e.g. `"ex:greeter"`, `"sensor:pressure"`).

### `properties`

Dict of named properties with optional sub-fields:
- `default` — value applied in `_initialize()` unless already set by the constructor
- `readonly` — if `True`, only a getter is generated; assignment raises `AttributeError`

```python
"properties": {
    "value":  {"default": 0},
    "unit":   {"default": "°C", "readonly": True},
    "label":  {},                    # no default, read-write
}
```

Properties are Python `property` descriptors backed by `_prop_<name>` instance attributes. Multiple instances of the same class are independent (defaults are applied per instance).

### `receive`

Declares which notice names this component handles. **Required** for `handle_notice` to dispatch to `handle_*` methods — if a notice name is not listed here, `handle_notice` will silently ignore it.

Two forms:
- **Array:** `["greet", "update"]` — auto-maps to `handle_greet`, `handle_update`
- **Dict:** `{"greet": "my_method"}` — maps to `self.my_method`

Handler naming: `"camelCase"` notices are converted to `snake_case`, so `"updateValue"` maps to `handle_update_value`. If a method matching the generated name does not exist on the class, the notice is silently dropped.

```python
"receive": ["greet", "update"],          # array form
"receive": {"greet": "on_greet"},        # dict form, custom method name
"receive": {"greet": {"handler": "on_greet"}},  # dict form, explicit key
```

### `subscribe`

Wires bus topics to internal notice names. String syntax: `"topic~notice"`, multiple separated by `;`. Dict syntax: `{"topic": "notice"}`.

```python
"subscribe": "sensor/temp~update",                   # single
"subscribe": "sensor/temp~update;sensor/hum~update", # multiple
"subscribe": {"sensor/temp": "update"},              # dict form
```

Wildcards (`+`, `#`) are supported:
```python
"subscribe": "news/#~article",   # multilevel wildcard
"subscribe": "+/alarm~alert",    # single-level wildcard
```

If no `~` separator is present, the topic itself is used as the notice name:
```python
"subscribe": "ping",   # topic 'ping' dispatches notice 'ping' → handle_ping
```

**`receive` is still required** to route the resulting notice to a handler.

### `publish`

Maps outgoing notice names to bus topics. Called via `await self._notify(notice, message)`.

```python
"publish": "done~greeter/done",                    # single
"publish": "done~greeter/done;error~greeter/error", # multiple
"publish": {"done": "greeter/done"},               # dict form
```

### `provide`

List of interface ids that this component exposes. The component must be given a `component_id` for the bus to identify it.

```python
"provide": ["itf:transfer"],
```

Requires a matching `Noid.c_interface` registration. Operations on the interface map to `handle_<operation>` methods exactly as `receive` does.

### `connect`

Requests a connection to one or more providers. String syntax: `"itf:name#component_id"`, multiple separated by `;`.

```python
"connect": "itf:transfer#display1",
"connect": "itf:data#store1;itf:log#logger1",
```

When the provider is registered, `connection_ready(c_interface, component_id, provider)` is called on the connecting component.

### `implementation`

Used only in the non-decorator programmatic form to pass the class directly:

```python
Noid.component({"id": "ex:greeter", "implementation": GreeterOid})
```

---

## Instantiation

### `Noid.create(id, properties, *, bus, ...)`

Mirrors JS `Oid.create(id, properties)`. Creates an instance of a registered component type.

```python
comp = Noid.create("ex:greeter", {"name": "Alice"}, bus=bus)
await comp.start()
```

Optional keyword arguments:
- `bus` — which bus instance to use (defaults to `Bus.i`)
- `component_instance_id` — sets `component_id` used by `provide`/`connect`
- `subscribe`, `publish`, `connect` — instance-level wiring overrides (applied on top of spec)

### Direct constructor

Equivalent to `Noid.create` but more explicit:

```python
comp = GreeterOid(
    bus=bus,
    component_id="greeter1",
    properties={"name": "Alice"},
    subscribe="extra/topic~greet",
)
await comp.start()
```

---

## Lifecycle

### `await comp.start()`

Shared-loop mode. Runs `_initialize()` inside the caller's running event loop:
1. Builds `_receive_handler` and `_provide_handler` from spec
2. Applies property defaults (skipped if already set at construction time)
3. Wires up bus subscriptions (`subscribe` spec → `_convert_notice` / `handle_notice`)
4. Registers provided interfaces on the bus (`provide` spec → `bus.provide`)
5. Connects to requested providers (`connect` spec → `bus.connect`)
6. Applies instance-level overrides (constructor `subscribe`/`publish`/`connect` args)

### `await comp.stop()`

Unsubscribes all topics and withdraws all provided interfaces. After `stop()`, the component is silent on the bus.

### `comp.start_in_thread()`

Dedicated-thread mode. See [Threading model](#threading-model) below.

---

## Writing handlers

### Receive handlers

```python
@Noid.component({"id": "ex:sensor", "receive": ["update"], "subscribe": "sensor/raw~update"})
class SensorOid(OidComponent):
    async def handle_update(self, notice: str, message: dict) -> None:
        # notice — the notice name (e.g. "update")
        # message — the bus message payload
        self.value = message["raw"]
        await self._notify("reading", {"value": self.value})
```

Handlers may be sync or `async def`. Async handlers are awaited by the bus in the correct event loop.

### Sub-notice routing

The first `/`-separated segment of the notice is used as the dispatch key:

```
notice "update/partial" → dispatches to _receive_handler["update"] → handle_update
```

### Triggering outgoing events

```python
await self._notify("done", {"result": 42})
```

`_notify` looks up the notice name in `_map_notice_topic` (built from the `publish` spec). If a mapping exists, `_publish(topic, message)` is called. No error is raised if no mapping exists.

### Invoke handlers (provided interfaces)

```python
@Noid.component({"id": "ex:adder", "provide": ["itf:math"]})
class AdderOid(OidComponent):
    def handle_add(self, notice: str, message: dict) -> int:
        return message["a"] + message["b"]
```

`handle_invoke` dispatches to `_provide_handler["{c_interface}.{operation}"]`, which is auto-named using the same `handle_<operation>` convention. Sync and async return values are both supported.

### Default built-in handlers

`OidBase` provides `handle_get` and `handle_set` for generic property access over the bus:

```python
# handle_get: reads self.<message["property"]>
# handle_set: sets self.<message["property"]> = message["value"]
```

---

## Interfaces

Register an interface before any component uses it:

```python
Noid.c_interface({
    "id": "itf:transfer",
    "operations": {
        "send": {},          # no response expected
        "query": {},
    },
})
```

Top-level `"response": True` on the interface spec causes `_invoke` to collect responses from **all** connected providers and return them as a list. Without it, only the first provider's response is returned.

```python
Noid.c_interface({
    "id": "itf:sensor",
    "response": True,       # query ALL connected sensors, return list
    "operations": {"read": {}},
})
```

---

## Threading model

### Shared-loop (default)

All components share one asyncio event loop in one thread. Use this unless a component has a reason to be isolated.

```python
bus = Bus()
sensor = SensorOid(bus=bus)
display = DisplayOid(bus=bus)

await sensor.start()
await display.start()
# All handlers run in the same event loop
```

### Dedicated thread (opt-in)

```python
sensor = SensorOid(bus=bus)
sensor.start_in_thread()   # blocks until subscriptions are live
...
sensor.stop_thread()       # signal stop
sensor.join_thread(timeout=5)
```

`start_in_thread()` guarantees that all subscriptions and providers are registered before it returns (unlike a bare `Thread.start()` which would require a separate synchronization primitive).

### Cross-thread message delivery

When a component runs in a dedicated thread, every bus handler it registers is wrapped by `_make_thread_dispatcher`. The wrapper:
- **Same loop:** creates a task for async handlers; calls sync handlers directly
- **Different thread:** uses `loop.call_soon_threadsafe` to schedule the handler call on the component's own loop; creates a task there for any async handlers

The bus itself is unaware of threading. Thread-safety in the bus comes from its `threading.Lock` protecting the listener tables.

---

## Complete example

```python
import asyncio
from noid.core.bus import Bus
from noid.core.component import Noid, OidComponent

bus = Bus()

Noid.c_interface({
    "id": "itf:data",
    "operations": {"send": {}},
})

@Noid.component({
    "id": "ex:producer",
    "publish": "ready~data/out",
    "provide": ["itf:data"],
})
class ProducerOid(OidComponent):
    async def handle_send(self, notice, message):
        await self._notify("ready", {"value": message.get("input", 0) * 2})


@Noid.component({
    "id": "ex:consumer",
    "subscribe": "data/out~on_data",
    "receive": ["on_data"],
})
class ConsumerOid(OidComponent):
    def handle_on_data(self, notice, message):
        print(f"Received: {message['value']}")


async def main():
    producer = ProducerOid(bus=bus, component_id="prod1")
    consumer = ConsumerOid(bus=bus)
    consumer._connect("itf:data", "prod1", consumer)

    await producer.start()
    await consumer.start()

    await bus.invoke("itf:data", "prod1", "send", {"input": 21})

    await producer.stop()
    await consumer.stop()

asyncio.run(main())
```
