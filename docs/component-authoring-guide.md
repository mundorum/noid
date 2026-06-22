# noid Component Authoring Guide

This document is written for Claude (or any AI assistant) working in a project that
implements noid components. Read it in full before writing any component code.

---

## 1. Mental model

noid components are **event-driven objects** that communicate through a shared **Bus**.
The Bus is the only channel: components never call each other directly.

```
Component A                      Bus                      Component B
    │                             │                            │
    ├─ await self._notify(n, m) ──┤                            │
    │   (maps notice → topic)     ├── publish(topic, m) ──────▶│
    │                             │   (matches subscribe)      ├─ _convert_notice
    │                             │                            ├─ handle_notice(n, m)
    │                             │                            └─ handle_<n>(notice, m)
```

Two communication styles:
- **Publish/subscribe** — fire-and-forget broadcast; any number of receivers
- **Connect/invoke** — targeted call to a specific provider; optional return value

---

## 2. Mandatory imports

```python
from noid.core.bus import Bus
from noid.core.component import Noid, OidComponent
```

Never import from `noid.core.base` directly — use `OidComponent` from `component.py`.

---

## 3. Defining a component

### 3a. Decorator form (recommended)

```python
@Noid.component({
    "id": "mypkg:sensor",           # REQUIRED — unique across all registrations
    "properties": {
        "unit": {"default": "°C"},  # optional default; optional "readonly": True
        "value": {},                 # no default, read-write
    },
    "receive":   ["update", "reset"],   # notices this component handles
    "subscribe": "raw/temp~update",     # bus topic → internal notice
    "publish":   "reading~sensor/out",  # internal notice → bus topic
    "provide":   ["itf:sensor"],        # interfaces this component exposes
    "connect":   "itf:store#store1",    # interfaces this component consumes
})
class SensorOid(OidComponent):
    ...
```

### 3b. JSON-only form (no custom logic)

```python
Noid.register({
    "id": "mypkg:relay",
    "subscribe": "in/raw~forward",
    "publish":   "forward~out/processed",
    # no 'receive' needed — auto-relay fires when no handler exists
})
```

### 3c. Non-decorator programmatic form

```python
class SensorOid(OidComponent): ...

Noid.component({"id": "mypkg:sensor", "implementation": SensorOid})
```

---

## 4. Spec field reference

| Field | Type | Description |
|---|---|---|
| `id` | str | **Required.** `"namespace:name"` convention. Must be unique. |
| `properties` | dict | Named properties. Keys = property names. Values = `{"default": x, "readonly": bool}`. |
| `receive` | list or dict | Declares which notices this component handles (required for handler dispatch). |
| `subscribe` | str or dict | Wires bus topics to internal notices. |
| `publish` | str or dict | Wires internal notices to bus topics. |
| `provide` | list | Interface ids this component provides. Requires `component_id`. |
| `connect` | str | Interface ids and provider ids this component connects to. |

### String syntax

All string spec fields use the **`~` separator**: `"source~destination"`.  
Multiple entries separated by **`;`**.

```python
# subscribe:  bus_topic~notice_name
"subscribe": "sensor/temp~update;sensor/hum~update"

# publish:  notice_name~bus_topic
"publish": "reading~sensor/out;error~sensor/error"

# connect:  interface_id#component_instance_id
"connect": "itf:store#db1;itf:log#logger1"
```

MQTT wildcards are allowed in subscribe topics:
- `+` — exactly one level: `"sensor/+/raw~update"`
- `#` — one or more levels: `"news/#~article"`

---

## 5. Handler naming

### `receive` handlers

The `receive` spec maps notice names to `handle_*` methods.

| Notice name | Method name |
|---|---|
| `"update"` | `handle_update` |
| `"updateValue"` (camelCase) | `handle_update_value` |
| `"my_notice"` | `handle_my_notice` |

**Important:** if `receive` lists a notice but the method does not exist on the class,
that notice is silently dropped. No error is raised.

```python
@Noid.component({"id": "ex:comp", "receive": ["update", "reset"]})
class MyOid(OidComponent):
    def handle_update(self, notice: str, message: dict) -> None: ...
    def handle_reset(self, notice: str, message: dict) -> None: ...
```

Dict form for custom method names:
```python
"receive": {"update": "on_update", "reset": "on_reset"}
```

### `provide` handlers

Operation names in the interface spec are mapped the same way:
`"add"` → `handle_add`, `"computeTotal"` → `handle_compute_total`.

---

## 6. The `receive` requirement

`handle_notice` dispatches **only** to notices declared in `receive`.  
If a notice arrives but is not in `receive`, it is silently ignored.

**Exception — auto-relay:** if a notice has no handler but a `publish` mapping exists
for it, the message is automatically forwarded. This works only for pure-JSON relay
components created with `Noid.register`. Custom classes should always declare `receive`.

---

## 7. Handler signatures

All handlers receive `(notice: str, message: Any)`.

- `notice` — the notice name that triggered the call (may include sub-path: `"update/partial"`)
- `message` — the raw message payload (typically a dict)

Handlers may be sync or `async def`. Async handlers are awaited by the bus:

```python
def handle_update(self, notice, message):        # sync — fine
    self.value = message["value"]

async def handle_fetch(self, notice, message):   # async — also fine
    result = await some_io_call()
    await self._notify("result", {"data": result})
```

---

## 8. Emitting events: `_notify`

```python
await self._notify("reading", {"value": self.value, "unit": self.unit})
```

- `_notify` looks up the notice in `_map_notice_topic` (built from `publish` spec).
- If no mapping exists, the call is a no-op (no error).
- Always `await` it — it is `async`.

---

## 9. Properties

Declared in `spec["properties"]`. Available as attributes on the instance:

```python
@Noid.component({
    "id": "ex:comp",
    "properties": {
        "label":    {"default": "sensor"},
        "readonly": {"default": "v1", "readonly": True},
        "dynamic":  {},   # no default; unset until assigned
    },
})
class MyOid(OidComponent):
    async def start(self):
        await super().start()
        print(self.label)       # "sensor"
        self.label = "new"      # ok
        # self.readonly = "v2"  # raises AttributeError
```

**Construction-time properties win over spec defaults:**
```python
comp = MyOid(bus=bus, properties={"label": "custom"})
await comp.start()
# comp.label == "custom"  (not "sensor")
```

---

## 10. Lifecycle

### Shared-loop (default)

```python
comp = SensorOid(bus=Bus.i, component_id="s1", properties={"unit": "°F"})
await comp.start()   # wires subscriptions, providers, connects
# ... component is active ...
await comp.stop()    # removes all subscriptions and providers
```

### Overriding `start()` / `stop()`

Use this when the component needs a background task or external resource:

```python
class TimerOid(OidComponent):
    async def start(self) -> None:
        await super().start()          # ALWAYS call super first
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        task = getattr(self, "_task", None)
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        await super().stop()           # ALWAYS call super last
```

`super().start()` wires all bus subscriptions and providers.  
`super().stop()` removes all subscriptions and providers.  
Both calls are mandatory; omitting them breaks wiring or causes resource leaks.

### Dedicated thread (opt-in)

```python
comp.start_in_thread()   # blocks until subscriptions are live
# ...
comp.stop_thread()       # signal stop
comp.join_thread(timeout=5)
```

When a component overrides `start()` and runs in a thread, `start_in_thread` calls
`start()` (not `_initialize()`), so the override is executed correctly.

---

## 11. Instantiation

### Via `Noid.create` (preferred)

```python
comp = Noid.create(
    "mypkg:sensor",
    {"unit": "K"},                    # properties (optional)
    bus=bus,
    component_instance_id="sensor1",  # required if using provide/connect
    subscribe="extra/topic~update",   # instance-level subscribe override (optional)
    publish="update~extra/out",       # instance-level publish override (optional)
    connect="itf:store#store1",       # instance-level connect (optional)
)
```

### Via constructor (equivalent, more explicit)

```python
comp = SensorOid(
    bus=bus,
    component_id="sensor1",
    properties={"unit": "K"},
    subscribe="extra/topic~update",
)
```

---

## 12. Interfaces

### Registering an interface

```python
Noid.c_interface({
    "id": "itf:transfer",
    "operations": {
        "send":    {},                 # no return value expected
        "query":   {},
    },
})
```

Add `"response": True` at the interface level to make `_invoke` collect responses
from **all** connected providers (returns a list):

```python
Noid.c_interface({
    "id": "itf:sensor",
    "response": True,
    "operations": {"read": {}},
})
```

Register interfaces **before** any component that uses them is instantiated.
By convention, put interface registration in the same module as the provider component.

### Providing an interface

```python
@Noid.component({
    "id": "ex:store",
    "provide": ["itf:transfer"],
})
class StoreOid(OidComponent):
    def handle_send(self, notice, message):    # "send" → handle_send
        store(message["value"])
        return {"stored": True}               # return value passed back to caller
```

A `component_id` is **required** for providers:
```python
store = StoreOid(bus=bus, component_id="store1")
```

### Consuming an interface

```python
@Noid.component({
    "id": "ex:sender",
    "connect": "itf:transfer#store1",         # in spec — wired at start()
})
class SenderOid(OidComponent):
    async def send(self, value):
        result = await self._invoke("itf:transfer", "send", {"value": value})
        return result
```

Or wire dynamically via constructor:
```python
sender = SenderOid(bus=bus, connect="itf:transfer#store1")
```

`_invoke` returns `None` if no provider is connected.

---

## 13. Scene JSON (NoidPlayer)

The player loads a JSON file and instantiates components declaratively.

```json
{
  "title":  "My Scene",
  "imports": ["./mycomponents.py"],
  "interfaces": [
    {"id": "itf:transfer", "operations": {"send": {}}}
  ],
  "register": [
    {"id": "ex:relay", "subscribe": "a~fwd", "publish": "fwd~b"}
  ],
  "components": [
    {
      "type":       "ex:timer",
      "id":         "t1",
      "properties": {"interval": 1.0, "count": 5},
      "publish":    "tick~timer/out;done~player/done",
      "threaded":   false
    },
    {
      "type":      "ex:logger",
      "subscribe": "timer/out~log"
    }
  ]
}
```

Key rules for scene JSON:
- `type` → registered component id (the `id` field in the spec)
- `id` → instance id (for `provide`/`connect` wiring)
- `publish`/`subscribe`/`connect` are instance-level overrides on top of spec wiring
- `"threaded": true` → `start_in_thread()` called during load (before `run()`)
- Publish `player/done` to tell the player to stop (e.g. `"publish": "done~player/done"`)

Run from CLI:
```bash
noid-play scene.json
noid-play scene.json --timeout 30
python -m noid scene.json
```

---

## 14. Common component patterns

### Trigger / source component

Emits events on a schedule or external trigger. Overrides `start()`/`stop()` to manage
a background task. Calls `_notify` to emit events. Has no `receive` handlers.

```python
@Noid.component({
    "id": "mypkg:poller",
    "properties": {"interval": {"default": 5.0}, "url": {}},
})
class PollerOid(OidComponent):
    async def start(self):
        await super().start()
        self._task = asyncio.create_task(self._loop())

    async def stop(self):
        task = getattr(self, "_task", None)
        if task and not task.done():
            task.cancel()
            try: await task
            except asyncio.CancelledError: pass
        await super().stop()

    async def _loop(self):
        while True:
            await asyncio.sleep(self.interval)
            data = await fetch(self.url)
            await self._notify("data", data)
            # optionally: await self._notify("done", {}) to stop the player
```

### Transformer / processor component

Receives on one notice, transforms, emits on another. The `subscribe`/`publish`
wiring in the scene file determines the actual topics; the component just defines
the transformation logic.

```python
@Noid.component({
    "id": "mypkg:normalizer",
    "receive": ["raw"],
})
class NormalizerOid(OidComponent):
    async def handle_raw(self, notice, message):
        normalized = {k: v / 100 for k, v in message.items()}
        await self._notify("normalized", normalized)
```

### Sink / logger component

Receives messages and does something with them (print, write to DB, emit to external
system). No outgoing notices needed unless confirming receipt.

```python
@Noid.component({
    "id": "mypkg:db-sink",
    "receive": ["store"],
    "properties": {"table": {"default": "events"}},
})
class DbSinkOid(OidComponent):
    async def handle_store(self, notice, message):
        await db.insert(self.table, message)
```

### Provider (service) component

Exposes a `provide` interface so others can `connect` and `invoke` operations on it.
Requires `component_id`. Returns values from handler methods.

```python
Noid.c_interface({"id": "itf:compute", "response": True, "operations": {"run": {}}})

@Noid.component({"id": "mypkg:worker", "provide": ["itf:compute"]})
class WorkerOid(OidComponent):
    async def handle_run(self, notice, message):
        return await compute(message["input"])
```

### Requester component

Connects to a provider and invokes operations on it. The `connect` spec in the scene
or constructor handles wiring; the component calls `_invoke` to use the service.

```python
@Noid.component({"id": "mypkg:orchestrator"})
class OrchestratorOid(OidComponent):
    async def run_pipeline(self, data):
        results = await self._invoke("itf:compute", "run", {"input": data})
        # results is a list when interface has response:True
        return results
```

---

## 15. Anti-patterns to avoid

| ❌ Wrong | ✓ Right |
|---|---|
| `from noid.core.base import OidBase` | `from noid.core.component import OidComponent` |
| Forgetting `await super().start()` in `start()` override | Always call `super().start()` first |
| Forgetting `await super().stop()` in `stop()` override | Always call `super().stop()` last |
| Calling `comp.start()` without `await` | `await comp.start()` |
| `_notify("event", data)` without `await` | `await self._notify("event", data)` |
| Listing a notice in `receive` without a matching `handle_*` method | Either add the method or remove from `receive` |
| Using a `provide` component without setting `component_id` | Pass `component_id=` to constructor or `"id":` in scene JSON |
| Calling `_invoke` before `start()` wires the connection | Always invoke after `await comp.start()` |
| Hard-coding bus topics inside component logic | Put topics in the `publish`/`subscribe` spec; keep logic topic-agnostic |
| `import Bus.i` and mutating the singleton in tests | Use `Bus()` (fresh instance) in every test |

---

## 16. Testing patterns

Use `pytest-asyncio` with `asyncio_mode = "auto"` (already configured in `pyproject.toml`).

```python
import pytest
from noid.core.bus import Bus
from noid.core.component import Noid, OidComponent

def fresh_bus() -> Bus:
    return Bus()   # never reuse Bus.i across tests

async def test_my_component_emits_reading() -> None:
    bus = fresh_bus()
    received = []
    bus.subscribe("sensor/out", lambda t, m: received.append(m))

    @Noid.component({
        "id": "test:sensor",
        "publish": "reading~sensor/out",
        "receive": ["measure"],
    })
    class TestSensor(OidComponent):
        async def handle_measure(self, notice, message):
            await self._notify("reading", {"value": message["v"] * 2})

    comp = TestSensor(bus=bus, subscribe="in/measure~measure")
    await comp.start()
    await bus.publish("in/measure", {"v": 5})
    assert received == [{"value": 10}]
    await comp.stop()
```

### Useful test patterns

```python
# Collect all messages on a topic
received = []
bus.subscribe("my/topic", lambda t, m: received.append(m))

# Check a handler was called
called = []
original = comp.handle_update
comp.handle_update = lambda n, m: called.append(m) or original(n, m)

# Threaded component: use threading.Event
import threading
done = threading.Event()
# In handler: done.set()
# In test: assert done.wait(timeout=2)

# Invoke
result = await bus.invoke("itf:math", "calc1", "add", {"a": 1, "b": 2})
```

---

## 17. Package and file layout (collections project)

```
my-collections/
  CLAUDE.md                    # this guide + project-specific notes
  pyproject.toml               # depends on noid; dev deps: pytest, pytest-asyncio
  noid_collections/
    __init__.py
    sensors/
      temperature/
        __init__.py
        temperature.py         # OidComponent subclass
        temperature_test.py    # or tests/test_temperature.py
      pressure/
        ...
    transforms/
      ...
    providers/
      ...
  scenes/
    demo.json                  # NoidPlayer scene files
    pipeline.json
  tests/
    test_temperature.py
    ...
```

Each component lives in its own subpackage alongside a test file. Interface
registrations (`Noid.c_interface`) live in the same module as the provider component.

---

## 18. Checklist before submitting a component

- [ ] `id` follows `namespace:name` convention and is unique
- [ ] Every notice in `receive` has a matching `handle_*` method (or a custom name dict)
- [ ] `await super().start()` is the first line of any `start()` override
- [ ] `await super().stop()` is the last line of any `stop()` override
- [ ] `await self._notify(...)` is always awaited
- [ ] `component_id` is set if the component uses `provide`
- [ ] Interfaces used by this component are registered before instantiation
- [ ] At least one test verifies the core behaviour via the bus (not by calling handlers directly)
- [ ] Tests use `Bus()` (not `Bus.i`)
- [ ] No web framework imports in component code
