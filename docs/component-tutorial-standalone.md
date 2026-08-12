# Building a noid Component — Standalone Tutorial

This document is self-contained. It is meant to be uploaded as a single file to
any LLM's chat interface (web-based, no file or tool access), together with a
request such as:

> "Using the attached document, build a noid component that does X."

Everything the LLM needs to generate a correct, runnable component is in this
one file — there is no need to fetch anything else.

**If you are the human running this tutorial:** after the LLM generates code,
save it to a `.py` file in your own noid project (see §1 for setup) and run it
as described in §4 or §14.

---

## 0. What is noid?

**noid** (Node OID) is a Python framework for building small, event-driven
components that communicate exclusively through a shared in-process **Bus**.
Components never call each other directly — they publish notices to bus
topics and subscribe to topics, decoupling producers from consumers. This
mirrors a companion JavaScript library (`oid`) used for browser components, so
the same mental model and spec vocabulary (`id`, `receive`, `provide`,
`publish`, `subscribe`, `properties`) applies on both sides.

A **scene** (a JSON file) declares which components to instantiate and how to
wire them together, and the **NoidPlayer** runs that scene — conceptually
like a browser loading an HTML page, except the "page" is JSON and there is
no browser.

---

## 1. Install

```bash
pip install mundorum-noid
```

Requires Python 3.10+. For running tests in your own project:

```bash
pip install "mundorum-noid[dev]"   # pytest, pytest-asyncio, pyyaml
```

---

## 2. Mental model

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

## 3. Mandatory imports

```python
from noid.core.bus import Bus
from noid.core.component import Noid, OidComponent
```

Never import from `noid.core.base` directly — use `OidComponent` from `component.py`.

---

## 4. Complete worked example (read this first)

This is a full, runnable round-trip: one component that greets, one that logs,
and a scene file that wires and runs them. Read it before the reference
sections below — it shows how every piece (spec, handler, scene, player
lifecycle) fits together.

**`components.py`**

```python
from noid.core.component import Noid, OidComponent


@Noid.component({
    "id": "tutorial:greeter",
    "properties": {"name": {"default": "World"}},
    "receive": ["greet"],
    "publish": "greeted~greeter/out",
})
class GreeterOid(OidComponent):
    async def handle_greet(self, notice, message):
        await self._notify("greeted", {"text": f"Hello, {self.name}!"})


@Noid.component({
    "id": "tutorial:logger",
    "receive": ["log"],
})
class LoggerOid(OidComponent):
    async def handle_log(self, notice, message):
        print(message["text"])
        await self._notify("done", {})
```

**`scene.json`**

```json
{
  "title": "Greeter tutorial",
  "imports": ["./components.py"],
  "components": [
    {
      "type": "tutorial:greeter",
      "properties": {"name": "noid"},
      "subscribe": "player/start~greet"
    },
    {
      "type": "tutorial:logger",
      "subscribe": "greeter/out~log",
      "publish": "done~player/done"
    }
  ]
}
```

**Run it:**

```bash
noid-play scene.json
```

**What happens:** `NoidPlayer` starts both components, then publishes
`player/start` once everything is wired live. The greeter's `subscribe` maps
that reserved topic to its own `greet` notice, so `handle_greet` fires
immediately, calling `self._notify("greeted", ...)`, which publishes to
`greeter/out` (per its `publish` spec). The logger's `subscribe` maps
`greeter/out` to its `log` notice, so `handle_log` fires, prints the text, and
publishes `done` → `player/done` — which tells the player to stop. Output:

```
Hello, noid!
```

Everything below is reference material for building components beyond this
minimal example.

---

## 5. Defining a component

### 5a. Decorator form (recommended)

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

### 5b. JSON-only form (no custom logic)

```python
Noid.register({
    "id": "mypkg:relay",
    "subscribe": "in/raw~forward",
    "publish":   "forward~out/processed",
    # no 'receive' needed — auto-relay fires when no handler exists
})
```

### 5c. Non-decorator programmatic form

```python
class SensorOid(OidComponent): ...

Noid.component({"id": "mypkg:sensor", "implementation": SensorOid})
```

---

## 6. Spec field reference

| Field | Type | Description |
|---|---|---|
| `id` | str | **Required.** `"namespace:name"` convention. Must be unique. |
| `name` | str | Human-readable display name. Auto-derived from `id` if absent. Used by metadata tools. |
| `description` | str | Component description. Used by metadata tools; falls back to the class docstring. |
| `properties` | dict | Named properties. Keys = property names. Values = `{"default": x, "readonly": bool, "kind": str, "description": str}`. Supported `kind` values: `"resource"` (file path, resolved by NoidPlayer), `"text"` (multiline string, rendered as textarea in the platform editor). |
| `receive` | list or dict | Declares which notices this component handles (required for handler dispatch). Dict form supports `{"notice": {"description": str}}`. |
| `output_notices` | dict | Declares output notices with descriptions: `{"notice": {"description": str}}`. Used by metadata tools only. |
| `subscribe` | str or dict | Wires bus topics to internal notices. |
| `publish` | str or dict | Wires internal notices to bus topics. |
| `provide` | list | Interface ids this component provides. Requires `component_id`. |
| `connect` | str | Interface ids and provider ids this component connects to. |

The `name`, `description`, `output_notices`, and per-field `description` keys are **metadata-only**: the runtime ignores them. They exist solely to feed the `noid-extract-meta` tool (see §21).

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

## 7. Handler naming

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

## 8. The `receive` requirement

`handle_notice` dispatches **only** to notices declared in `receive`.
If a notice arrives but is not in `receive`, it is silently ignored.

**Exception — auto-relay:** if a notice has no handler but a `publish` mapping exists
for it, the message is automatically forwarded. This works only for pure-JSON relay
components created with `Noid.register`. Custom classes should always declare `receive`.

---

## 9. Handler signatures

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

## 10. Emitting events: `_notify`

```python
await self._notify("reading", {"value": self.value, "unit": self.unit})
```

- `_notify` looks up the notice in `_map_notice_topic` (built from `publish` spec).
- If no mapping exists, the call is a no-op (no error).
- Always `await` it — it is `async`.

---

## 11. Properties

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

**File-path properties — `"kind": "resource"`:**

Add `"kind": "resource"` to any property that holds a file path. `NoidPlayer` then
resolves namespace-prefixed values (e.g. `"shared:data/intro.txt"`) to absolute
filesystem paths before passing them to the component. Without this, the raw string
is handed through unchanged.

```python
"properties": {
    "input_file": {
        "default": "",
        "kind": "resource",
        "description": "Path to a file. Prefix shared: resolves to the shared resource area.",
    },
}
```

**Multiline text properties — `"kind": "text"`:**

Add `"kind": "text"` to any property whose value is expected to span multiple lines
(e.g. prompt templates, inline CSV content, rule sets). The runtime treats
the value as a plain string — `kind` is purely a hint for tooling. The platform
editor renders these properties as a resizable textarea instead of a single-line input.

```python
"properties": {
    "prompt_template": {
        "default": "{{input}}",
        "kind": "text",
        "description": "Prompt template. Supports {{input}} and any message key as {{placeholder}}.",
    },
}
```

---

## 12. Lifecycle

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

## 13. Instantiation

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

## 14. Interfaces

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

## 15. Scene JSON (NoidPlayer)

The player loads a JSON file and instantiates components declaratively —
conceptually like a browser loading an HTML page, except the "page" is JSON.

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
- Sections are processed in this order: `imports`, `interfaces`, `register`, `components`

### The `player/` reserved topic protocol

`NoidPlayer` uses two reserved topics to signal scene lifecycle events, published
automatically — components subscribe to them via the normal `subscribe` spec, no
special Python code needed.

- **`player/start`** — published once, after all components are started and their
  subscriptions are live. Any component that needs a "go" signal subscribes to it,
  e.g. `"subscribe": "player/start~trigger"`. This is how source components (with no
  external event to react to) kick off their first action.
- **`player/done`** — `NoidPlayer.run()` blocks until this topic is published, then
  stops and cleans up. Any component can end the session:
  `"publish": "done~player/done"`.

Run from CLI:
```bash
noid-play scene.json
noid-play scene.json --timeout 30
python -m noid scene.json
```

Or programmatically:
```python
from noid.core.player import NoidPlayer
NoidPlayer.play("scene.json")
```

---

## 16. Common component patterns

### Trigger / source component

Emits events on a schedule or external trigger. Overrides `start()`/`stop()` to manage
a background task. Calls `_notify` to emit events.

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

### Controllable timer / source component

A source that manages its own background task but also accepts control notices
(`start`, `stop`, `reset`). The task is **not** started in `start()` — it waits for
an explicit `start` notice, which lets the scene wire `player/start~start` when
auto-start is wanted.

```python
@Noid.component({
    "id": "mypkg:timer",
    "properties": {"period": {"default": 1.0}, "cycles": {"default": 0}},
    "receive": {
        "start": {"description": "Begin emitting pulses."},
        "stop":  {"description": "Halt without resetting the count."},
        "reset": {"description": "Halt and reset the count to zero."},
    },
    "publish": "pulse~mypkg/pulse;done~mypkg/done",
})
class MyTimerOid(OidComponent):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._task = None
        self._count = 0

    async def stop(self):
        await self._stop_task()
        await super().stop()

    async def handle_start(self, notice, message):
        if self._task and not self._task.done():
            return   # already running
        self._task = asyncio.create_task(self._run())

    async def handle_stop(self, notice, message):
        await self._stop_task()

    async def handle_reset(self, notice, message):
        await self._stop_task()
        self._count = 0

    async def _stop_task(self):
        if self._task and not self._task.done():
            self._task.cancel()
            try: await self._task
            except asyncio.CancelledError: pass

    async def _run(self):
        try:
            while True:
                await asyncio.sleep(float(self.period))
                self._count += 1
                await self._notify("pulse", {"count": self._count})
                cycles = int(self.cycles) if self.cycles else 0
                if cycles and self._count >= cycles:
                    await self._notify("done", {})
                    break
        except asyncio.CancelledError:
            pass
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

## 17. Anti-patterns to avoid

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

## 18. Testing patterns

Use `pytest-asyncio` with `asyncio_mode = "auto"` set in `pyproject.toml`.

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

# Threaded component: use threading.Event
import threading
done = threading.Event()
# In handler: done.set()
# In test: assert done.wait(timeout=2)

# Invoke
result = await bus.invoke("itf:math", "calc1", "add", {"a": 1, "b": 2})
```

---

## 19. Project layout

A minimal noid project that ships components:

```
my-project/
  pyproject.toml               # depends on mundorum-noid; dev deps: pytest, pytest-asyncio
  my_components/
    __init__.py
    sensor.py                  # OidComponent subclass(es)
    sensor_test.py
  scenes/
    demo.json                  # NoidPlayer scene files
  tests/
    test_sensor.py
```

Each component can live in its own module alongside a test file. Interface
registrations (`Noid.c_interface`) live in the same module as the provider component.

---

## 20. Component metadata (optional)

Each component can ship a `.meta.yaml` file alongside its Python module. This
file is the contract between the component and composition tools — tools read
it instead of importing or parsing Python. Skip this section for a first
component; add it once the component is ready to be shared or catalogued.

### Enriching the spec

Add the optional metadata fields directly in the `@Noid.component` spec. The
runtime ignores all of them; they are consumed only by `noid-extract-meta`.

```python
@Noid.component({
    "id": "data:text-source",
    "name": "Text Source",                          # display name (optional)
    "description": "Publishes text content as a message whenever a load notice arrives.",
    "properties": {
        "text":       {"default": "", "description": "Inline text to publish. Ignored if input_file is set."},
        "input_file": {"default": "", "kind": "resource", "description": "Path to a text file (takes precedence)."},
        "label":      {"default": "text", "description": "Label in the published payload."},
    },
    "receive": {
        "load": {"description": "Loads and publishes the text content."},
    },
    "publish": "text~data/text/output;done~data/text/done",
    "output_notices": {
        "text": {"description": "Emitted when loaded. Payload keys: label (str), content (str)."},
        "done": {"description": "Emitted after text, signaling pipeline completion."},
    },
})
class TextSourceOid(OidComponent):
    ...
```

Key rules:
- `receive` dict form: value is `{"description": "..."}` (and optionally `"handler": "method_name"`).
  The old string-value form `{"notice": "method_name"}` still works unchanged.
- `output_notices` is separate from `publish` because `publish` is a routing map
  (notice → topic), not a notice declaration. Descriptions go in `output_notices`.
- `name` is derived automatically from `id` if absent (`"data:text-source"` → `"Text Source"`).
- `description` falls back to the class docstring if absent from the spec.

### Generating the YAML file

```bash
# Write <component-id>.meta.yaml alongside the source file
noid-extract-meta my_components/sensor.py

# Write to an explicit path
noid-extract-meta sensor.py --out sensor.meta.yaml

# Print to stdout (inspect or pipe to a formatter)
noid-extract-meta sensor.py --stdout
```

### YAML schema

```yaml
id: data:text-source
name: Text Source
description: Publishes its text property as a message whenever triggered.

properties:
  content:
    description: Inline text content to publish. Ignored if input_file is set.
    default: ''
    required: false
    kind: text          # rendered as a textarea in the platform editor
  input_file:
    description: Path to a text file (takes precedence over content).
    default: ''
    required: false
    kind: resource      # NoidPlayer resolves namespace-prefixed values
  label:
    description: Label in the published payload.
    default: text
    required: false

input_notices:
  trigger:
    description: Triggers publication of the text content.

output_notices:
  text:
    description: 'Emitted when triggered. Payload keys: label (str), content (str).'
  done:
    description: Emitted after text, signaling pipeline completion.

# Only present when the component provides interfaces:
provides:
  - id: itf:compute
    operations:
      run:
        description: Execute the computation and return the result.

# Only present when the component connects to interfaces:
connects:
  - itf:store
```

---

## 21. Checklist before submitting a component

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
- [ ] Spec enriched with `description`, property descriptions, and `output_notices` (see §20, optional)
- [ ] File-path properties declare `"kind": "resource"` so NoidPlayer resolves namespace-prefixed values
- [ ] Multiline-text properties declare `"kind": "text"` so the platform editor renders a textarea
