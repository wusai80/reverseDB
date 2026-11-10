# ReverseDB: student continuation guide

Handoff date: 2026-11-10. The next deliverable is a reproducible server evaluation of ten projects using one shared framework, with figures that explain both the mechanism and its limitations.

## 1. Research objective and current position

The question is: **after recovering a program's relevant functionality, can we implement that capability with less support code or fewer application/database crossings, while preserving its required effects and complete data?**

Observed execution is evidence about a capability, not its definition. A module absent from one trace is not automatically removable. Static references are not necessarily executed calls. Unknown dispatch, identity, errors, initialization, transactions, locks, and recovery obligations must conservatively retain the original behavior unless the declared contract justifies a transformation.

Do not turn this into five or ten unrelated hand optimizers. Language and runtime adapters may differ, but all projects use the same framework interfaces, admission discipline, and frozen selection policy. Do not prune records, fields, media, or business cases to manufacture speedups. Do not optimize by silently weakening durability or concurrency.

### What already exists

| Component | Read first | Role |
|---|---|---|
| Public API | `prototype/adaptive_planning/api.py` | Candidate provider, semantic validator, measurement provider, selection policy |
| Common framework | `prototype/adaptive_planning/framework.py` | Context/artifact binding, composition checks, original fallback |
| Current adapters | `prototype/adaptive_planning/adapters.py`, `description_adapter.py` | Connect reviewed evidence to existing algorithms |
| Deletion reasoning | `prototype/module_pruning/demand_model.py` | Required/possible dependency closure and conservative decisions |
| Fusion reasoning | `scripts/fusion_placement.py`, `fusion_regions.py`, `placement_frontend.py` | Effect-aware region legality and placement |
| API documentation | `paper/adaptive-planning-api.txt` | Contracts and extension examples |
| Paper | `paper/main.tex`, `paper/results-tables.tex` | Current claims and aggregate tables |
| Fresh evaluation | `scripts/run_five_project_evaluation.py`, `audit_five_project_evaluation.py` | Historical fixed-schedule execution and audit |

The four interfaces are `CandidateProvider.propose`, `SemanticValidator.validate`, `MeasurementProvider.measure`, and `SelectionPolicy.choose`. Validation returns allow/retain/unknown; only admitted plans enter performance selection. A composition needs its own validation and whole-plan measurements. Never add individual speedups to estimate the composition.

The default ranking uses at least six paired blocks, 4,000 bootstrap draws, seed 1729, alpha 0.05, and a minimum ratio of 1.02. Preserve its exact implementation during transfer experiments. The current system still needs reviewed contracts, recovery summaries, and lowerings. It is not automatic whole-program decompilation or a universal equivalence proof.

### Current evidence: five development projects

These are the five principal subjects. Paperless, Chatwoot, and Saleor are earlier explorations, not extra completed subjects in this count.

| Subject | Current framework choice | Fresh local observation | Interpretation |
|---|---|---|---|
| Java/TPC-C + PostgreSQL | Fusion | 1.803× | Admitted, scoped success |
| alf.io + PostgreSQL | Original | A/A ratio 1.027× | Identical code; variation, not an optimization |
| Django Oscar + PostgreSQL | Original | Experimental ratio 1.046× | Missing admission obligations; an earlier batch had no stable gain |
| beets + SQLite | Original | Experimental ratio 1.101× | Timing benefit does not close semantic gaps |
| Fossil + SQLite | Deletion | Hash 1.844×; branch 1.222× | Two workloads of one admitted subject |

Ratios above are **ratios of arm medians**, not geometric paired ratios or confidence bounds. They are workstation results, not server results or held-out transfer. Timing boundaries differ by subject. Do not pool absolute milliseconds or overwrite older negative batches.

The latest five-project run has 402 application executions; the separate Fossil module factorial has 508. These are execution counts, not independent statistical sample sizes. The factorial separates SQL-side presentation work, C-side formatting/private-status work, and their combination. SQL-side removal dominates; positive synergy is not established.

## 2. First priority: move evaluation to a stable server

**Current status:** no server has been configured by this handoff. The source repository is ready for transfer, but the legacy experiment runners are not yet portable. Do not claim that cloning alone reproduces the paper.

### Step A — obtain a clean environment

Use a dedicated Linux CPU server, preferably with local SSD storage and sufficient RAM to avoid swapping. Start planning around 8 physical cores, 32 GiB RAM, and 100 GiB free disk; these are starting resources, not measured minimum requirements. A GPU is unnecessary for the current benchmarks. If a remote GPU machine is available, its CPU/storage can be used without enabling GPU-dependent execution.

1. Clone this private repository on the server using the student's own authorized GitHub access. Never copy a personal token into the repository.
2. Record OS/kernel, CPU model and architecture, physical/logical cores, memory, filesystem/storage, compiler/linker, JDK, Python, SQLite, PostgreSQL, Docker, and container image digests. Record frequency policy, CPU affinity, container limits, NUMA placement where applicable, and background load.
3. Rebuild native and Java artifacts and recreate Python environments. Do not copy a macOS virtual environment, executable, or Docker image identity onto an x86 server and call it the same deployment.
4. Keep source, downloaded artifacts, immutable fixture snapshots, and per-run output in separate directories. `work/`, `downloads/`, `data/`, `evidence/`, and `output/` are ignored by Git.
5. Run the source-only smoke checks first:

```sh
python3 -B scripts/handoff_smoke.py
python3 scripts/fetch_handoff_sources.py --list
# Downloads pinned upstream source trees; does not install or execute them:
python3 scripts/fetch_handoff_sources.py --all
```

The download helper uses full Git commits and refuses to overwrite existing directories. It is not a dependency installer. Downloaded trees retain upstream licenses. `sources.lock.json` at the root belongs to the earlier three-project exploration; use `handoff/artifacts.json` for the principal five.

### Step B — restore or regenerate data outside GitHub

There are two distinct inputs:

- **Upstream software/dependencies:** download from the pinned repositories and release references in `handoff/artifacts.json`; build for the server and record dependency hashes. alf.io's existing JDK lock is macOS ARM64-specific and must be replaced with a server lock. Python package versions from the workstation are not a portable binary lock.
- **Experimental data and evidence:** obtain an independently hosted archive from the supervisor, or regenerate fixtures with the original generators. This repository deliberately contains neither the data nor raw measurement bundles. **There is currently no published archive URL.** Do not invent a download location or claim old evidence is available from this repository.

For an externally hosted dataset/archive, create a local manifest following `handoff/external-data.example.json`, filling its actual HTTPS URL, SHA-256, and byte length. Download with:

```sh
python3 scripts/download_handoff_data.py handoff/local-data.json --out downloads
```

The helper verifies bytes and checksum and does not extract or execute the archive. It is intentionally unusable until real archive metadata is supplied. Keep signed URLs and credentials out of Git. Inspect archive members before extraction; restore into a new staging directory rather than over an existing experiment.

| Subject | Fixture origin / entry point | What must be preserved |
|---|---|---|
| Java/TPC-C | `scripts/tpcc_baseline.py`, pinned BenchBase loader; app scenarios in `app/tpcc/scenarios/` | All nine tables, fixed initial snapshot and transaction schedule; legacy runners use an archived `initial.dump` |
| alf.io | `prototype/alfio/AlfioBaselineProbe.java`, `scripts/alfio_baseline.py` | Full migrated schema, reservation lifecycle state, fixed replay entropy where specified |
| Oscar | `prototype/oscar/baseline_probe.py`, performance/replay probes | Full application fixture, orders and stock effects, fixed clock/seed where declared |
| beets | `scripts/test_beets_feasibility.py` | Generated libraries of 100/1,000/5,000 tracks, five tables, valid WAV files and metadata; no personal music required |
| Fossil | `stream(n)` in `scripts/test_fossil_feasibility.py` | Deterministic repository history, Unicode/messages/content, all tables/artifacts and fallback cases |

These entry points are pointers into the historical workflow, not independent ready-to-run bootstrap commands. Several also require prior evidence, recovered code, or dependency manifests. First extract and document their prerequisite chain. Regeneration may produce different bytes due to runtime/path-dependent values: record the new fixture and deployment identities, rerun functional validation, and retain the old batch separately.

### Step C — remove environment assumptions without changing the algorithm

Work on a `codex/server-port` branch. Centralize configuration in a small runtime adapter; retain the historical frozen scripts for comparison. Audit at least:

```sh
rg -n 'desktop-linux|/opt/homebrew|/Users/|/private/tmp|Contents/Home' scripts prototype app
rg -n 'evidence/|work/' scripts/benchmark* scripts/measure* scripts/test_fossil*
```

Known blockers: `scripts/app_db_smoke.py` hardcodes a Homebrew JDK and `desktop-linux`; alf.io uses a macOS Temurin path; Oscar depends on an archive under `/private/tmp`; beets expects prior source-tree and fixture evidence; native Fossil requires rebuilt objects and recovered-function artifacts. The existing image lock records ARM64 image IDs. The master runner needs a frozen `protocol.json` and existing evidence parent directories.

Use configurable tool paths, Docker context, input/evidence roots, and explicit project manifests. Resolve dependency paths before launching expensive runs and list missing prerequisites. **Do not bypass hash checks, weaken semantic gates, or rewrite old evidence manifests merely to make a new machine pass.** New binaries and paths require newly bound contexts and a new audit trail. Test portability changes separately from optimizer changes.

### Step D — server acceptance and stable measurement

1. Rebuild and validate original programs first. Compare complete output/state and declared errors, ordering, and effects. For native recovery, include original → recovered-but-unoptimized → optimized to separate recovery changes from transformation gains.
2. Validate candidates and full data preservation before timing. Failing/unknown candidates remain experimental and cannot become framework successes.
3. Run an A/A control using identical code and input. Define a noise/drift acceptance criterion and fixed repetition budget before examining optimization results. If it fails, diagnose the environment; do not keep rerunning until a favorable batch appears.
4. Freeze seeds, request order, dataset snapshot, warmup policy, cache policy, block count, arm ordering, timing boundary, and resource allocation. Alternate two-arm order or use a balanced four-arm schedule. One benchmark job at a time; profiling/instrumented runs are separate from timing.
5. Keep correctness, tuning/selection costs, and final test costs separate. Fresh assessment blocks must not also choose the winning plan. Use baseline/full reconstruction, deletion only, fusion only, and composition when applicable. Report unavailable arms explicitly.
6. Preserve raw per-block records, failed runs, complete logs, manifests, and machine metadata outside GitHub. Rejected batches stay visible with a reason; never silently drop outliers.

**Server milestone:** all five originals and their currently admitted plans run on the server; full-state audits pass; A/A control meets the predefined criterion; a clean checkout plus external input manifests can reproduce one complete run without the original laptop. Only then begin the new-project study.

## 3. Second priority: expand to ten projects

Use five existing **development** projects and five prospectively selected **transfer** projects. Ten variants/workloads of the same application do not count as ten projects. No transfer claim is supported yet.

The following is an ordered candidate pool, not a claim of implemented adapters. Upstream repository existence was checked for this handoff; runtime/database support and exact workload feasibility still need inspection.

| Slot | Proposed repository | Proposed capability to screen |
|---|---|---|
| 6 | [Calibre](https://github.com/kovidgoyal/calibre) | Library metadata listing/export with synthetic documents |
| 7 | [Trac](https://github.com/edgewall/trac) | Ticket query/report plus a small update workflow |
| 8 | [Gitea](https://github.com/go-gitea/gitea) | Repository/issue listing with permission checks |
| 9 | [Kanboard](https://github.com/kanboard/kanboard) | Task-board listing and task transitions |
| 10 | [GnuCash](https://github.com/Gnucash/gnucash) | Account/report calculation over synthetic transactions |

Confirm a suitable database-backed path and noninteractive replay before admission to the study; do not assume all use the same backend or that every language frontend already exists. Pin the exact source commit, dependencies, license, workload contract, and fixture generator before optimization. Do not download live user records or real financial data.

### One protocol for every project

1. Before screening, freeze the shared core, feature extractor/scorer, thresholds, candidate budget, and maximum onboarding effort. Record prior exposure: a project already used to tune the method is not held-out.
2. Screen eligibility without optimized timings: obtainable source/binary, reproducible original, database-backed capability, complete synthetic fixture, inspectable effect boundary, and feasible recovery adapter. Keep every exclusion and its reason. Do not replace a project because its optimization fails or is slow.
3. Recover a dependency graph with value, control, and effect edges. Preserve unresolved edges. Collapse strongly connected components before showing a DAG; recursion is not literally acyclic. Specify module granularity by coherent capability/effect boundary, not arbitrary lines or observed records.
4. Profile the recovered baseline once under a predeclared budget. Identify costly support work and required consumers. Low coverage alone is never removal authority. Profile the cost of the region that could actually be removed, not total cost of a required module.
5. Propose deletion/fusion/composition through the existing APIs; run the unchanged admission and selection method. New language extractors are allowed as documented adapters, not project-specific scoring rules. Record manual review time and unsupported features.
6. Validate against the original with full-data comparison and boundary cases. Keep errors, callbacks, identity, ordering, concurrency and transaction obligations in scope whenever the capability needs them. Separate tested agreement from proved/checked premises.
7. Evaluate on independent assessment blocks after selection. Report original fallback, unknown obligations, rejected candidates, slowdowns, and absence of a useful hotspot. They are valid results, not reasons to retune per project.

If inspecting a new project causes a change to the core/model/policy, label that project development-used, refreeze, and recruit a new prospectively held-out subject if a five-project transfer claim is still desired. Document the change and preserve the previous result. Do not hide selection attrition to reach ten favorable cases.

### Required result record and ablations

For each subject record: source/runtime identity; contract and timing boundary; recovery method and limitations; full data size; candidate modules/regions and unknowns; admission verdicts/reasons; selected arm; independent paired timings; analysis/recovery overhead; and audit status. Distinguish CPU, wall time, memory, startup, and steady state rather than merging them into one score.

Run the same conceptual ablations across supported subjects: original, recovered unoptimized, deletion only, fusion only, both, and adaptive choice. Mark unsupported combinations instead of inventing implementations. For probabilistic prioritization, compare frozen features versus predeclared feature-group removals on appropriate labeled/held-out evaluation; report calibration and abstention, not just accuracy. A safety-gate-disabled variant is a diagnostic correctness experiment, not an eligible performance competitor.

Keep **Fossil as the initial module case study**: original / SQL-side only / C-side only / both, exact output and state comparisons, callback/query counts, paired timings, and interaction uncertainty. Add a second module study only if it answers a different mechanism question. Do not treat several correlated measurements from one project as several independent projects.

**Ten-project milestone:** a complete subject ledger with all outcomes, five genuinely held-out evaluations if feasible, frozen shared-method hashes, independent assessment costs, full correctness/data audits, and no project-specific threshold changes.

## 4. Third priority: explain results with better figures

Tell the mechanism before the implementation detail: a required capability induces obligations; recovered dependencies identify removable or fusible support; validation controls legality; measurements choose among legal alternatives; uncertainty falls back to original.

| Figure | What it must explain | Data and cautions |
|---|---|---|
| Framework diagram | Four public APIs, shared gates, two transformation families, original fallback | Project-neutral labels; show where humans/recovery adapters supply premises |
| Before/after dependency example | One required output, control/effect edges, retained unknowns, removed support, fused region | Annotate edges and legend; demonstrate full data retained; a condensed DAG may contain SCC nodes |
| Ten-project coverage matrix | Recovered → proposed → admitted → measured → selected, plus unknown/retain reasons | Include every project and failures; do not call unsupported projects a speedup |
| Paired speedup plot | Variation and uncertainty across original/candidate pairs | Log axis, neutral line at 1; distinguish admitted, experimental, and A/A; separate batches and boundaries |
| Module factorial | Attribution to SQL-side, C-side, and joint removal | Four arms, paired points/intervals, counts of operations removed; interaction needs its own interval |
| Overhead/scaling plot | When analysis/recovery pays off and where benefit saturates | Only after collecting these measurements; no extrapolated break-even from unrelated workloads |

`scripts/plot_handoff_performance.py` provides a generic paired-data plot and summary export. Install Matplotlib in a separate plotting environment and record the resolved version. It consumes a normalized CSV created from audited raw records, not from rounded paper tables:

```text
project,workload,candidate,status,deployment,batch,split,block,original_ms,candidate_ms
```

Allowed `status`: `admitted`, `experimental`, `aa`. Allowed `split`: `selection`, `assessment`. Only assessment rows are plotted. Each row is one measured original/candidate pair; exclude warmups during the audited conversion and retain their raw logs. Run:

```sh
python3 scripts/plot_handoff_performance.py data/paired-assessment.csv --out output/figures
```

The script reports geometric mean paired speedup and a descriptive paired-bootstrap 95% interval (4,000 resamples, seed 1729). It is a plotting summary, not the selection policy, an admission check, or a correction for ten-project multiple comparisons. It also writes input hashes and plotting settings. Never attach these intervals to ratios of medians from the current paper. Use vector PDF/SVG for paper figures, legible text at final print size, accessible colors plus shapes, and captions specifying units, n, estimand, interval method, and timing boundary. Do not generate scientific charts with an image model.

**Visualization milestone:** every figure is regenerated from one declared input manifest, every point traces to an audited paired record, and the caption makes the strength and limits of the claim clear.

## 5. Practical work order and reporting

1. Read this guide, API documentation, and the manuscript. Run source-only checks. Produce a missing-input inventory.
2. Obtain/download or regenerate inputs; port one original end-to-end (Fossil is a compact starting case), then the other four. Freeze a new server context and validate recovery before optimization.
3. Complete fixed server baseline/A/A/candidate experiments for the existing five. Keep all prior local results as historical batches.
4. Freeze the prospective transfer protocol and candidate list. Onboard the five new subjects sequentially; discuss semantic difficulty before changing the core.
5. Run the common ablations, independent assessment, and Fossil module analysis. Generate the figure set and update the manuscript with measured claims only.

Each milestone report should contain: question; immutable inputs; exact command; pass/fail/unknown; evidence location; functional and full-state checks; performance with its timing boundary; remaining uncertainty; and the single next decision. Avoid an open-ended optimization/retesting loop: preregister the budget, stop at it, and preserve negative outcomes.

The GitHub repository contains research source and paper source, **not** datasets, label/sample cohorts, upstream checkouts, compiled binaries, raw logs, historical evidence, personal writing notes, or credentials. Aggregate paper tables, frozen model parameters, and small scenario/configuration files remain included. Label cohorts needed to repeat model calibration must also be obtained through the external archive. `scripts/export_source_handoff.py` defines the allowlist and emits a checksum inventory for review before upload. Do not use a broad `git add .` on the original research workspace.

The supervisor still needs to supply server access and an external evidence/data download location if exact historical replay is required. The student should not wait for the old archives to read the code or run the source-only checks, but must not label those checks full experimental replication.
