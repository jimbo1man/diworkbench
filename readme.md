# DI Workbench v3

A governed AI-assisted specification pipeline that enforces structured workflows across Architect, Reviewer, and Implementer roles. Built to formalize decision intelligence around spec development, ensuring contracts are honored, revisions stay in scope, and human escalation happens when it should.

## What it does

DI Workbench manages the lifecycle of a specification from draft to implementation-ready. It automates the governance layer between AI-generated specs and AI-generated reviews, enforcing contracts and catching overreach before anything reaches an implementer.

### The pipeline

```
DRAFT → ARCHITECTED → UNDER_REVIEW → DECISION → APPROVED → IMPLEMENTING → COMPLETE
                           │
                           ├── REVISE_ALLOWED → auto-route back to Architect (capped loops)
                           ├── REVISE_FORBIDDEN → rejected, reviewer must resubmit
                           └── BLOCKED → human escalation required
```

### Key design decisions

- **The Decision Engine ignores the reviewer's self-declared verdict.** All routing is derived from diff analysis against the contract. This prevents a reviewer from approving a spec that violates locked fields.
- **Contracts lock schema and field definitions.** The Architect defines canonical structures; the Reviewer can flag issues but cannot rename, remove, or restructure locked fields.
- **Revision loops are capped** (default: 2) to prevent infinite Architect ↔ Reviewer cycling. When loops exhaust, the system escalates to human arbitration.
- **Forbidden vs. Allowed scope classification** — every reviewer issue is classified as within or outside the reviewer's authority. Forbidden-scope changes are automatically rejected.

## Architecture

```
di_workbench_v3/
├── app.py                    # Streamlit UI — interactive 4-pane workbench
├── server.py                 # FastAPI backend — API-driven access
├── engine/                   # Core pipeline logic
│   ├── decision_engine.py    # Workflow controller — routes based on diff analysis
│   ├── contract_validator.py # Validates contracts and review schema
│   ├── diff_guard.py         # Classifies changes as allowed/forbidden/blocker
│   ├── workflow_state.py     # State machine definition and transitions
│   ├── workflow_transitions.py
│   ├── run_record.py         # Run data model and persistence structure
│   ├── run_store.py          # Run persistence and retrieval
│   ├── run_scoring.py        # Spec quality scoring
│   ├── run_comparison.py     # Cross-run diff and comparison
│   ├── run_insights.py       # Computed insights from run data
│   ├── issue_resolution.py   # Issue tracking and resolution logic
│   ├── review_normalization.py
│   ├── status_adapter.py
│   ├── artifact_store.py     # Artifact persistence layer
│   ├── backlog_manager.py    # Project backlog tracking
│   ├── body_v1_scoring.py    # Domain-specific: health scoring engine
│   ├── body_v1_validator.py  # Domain-specific: health data validation
│   └── body_v1_insights.py   # Domain-specific: health insights
├── prompts/                  # Role-specific system prompts
│   ├── architect.txt         # Architect: spec generation authority
│   ├── reviewer.txt          # Reviewer: structured JSON review output
│   └── implementer.txt       # Implementer: execution guidance
├── contracts/                # Versioned contract definitions
├── specs/                    # Specification documents
├── reviews/                  # Review output artifacts
├── diffs/                    # Diff analysis artifacts
├── handoffs/                 # Implementation handoff documents
├── runs/                     # Persisted run history (JSON)
├── backlog/                  # Project backlog
├── workflow/                 # Workflow state artifacts
└── data/                     # Working data directories
```

## Workflow states

| State | Description |
|-------|-------------|
| `DRAFT` | Initial state — spec not yet generated |
| `ARCHITECTED` | Architect has produced a spec |
| `UNDER_REVIEW` | Reviewer is evaluating the spec |
| `REVIEW_REVISE_ALLOWED` | Reviewer flagged allowed-scope issues — auto-routes to Architect |
| `REVIEW_REVISE_FORBIDDEN` | Reviewer attempted forbidden changes — rejected |
| `APPROVED` | Spec passed review — ready for implementation |
| `IMPLEMENTING` | Implementation in progress |
| `BLOCKED` | Human escalation required |
| `COMPLETE` | Implementation finished |

## Roles

### Architect
Full authority over output schema, module boundaries, contracts, and sequencing. Produces complete, implementable specifications. Cannot be overridden by the Reviewer.

### Reviewer
Pressure-tests specs for implementability. May flag missing definitions, ambiguity, edge cases, and implementation risk. Must return structured JSON. Cannot rename locked fields, collapse layers, or introduce new scope.

### Decision Engine
The system authority. Validates review schema, validates contracts, runs diff guard, and determines the next workflow state. The reviewer's declared decision is explicitly ignored — all routing is derived from classified diffs.

## AI model roles

| Role | Model | API |
|------|-------|-----|
| Architect | ChatGPT (OpenAI) | OpenAI API |
| Reviewer | Claude (Anthropic) | Anthropic API |
| Decision Engine | Deterministic — no LLM | Local logic |

The Architect and Reviewer are deliberately different models. This creates genuine adversarial tension in the review process rather than one model reviewing its own output.

## Setup

### Requirements

- Python 3.12+
- OpenAI API key (for Architect / ChatGPT)
- Anthropic API key (for Reviewer / Claude)

### Install

```bash
pip install -r requirements.txt
```

### Run (Streamlit UI)

```bash
streamlit run app.py --server.port 8501
```

### Run (FastAPI backend)

```bash
uvicorn server:app --host 0.0.0.0 --port 8000
```

### Environment

Create a `.env` file in the project root:

```
OPENAI_API_KEY=your_openai_key_here
ANTHROPIC_API_KEY=your_anthropic_key_here
```

## Run history

Each pipeline execution is persisted as a timestamped JSON file in `runs/`. Runs capture the full state — spec, contract, review, decision, scores, and insights — enabling cross-run comparison and trend analysis.

## Status

Active development. Built as internal tooling for the Lucy ecosystem.
