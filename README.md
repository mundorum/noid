# noid — Node OID

**noid** (node + [oid](../oid)) is the Python-side component framework of the **mundorum** ecosystem. It mirrors the architecture of the companion JavaScript library [oid](../oid) so that components can be split cleanly across the stack: the JS side handles UI and rendering in the browser; the Python side handles data processing, workflows, and business logic on the server — or runs standalone on the desktop.

The name reflects the role: a **noid** is a processing *node* in a network of oid components.

## Companion project

noid and oid share design principles and should always be developed together. The JS library lives at [`~/git/mundorum/oid`](../oid).

## Core concepts

| Concept | Role |
|---|---|
| **Bus** | Asyncio pub/sub + provide/connect/invoke, same API as the JS Bus |
| **OidBase** | Spec-driven handler building, lifecycle, topic/notice mapping |
| **OidComponent** | Extension point for application components |
| **Connection** | Protocol abstraction that decouples the Bus bridge from any web framework |
| **WorkflowAdapter** | Protocol abstraction that decouples workflow orchestration from the engine |

## Design principles

- **Framework-independent core.** The Bus, component model, and bridge logic import no web framework. noid can run with no server at all.
- **Swappable adapters.** Web frameworks (FastAPI, Django/Channels) and workflow engines (Dagster, Temporal, LangGraph) attach via Protocol adapters, not hard dependencies.
- **Mirror the JS API.** Where the JS oid library defines the shape of something, the Python side replicates it as closely as the language allows.

## Installation

```
pip install n-o-id              # core only — no web or workflow deps
pip install n-o-id[fastapi]     # + FastAPI transport adapter
pip install n-o-id[django]      # + Django Channels transport adapter
pip install n-o-id[dagster]     # + Dagster workflow adapter (dataflow workloads)
pip install n-o-id[temporal]    # + Temporal workflow adapter (agent workloads)
pip install n-o-id[langgraph]   # + LangGraph workflow adapter (agent workloads)
```

## Documentation

- [Architecture](docs/architecture.md) — design decisions, component model, bus, hexagonal structure
- [Transport adapters](docs/transport-adapters.md) — connecting noid to FastAPI or Django
- [Workflow integration](docs/workflow-integration.md) — orchestrating dataflows and LLM agents
