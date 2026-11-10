# ReverseDB

Research prototype for capability-preserving software reconstruction: remove unnecessary support and fuse operations across application/database boundaries while retaining complete business data.

**Start with [guide.md](guide.md)** for the student handoff, server migration, ten-project evaluation plan, and figure specifications.

The framework separates candidate discovery, semantic admission, measurement, and selection. Unknown obligations retain the original implementation. Language-specific adapters can differ; the shared decision method must remain the same.

This is a **source-only research handoff**, not a turnkey replication package. Datasets, raw measurements, historical evidence, downloaded upstream trees, binaries, and virtual environments are excluded. Upstream download references and fixture-generation entry points are in [handoff/artifacts.json](handoff/artifacts.json). Historical evidence still needs a separately hosted, checksum-verified archive; no public dataset URL is currently supplied. Small workload definitions, contracts, model parameters, and manuscript aggregate tables are source/configuration and are included.

```sh
python3 -B scripts/handoff_smoke.py
python3 scripts/fetch_handoff_sources.py --list
```

Current principal development subjects: Java/TPC-C, alf.io, Oscar, beets, and Fossil. Five additional projects are **proposed**, not implemented or evaluated. Existing experiments admit Java fusion and Fossil deletion; the other three retain the original. Experimental improvements alone do not establish semantic admission or held-out transfer.

Core: `prototype/adaptive_planning/`. Experiment adapters and runners: `prototype/` and `scripts/`. Manuscript: `paper/main.tex`. Historical research notes: `docs/` (some contain workstation-specific instructions; the handoff guide takes precedence for new work).

Retain upstream attribution and license notices, including `app/tpcc/BENCHBASE-LICENSE`. No new project-wide redistribution license has been assigned. The repository is private; publication and an anonymous review artifact are separate steps.
