# noid — Architecture

This document is the authoritative record of noid's design decisions. When implementing features, follow this guide and update it if any decision changes.

## 1. Guiding principles

### Mirror the JS oid library

The JS [oid](../../oid) library defines the canonical component model. noid's Python design follows it as closely as the language allows. The two key concepts that must remain aligned are the **Bus API** and the **component spec structure** (`{id, receive, provide, properties}`).

### Hexagonal architecture (ports and adapters)

The noid core is a pure-Python asyncio library. It imports no web framework and no workflow engine. All external systems — web transports (FastAPI, Django) and workflow engines (Dagster, Temporal, LangGraph) — attach via **Protocol abstractions** defined in the core and implemented in optional adapter packages.

This means:
- noid can run entirely in-process on a desktop machine, with no server.
- A deployment can swap web frameworks without touching component code.
- Multiple workflow engines can run side by side, each behind the same adapter interface.

---

## 2. Component model

The component model is a direct Python port of the JS oid hierarchy.

### Class hierarchy

```
OidBase
  └── OidComponent
```

No `OidUI` layer exists on the Python side — that is the JS layer's responsibility.

### Key building blocks

| Python construct | Purpose | JS equivalent |
|---|---|---|
| `abc.ABC` + `Protocol` | Interface definitions | `Oid.cInterface(spec)` |
| `dataclass` or `TypedDict` | Component spec | `{id, receive, provide, properties}` |
| Class decorator `@oid_component(spec)` | Component registration | `Oid.component(spec)` |
| `asyncio` | Async handler model | JS async publish/invoke |

### OidBase

Responsible for:
- Reading the component spec at class registration time
- Building handler dispatch tables from `receive` and `provide` declarations
- Managing the component lifecycle (mount, unmount)
- Mapping topic patterns to handler methods

### OidComponent

The primary extension point for application components. It inherits OidBase and adds no logic of its own — it exists as a named layer so that future framework-specific subclasses (e.g., `OidWorkflow`) have a clear place in the hierarchy.

### Split component convention

When a component has both a JS (UI) part and a Python (logic) part, they live together in a directory named after the component:

```
my-sensor/
  my-sensor.js     # OidUI in oid — renders data, dispatches user actions via bus
  my-sensor.py     # OidComponent in noid — data fetch, processing, replies via bus
```

---

## 3. Bus

The Bus is a direct asyncio port of the ~175-line JS `Bus` class in oid. It uses Python `re` for MQTT-style wildcard patterns (`+` → `[^/]+`, `#` → `.+`).

### API (mirrors JS exactly)

```python
class Bus:
    def subscribe(self, topic: str, handler) -> None: ...
    async def publish(self, topic: str, message: dict) -> None: ...
    def provide(self, c_interface: str, id: str, provider) -> None: ...
    def withhold(self, c_interface: str, id: str) -> None: ...
    def connect(self, c_interface: str, id: str, callback) -> None: ...
    async def invoke(self, c_interface: str, id: str, notice: str, message: dict) -> None: ...
```

### Key properties

- **Ephemeral.** The bus carries no durable state. Durability is the workflow engine's responsibility, or the transport layer's (Redis-backed bus for cross-process delivery).
- **In-process.** The default bus lives inside one asyncio event loop. Cross-process and cross-host messaging use an adapter (e.g., a `RedisBus` backed by `aioredis`), exposing the same API.

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

`meta` is optional. Omitting it leaves plain pub/sub unaffected. It is required when a workflow engine needs to correlate a response to a running instance.

---

## 4. Hexagonal layers summary

```
┌───────────────────────────────────────────────────────────────────┐
│  Application components  (OidComponent subclasses)                │
├───────────────────────────────────────────────────────────────────┤
│  noid core  (Bus · OidBase · OidComponent · BusBridge)            │
│  Pure Python + asyncio — imports no web framework, no engine      │
├────────────────────────┬──────────────────────────────────────────┤
│  Transport port        │  Workflow port                            │
│  Connection (Protocol) │  WorkflowAdapter (Protocol)              │
├────────┬───────────────┼────────────┬──────────────────┬──────────┤
│FastAPI │Django/Channels│  Dagster   │  Temporal        │LangGraph │
│adapter │adapter        │  adapter   │  adapter         │adapter   │
└────────┴───────────────┴────────────┴──────────────────┴──────────┘
```

Details on the transport and workflow layers are in their respective documents:

- [Transport adapters](transport-adapters.md)
- [Workflow integration](workflow-integration.md)

---

## 5. Package structure

```
noid/
  core/
    bus.py               # Bus class
    base.py              # OidBase
    component.py         # OidComponent
    bridge.py            # BusBridge (transport-agnostic)
    workflow_bridge.py   # WorkflowBridge (engine-agnostic)
  transport/
    base.py              # Connection Protocol
    fastapi.py           # FastAPI adapter  [noid[fastapi]]
    django.py            # Django Channels adapter  [noid[django]]
  workflow/
    base.py              # WorkflowAdapter Protocol
    dagster.py           # Dagster adapter  [noid[dagster]]
    temporal.py          # Temporal adapter  [noid[temporal]]
    langgraph.py         # LangGraph adapter  [noid[langgraph]]
```

---

## 6. Decision log

Decisions are recorded here as they are made, so the reasoning remains alongside the design.

| # | Decision | Rationale |
|---|---|---|
| 1 | Core is framework-independent | noid must run locally with no server; web/engine choices must stay swappable |
| 2 | FastAPI and Django/Channels as parallel transport adapters | Existing Django platform must integrate; FastAPI wanted for simpler projects |
| 3 | Bus API mirrors JS oid exactly | Cognitive alignment between JS and Python developers; lower porting effort |
| 4 | Bus stays ephemeral; durability owned by workflow engine | Prevents accidental coupling of messaging layer to persistence concerns |
| 5 | Airflow rejected | Wrong paradigm for both target workloads; static DAGs, sync, out-of-process tasks |
| 6 | Two workflow engine families, not one | Dataflow workloads and agent-coordination workloads have opposite shapes; best-in-class tools differ |
| 7 | `meta` envelope optional on bus messages | Needed for workflow correlation without breaking existing plain pub/sub consumers |
