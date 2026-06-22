# noid — Architecture

This document is the authoritative record of noid's design decisions. When implementing features, follow this guide and update it if any decision changes.

---

## 1. Guiding principles

### Mirror the JS oid library

The JS [oid](../../oid) library defines the canonical component model. noid's Python design follows it as closely as the language allows. The two key concepts that must remain aligned are the **Bus API** and the **component spec structure** (`id`, `receive`, `provide`, `publish`, `subscribe`, `properties`).

### Hexagonal architecture (ports and adapters)

The noid core is a pure-Python asyncio library. It imports no web framework and no workflow engine. All external systems — web transports (FastAPI, Django) and workflow engines (Dagster, Temporal, LangGraph) — attach via **Protocol abstractions** defined in the core and implemented in optional adapter packages.

This means:
- noid can run entirely in-process on a desktop machine, with no server.
- A deployment can swap web frameworks without touching component code.
- Multiple workflow engines can run side by side, each behind the same adapter interface.

---

## 2. Component model

### Implementation status

The component model is **implemented** in `noid/core/base.py` and `noid/core/component.py`. See [component-model.md](component-model.md) for the full reference.

### Class hierarchy

```
OidBase          (noid/core/base.py)
  └── OidComponent   (noid/core/component.py)
```

The JS hierarchy (`Primitive → OidBase → OidComponent`) is collapsed into two Python classes because there is no `HTMLElement` to extend. `OidBase` covers both `Primitive` (bus proxy methods) and the JS `OidBase` (handler dispatch, spec wiring).

No `OidUI` layer exists on the Python side — rendering is the JS layer's responsibility.

### Key building blocks

| Python construct | Purpose | JS equivalent |
|---|---|---|
| `OidBase` | Bus proxy + spec wiring + lifecycle | `Primitive` + `OidBase` |
| `OidComponent(OidBase)` | User extension point | `OidUI` / `OidBase` subclass |
| `Noid.c_interface(spec)` | Register an interface | `Oid.cInterface(spec)` |
| `@Noid.component(spec)` | Register a class as a component | `Oid.component({..., implementation: cls})` |
| `Noid.register(spec)` | JSON-driven registration (no custom class) | `Oid.component({})` with no implementation |
| `Noid.create(id, props)` | Instantiate a registered component | `Oid.create(id, props)` |
| `comp.start()` | Mount (async, shared event loop) | `connectedCallback` |
| `comp.stop()` | Unmount | `disconnectedCallback` |
| `comp.start_in_thread()` | Mount in a dedicated thread/loop | — (Python-only concept) |

### OidBase

Responsible for:
- Bus proxy methods (`_subscribe`, `_publish`, `_provide`, `_withhold`, `_connect`, `_invoke`)
- Building handler dispatch tables from `receive` and `provide` spec fields
- Parsing `subscribe`, `publish`, and `connect` spec strings into bus wiring
- Managing subscriptions for clean teardown (tracked in `_subscriptions`)
- Dispatching incoming notices to `handle_*` methods via `handle_notice`
- Propagating outgoing events via `_notify(notice, message)`
- Cross-thread delivery via `_make_thread_dispatcher`

### OidComponent

The primary extension point for application components. Inherits all machinery from `OidBase` and adds no logic of its own. Subclass this for all component work.

### Noid registry

Python equivalent of the JS `Oid` class. A module-level singleton-style class (all state on class attributes):
- `Noid._interface_reg` — registered interface specs
- `Noid._oid_reg` — registered component classes (keyed by spec id)
- `Noid.c_interface(spec)` / `Noid.get_interface(id)` — interface registry
- `Noid.component(spec)` — decorator that attaches `_spec` and property descriptors to a class
- `Noid.register(spec)` — JSON-only path; auto-creates a generic `OidComponent` subclass
- `Noid.create(id, props)` — factory

### Split component convention

When a component has both a JS (UI) part and a Python (logic) part, they live together in a directory named after the component:

```
my-sensor/
  my-sensor.js     # OidUI in oid — renders data, dispatches user actions via bus
  my-sensor.py     # OidComponent in noid — data fetch, processing, replies via bus
```

---

## 3. Bus

### Implementation status

The Bus is **implemented** in `noid/core/bus.py`.

### API (mirrors JS exactly)

```python
class Bus:
    # Message-oriented
    def subscribe(self, subscribed: str | dict, handler=None) -> None: ...
    def unsubscribe(self, subscribed: str | dict, handler=None) -> None: ...
    async def publish(self, topic: str, message: dict) -> None: ...

    # Connection-oriented
    def provide(self, c_interface: str, component_id: str, provider) -> bool: ...
    def withhold(self, c_interface: str, component_id: str) -> bool: ...
    def connect(self, c_interface: str, component_id: str, callback) -> bool: ...
    async def invoke(self, c_interface: str, component_id: str, notice: str, message: dict) -> Any: ...

Bus.i = Bus()   # module-level singleton, mirrors JS Bus.i
```

### Key properties

- **Ephemeral.** The bus carries no durable state. Durability is the workflow engine's or transport layer's responsibility.
- **In-process.** The default bus lives inside one Python process. Cross-process messaging will use an adapter (e.g., a `RedisBus`), exposing the same API.
- **Thread-safe.** A single `threading.Lock` protects mutations to the listener and provider tables. Publish takes a snapshot inside the lock and calls handlers outside it.
- **Async handlers.** `publish` and `invoke` check `asyncio.iscoroutine(result)` on each handler's return value and `await` it if so, allowing both sync and `async def` handlers transparently.

### Wildcard rules (MQTT-inspired)

`+` matches exactly one topic level (no slashes). `#` matches one or more levels. Implemented with `re.fullmatch` against the compiled regex. Mirror of JS `Bus._convertRegExp`.

### Message envelope

The standard wire format for messages relayed over a transport:

```json
{
  "type": "publish",
  "topic": "sensor/reading",
  "message": { "value": 42 },
  "meta": {
    "correlation": "<workflow-instance-id>",
    "reply_to": "<response-topic>",
    "causation": "<originating-message-id>"
  }
}
```

`meta` is optional and is only needed when a workflow engine must correlate a response to a running instance.

---

## 4. Threading model

### Motivation

Python components can be I/O-bound (awaiting database, network, or LLM calls) or CPU-bound (data processing). The threading model lets each component choose its own execution context without touching the bus or other components.

### Two modes

**Shared-loop mode (default):** All components are started with `await comp.start()` inside a single asyncio event loop and a single thread. Components cooperate via `asyncio` tasks. This is the simplest mode and sufficient for most deployments.

```python
comp = SensorOid(bus=Bus.i)
await comp.start()
```

**Dedicated-thread mode (opt-in):** A component may call `comp.start_in_thread()`, which:
1. Creates a new daemon `threading.Thread`.
2. Runs a fresh `asyncio` event loop on that thread.
3. Calls `_initialize()` (wires up all subscriptions and providers) inside that loop.
4. Blocks the *calling* thread until initialization is complete — so subscriptions are live when `start_in_thread()` returns.
5. Holds in `await stop_event.wait()` until `comp.stop_thread()` is called.

```python
comp = SensorOid(bus=Bus.i)
comp.start_in_thread()   # returns immediately after subscriptions are live
...
comp.stop_thread()
comp.join_thread()
```

### Cross-thread message delivery

The bus is shared across all threads. When a component registers subscriptions in threaded mode, each handler is wrapped by `OidBase._make_thread_dispatcher`:

```
Publisher loop (any thread)
  │
  ├─ dispatcher(topic, message)       ← called by bus.publish
  │    ├─ same loop?   → create_task(handler(topic, msg))
  │    └─ other thread → my_loop.call_soon_threadsafe(_call_in_loop)
  │                           └─ _call_in_loop runs in the component's loop
  │                               and creates a task for async handlers
  │
Component's own event loop (Thread B)
  └─ handler(topic, message)          ← executes here
```

The bus never knows about threading. The complexity is entirely inside the component's dispatcher wrapper.

### Threading diagram

```
┌─────────────────────────────────────────────────────┐
│  Bus  (thread-safe writes via threading.Lock)        │
└───────┬──────────────────────┬──────────────────────┘
        │                      │
  ┌─────▼──────┐         ┌─────▼──────┐
  │  Thread A  │         │  Thread B  │
  │  loop A    │         │  loop B    │
  │ ComponentX │  ─────▶ │ ComponentY │  cross-thread dispatch
  └────────────┘         └────────────┘  via call_soon_threadsafe
```

---

## 5. Hexagonal layers summary

```
┌───────────────────────────────────────────────────────────────────┐
│  Application components  (OidComponent subclasses)                │
├───────────────────────────────────────────────────────────────────┤
│  noid core  (Bus · OidBase · OidComponent · Noid · BusBridge)     │
│  Pure Python + asyncio — imports no web framework, no engine      │
├────────────────────────┬──────────────────────────────────────────┤
│  Transport port        │  Workflow port                            │
│  Connection (Protocol) │  WorkflowAdapter (Protocol)              │
├────────┬───────────────┼────────────┬──────────────────┬──────────┤
│FastAPI │Django/Channels│  Dagster   │  Temporal        │LangGraph │
│adapter │adapter        │  adapter   │  adapter         │adapter   │
└────────┴───────────────┴────────────┴──────────────────┴──────────┘
```

Details on each layer are in their respective documents:
- [Component model](component-model.md)
- [Player — declarative scene runner](player.md)
- [Transport adapters](transport-adapters.md)
- [Workflow integration](workflow-integration.md)

---

## 6. Package structure

```
noid/
  core/
    bus.py               # Bus class  ✓ implemented
    base.py              # OidBase    ✓ implemented
    component.py         # OidComponent + Noid registry  ✓ implemented
    player.py            # NoidPlayer (declarative scene runner)  ✓ implemented
    bridge.py            # BusBridge (transport-agnostic)  — planned
    workflow_bridge.py   # WorkflowBridge (engine-agnostic)  — planned
  transport/
    base.py              # Connection Protocol  — planned
    fastapi.py           # FastAPI adapter  [noid[fastapi]]  — planned
    django.py            # Django Channels adapter  [noid[django]]  — planned
  workflow/
    base.py              # WorkflowAdapter Protocol  — planned
    dagster.py           # Dagster adapter  [noid[dagster]]  — planned
    temporal.py          # Temporal adapter  [noid[temporal]]  — planned
    langgraph.py         # LangGraph adapter  [noid[langgraph]]  — planned

tests/
  core/
    test_bus.py          # 25 tests  ✓
    test_base.py         # 32 tests  ✓
    test_player.py       # 17 tests  ✓

playground/
  learning/
    01-bus/
      01-publish-subscribe/publish_subscribe.py   ✓
      02-wildcard/wildcard.py                     ✓
    02-component/
      01-basic/basic.py                           ✓
      02-publish-subscribe/pub_sub.py             ✓
      03-threaded/threaded.py                     ✓
    03-player/
      components.py                                    # shared example components
      01-basic/scene.json + run.py                ✓
      02-publish-subscribe/scene.json + run.py    ✓
      03-wildcards/scene.json + run.py            ✓
      04-connect/scene.json + run.py              ✓
```

---

## 7. Decision log

| # | Decision | Rationale |
|---|---|---|
| 1 | Core is framework-independent | noid must run locally with no server; web/engine choices must stay swappable |
| 2 | FastAPI and Django/Channels as parallel transport adapters | Existing Django platform must integrate; FastAPI wanted for simpler projects |
| 3 | Bus API mirrors JS oid exactly | Cognitive alignment between JS and Python developers; lower porting effort |
| 4 | Bus stays ephemeral; durability owned by workflow engine | Prevents accidental coupling of messaging layer to persistence concerns |
| 5 | Airflow rejected | Wrong paradigm for both target workloads; static DAGs, sync, out-of-process tasks |
| 6 | Two workflow engine families, not one | Dataflow workloads and agent-coordination workloads have opposite shapes; best-in-class tools differ |
| 7 | `meta` envelope optional on bus messages | Needed for workflow correlation without breaking existing plain pub/sub consumers |
| 8 | `Primitive` + JS `OidBase` collapsed into Python `OidBase` | No `HTMLElement` in Python; one class is simpler without losing any functionality |
| 9 | `receive` spec required for `handle_notice` dispatch | Mirrors JS behaviour exactly; keeps handler registration explicit and auditable |
| 10 | `handle_notice` and `_convert_notice` return handler results | Allows async `handle_*` methods to propagate their coroutines back to the bus, which awaits them; no special casing in the spec |
| 11 | Thread dispatcher wraps the handler, not the bus subscription | Bus stays simple and thread-agnostic; threading complexity is entirely inside `OidBase._make_thread_dispatcher` |
| 12 | `start_in_thread()` blocks until `_initialize()` completes | Callers can safely publish immediately after `start_in_thread()` returns; no race window |
| 13 | `Noid.register(spec)` for JSON-driven components | Pure-spec components (no Python class) are a first-class path; useful for configuration-driven deployments and for mirroring the declarative HTML element approach in JS |
| 14 | Property descriptors added to class at registration time | Backed by `_prop_<name>` instance attributes; defaults applied per-instance in `_initialize()` so multiple instances are independent |
| 15 | `NoidPlayer` as the Python equivalent of an HTML page | The JSON scene file plays the role of an HTML document; `NoidPlayer` plays the role of the browser; the same attribute names (`type`, `id`, `properties`, `publish`, `subscribe`, `connect`) are used in both |
| 16 | `start_in_thread()` delegates to `start()` / `stop()` internally | The threaded `_boot` coroutine calls `start()` (not the internal `_initialize()`), so component subclasses that override `start()` get their initialization called in dedicated-thread mode too |
| 17 | Auto-relay in `handle_notice` returns `_notify` coroutine | When no handler is registered for a notice but a publish mapping exists, `handle_notice` returns `self._notify(notice, message)` — a coroutine that the bus awaits, giving the same delivery guarantee as an explicit async handler. Enables pure-JSON relay components with zero Python code |
| 18 | `player/done` as the stop signal | Any component can end a player session by publishing to `player/done` — mirrors browser tab-close equivalent; keeps stop logic in the scene, not hard-coded in application code |
| 19 | Per-component readiness queue in `OidBase` | Async components (e.g. LLM agents) may need to gate concurrent messages. `set_ready(False/True)` on `OidBase` buffers incoming notices in a FIFO list and drains them when the component becomes ready. Queuing lives in the component, not the Bus, so the Bus remains simple and handler-agnostic. Drain is scheduled as an asyncio task in the component's own event loop, which is the same path used by all other async handlers. |
