# Workflow integration

This document describes how noid integrates with workflow engines. It is a companion to [architecture.md](architecture.md).

## Role of a workflow engine in noid

The noid bus gives **choreography** for free: components react to published events and invoke each other directly. A workflow engine adds **orchestration**: a durable, stateful coordinator with explicit sequencing, timers, retries, compensation, and human-in-the-loop steps.

The engine is not baked into the core. It attaches via a `WorkflowAdapter` Protocol, mirroring exactly the same pattern used for web framework transport adapters.

---

## Target workloads

noid is designed for two distinct workflow shapes. They differ fundamentally, so the best engine choice differs too.

### Workload A — Orange-style dataflow analysis

A network of processing nodes passing datasets downstream, optionally at scale. Think [Orange Data Mining](https://orangedatamining.com/) widgets wired together.

**Properties:** interactive, DAG-structured (mostly acyclic), data-in-data-out, sometimes larger-than-memory, latency tolerant.

**noid angle:** an Orange widget *is* a noid component; Orange's input/output channels *are* bus `provide`/`connect` wiring. Small interactive pipelines may not need a workflow engine at all — connected components on the bus suffice. Reach for an engine when you need scale, retries, or data lineage.

**Recommended engine:** [**Dagster**](https://dagster.io/) — typed data assets, lineage tracking, and a scheduler. Closest in spirit to Orange's node model.
**Scale layer:** [**Dask**](https://www.dask.org/) — scales pandas/numpy/scikit-learn pipelines nearly transparently. Add [Ray](https://www.ray.io/) if you also need distributed actors (relevant to Workload B).
**Lighter alternative:** [**Prefect**](https://www.prefect.io/) — async-native, dynamic flows, simpler setup than Dagster.

### Workload B — Agent coordination over LLMs/SLMs

Coordinating multiple AI agents that call language models, use tools, fork/join, and sometimes wait for human decisions.

**Properties:** long-running, dynamic, branching and potentially cyclic (agent loops), stateful, human-in-the-loop, high latency per step.

**noid angle:** each agent is a noid component. The engine owns control flow and durability; the bus carries peer-to-peer messages between agents.

> **Note:** The choice of LLM/SLM provider and SDK is separate from workflow orchestration and is not decided here.

**Recommended engine (production/durable):** [**Temporal**](https://temporal.io/) — durable execution that survives process crashes, retries long LLM calls automatically, signals for human-in-the-loop, async Python SDK. Best choice when reliability matters.
**Recommended engine (lighter/faster iteration):** [**LangGraph**](https://langchain-ai.github.io/langgraph/) — purpose-built for agent graphs: cycles, shared mutable state, checkpointing, pause/resume. Lower infrastructure cost than Temporal.

**Temporal determinism constraint:** Temporal workflow code must be deterministic. All bus I/O and LLM calls must happen inside Temporal *activities*, never inside workflow definition code. The adapter must enforce this boundary. LangGraph has no such constraint.

---

## Engines not chosen

| Engine | Reason not chosen |
|---|---|
| **Airflow** | Wrong paradigm for both workloads. Static DAGs, synchronous tasks, out-of-process execution, no compute scaling. Wide adoption is in the ETL/batch-pipeline niche, which neither workload resembles. |
| **SpiffWorkflow** | Embeddable BPMN engine — good fit if BPMN process modeling is wanted, but neither target workload is BPMN-shaped. Remains an option for future use. |
| **Camunda 8 / Zeebe** | JVM server required; Python is only a job-worker client. Infrastructure cost not justified. |

---

## The WorkflowAdapter protocol

```python
# noid/workflow/base.py
from typing import Protocol

class WorkflowAdapter(Protocol):
    async def start(
        self,
        definition: str,        # workflow name or path
        payload: dict,          # initial input
        correlation: str,       # instance ID for later signalling
    ) -> str: ...               # returns the running instance ID

    async def signal(
        self,
        correlation: str,       # instance to signal
        name: str,              # signal name
        payload: dict,
    ) -> None: ...
```

## WorkflowBridge

The core `WorkflowBridge` wires bus topic patterns to workflow lifecycle events. It is engine-agnostic:

```python
# noid/core/workflow_bridge.py
class WorkflowBridge:
    def __init__(self, bus: Bus, adapter: WorkflowAdapter, triggers: list[str]):
        self.bus = bus
        self.adapter = adapter
        for pattern in triggers:
            bus.subscribe(pattern, self._on_trigger)

    async def _on_trigger(self, topic: str, message: dict) -> None:
        meta = message.get("meta", {})
        correlation = meta.get("correlation") or topic
        if meta.get("signal"):
            await self.adapter.signal(correlation, meta["signal"], message)
        else:
            await self.adapter.start(topic, message, correlation)
```

Activities inside the engine invoke noid components over the bus:

```python
# Inside a Dagster op or Temporal activity:
await bus.invoke("data-processor", "default", "transform", {"input": df})
```

---

## Choosing between engines

If you are unsure which engine to use, this decision tree applies:

```
Is the workflow a scheduled/batch data pipeline?
  ├─ Yes, interactive DAG of processing steps → Dagster (+ Dask for scale)
  └─ No

Does it coordinate AI agents or need durable retries across crashes?
  ├─ Yes, production reliability required → Temporal
  └─ Yes, prototype / low infra cost preferred → LangGraph

Can it be expressed as connected noid components reacting to bus events?
  └─ Yes → no engine needed; use choreography
```

Multiple engines can be active simultaneously — each behind its own `WorkflowAdapter` instance. A deployment might run Dagster for data pipelines and LangGraph for agent workflows concurrently, both connecting to the same noid bus.
