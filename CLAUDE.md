# noid

This project is the Python implementation of **noid** (Node OID) — the Python-side component framework of the mundorum ecosystem. It works in close alignment with the companion project at `~/git/mundorum/oid` (the JavaScript/browser-facing OID library).

## Companion project

Whenever working in this project, also include `~/git/mundorum/oid` as an additional working directory. Both projects share related logic and changes in one often require corresponding updates in the other.

## Additional directories

- `~/git/mundorum/oid` — companion OID library; always load alongside this project.

## Architecture

All design decisions are recorded in `docs/`. **Read the relevant document before implementing any feature.**

- [`docs/architecture.md`](docs/architecture.md) — authoritative design: component model, bus, hexagonal structure, decision log
- [`docs/namespaces.md`](docs/namespaces.md) — namespace system: module and resource namespaces, resolution order, `kind: resource` opt-in
- [`docs/scene-package.md`](docs/scene-package.md) — on-disk scene package format (directory + scene.json + components/ + data/)
- [`docs/transport-adapters.md`](docs/transport-adapters.md) — web framework integration (FastAPI, Django/Channels)
- [`docs/workflow-integration.md`](docs/workflow-integration.md) — workflow engine choices (Dagster/Dask for dataflows, Temporal/LangGraph for agents)

Key invariants to never violate:
- The `noid/core/` package must import no web framework and no workflow engine.
- Transport and workflow engines attach via Protocol adapters only.
- The Bus API must mirror the JS oid `Bus` class exactly.
- If a design decision changes, update `docs/` in the same commit.

## Sibling projects

A sibling **noid-collections** project implements reusable noid components built on this framework.

- [`docs/component-authoring-guide.md`](docs/component-authoring-guide.md) — exhaustive guide for implementing components correctly; intended for AI-assisted development in collections projects.
- [`docs/sibling-CLAUDE-template.md`](docs/sibling-CLAUDE-template.md) — ready-to-use CLAUDE.md template for any sibling collections project. Copy it to that project's root as `CLAUDE.md` before starting work there.

## Platform context

The user has an existing **Django** platform. noid must integrate with it via the Django/Channels transport adapter without requiring the full Django stack for standalone use.

## Workflow workloads

Two target workload types drive the workflow engine choices:
1. Orange-Datamining-style dataflow analysis → Dagster + Dask
2. LLM/SLM agent coordination → Temporal (production) or LangGraph (lighter)

Airflow is explicitly rejected for both workloads (see decision log in `docs/architecture.md`).
