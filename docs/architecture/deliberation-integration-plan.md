# Deliberation Module — Integration & Porting Plan

> **Status**: Draft  
> **Author**: Architecture review  
> **Created**: 2025-07-17  
> **Source material**: `stockmarketapp` (Index Mavens) agent infrastructure  
> **Target**: `Missioncontrol-projectdigitalproductsagents` (OpenClaw Mission Control)

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Source Inventory — What We Are Porting](#2-source-inventory--what-we-are-porting)
3. [Target Inventory — What Already Exists in MC](#3-target-inventory--what-already-exists-in-mc)
4. [Key Architectural Transforms](#4-key-architectural-transforms)
5. [Phase 0 — Infrastructure Prerequisites](#5-phase-0--infrastructure-prerequisites)
6. [Phase 1 — Memory Infrastructure](#6-phase-1--memory-infrastructure)
7. [Phase 2 — Deliberation Engine](#7-phase-2--deliberation-engine)
8. [Phase 3 — Message Bus & Real-Time](#8-phase-3--message-bus--real-time)
9. [Phase 4 — Episodic Memory & Learning](#9-phase-4--episodic-memory--learning)
10. [Phase 5 — API Layer](#10-phase-5--api-layer)
11. [Phase 6 — Frontend](#11-phase-6--frontend)
12. [Phase 7 — Integration Wiring](#12-phase-7--integration-wiring)
13. [Database Migration Strategy](#13-database-migration-strategy)
14. [File Manifest](#14-file-manifest)
15. [Risk Register](#15-risk-register)
16. [Testing Strategy](#16-testing-strategy)
17. [Rollout Sequence](#17-rollout-sequence)
18. [Appendix A — Model Field Mapping](#appendix-a--model-field-mapping)
19. [Appendix B — Source ↔ Target File Map](#appendix-b--source--target-file-map)

---

## 1. Executive Summary

### What we are building

A **Deliberation Module** for Mission Control that enables agents to:

- **Debate** — propose a thesis and challenge it with counter-arguments
- **Discuss** — exchange evidence, rebuttals, and supporting data
- **Verify** — fact-check claims, vote on positions, rank arguments
- **Synthesize** — produce a consensus finding with captured dissent
- **Remember** — persist synthesized findings as durable, searchable memory
- **Recall** — retrieve relevant past deliberations via semantic search

### Why we are porting from Index Mavens (not building from scratch)

The `stockmarketapp` project contains a **production-grade implementation** of:

- A 3-tier memory system (Working → Short-Term → Long-Term with pgvector)
- A structured inter-agent debate engine with divergence detection
- A Redis Streams message bus for async inter-agent communication
- An episodic memory system that learns patterns from past decisions
- Rich frontend components for debate visualization and decision history

These systems were built for trading agents but are **domain-agnostic at the
infrastructure level**. Porting saves an estimated 3–4 weeks of design and
implementation versus building from zero.

### Where it lives

This module lives entirely in **Mission Control** (not OpenClaw core). MC owns
orchestration, memory persistence, governance, and agent coordination. OpenClaw
agents participate in deliberations but do not own the deliberation state.

---

## 2. Source Inventory — What We Are Porting

### From `stockmarketapp/apps/python-backend/src/`

| Source File | Lines | What It Does | Adaptation Needed |
|---|---|---|---|
| `agents/memory/working_memory.py` | ~400 | Redis ephemeral state: per-user agent state, pipeline context, TTL-based keys | Re-key from `user_id` to `board_id`; align with MC's Redis config |
| `agents/memory/short_term.py` | ~280 | Postgres JSONB message store: pipeline traces, agent context windows, retention pruning | Replace raw SQLAlchemy with SQLModel; add board scoping |
| `agents/memory/long_term.py` | ~500 | Postgres + pgvector: knowledge base with embeddings, decision log, behavioral profiles, semantic search | Port semantic search; replace decision log with deliberation synthesis promotion |
| `agents/memory/message_bus.py` | ~400 | Redis Streams: publish/subscribe, consumer groups, correlation IDs, message envelopes | Integrate with `GatewayDispatchService` as delivery backend |
| `agents/memory/__init__.py` | ~30 | Module facade with singleton exports | Adapt to MC service patterns |
| `agents/debate.py` | ~600 | Debate engine: divergence detection, challenge/rebuttal/mediator protocol, rule-based fallback | Generalize from trading signals to arbitrary agent positions |
| `models/agent_models.py` (partial) | ~500 | `AgentMessage`, `DecisionLog`, `KnowledgeBase`, `EpisodicMemory`, `DebateChain`, `PipelineOutcome` | Translate to SQLModel; board-scope all tables |
| `services/agent_lifecycle.py` (reference) | ~300 | Heartbeat tracking, stale detection | Reference only; MC has its own lifecycle system |
| `services/activity_log.py` (reference) | ~50 | Event recording helper | MC already has `record_activity()`; no port needed |

### From `stockmarketapp/apps/nextjs/app/dashboard/`

| Source File | Lines | What It Does | Adaptation Needed |
|---|---|---|---|
| `agents/components/AgentDebatesView.tsx` | ~600 | Debate list with filters, stats bar, debate cards, expand/collapse | Restyle for MC design system; replace trading domain types |
| `agents/components/DecisionHistory.tsx` | ~700 | Decision timeline with accuracy tracking, agent input display, action buttons | Adapt to deliberation synthesis history |
| `agents/components/RecommendationCard.tsx` | ~800 | Detailed recommendation view: confidence bars, agent agreement, risk rules | Adapt to deliberation detail view with consensus visualization |
| `approvals/components/DebateTranscript.tsx` | ~400 | Turn-by-turn debate viewer with agent avatars, role badges, timestamps | Nearly direct port; restyle for MC |

---

## 3. Target Inventory — What Already Exists in MC

### Backend infrastructure we will reuse (not replace)

| MC Component | File | How Deliberation Uses It |
|---|---|---|
| `BoardMemory` model | `models/board_memory.py` | Promoted synthesis findings are stored here; agents recall via existing memory API |
| `BoardGroupMemory` model | `models/board_group_memory.py` | Cross-board deliberation insights promoted here |
| `BoardMemory` API + SSE stream | `api/board_memory.py` | Agents already query/stream board memory; synthesis entries appear seamlessly |
| `GatewayDispatchService` | `services/openclaw/gateway_dispatch.py` | Delivers deliberation notifications to agents via OpenClaw sessions |
| `ActorContext` auth | `api/deps.py` | Both users and agents can participate in deliberations via existing auth |
| `record_activity()` | `services/activity_log.py` | All deliberation events recorded in the activity audit trail |
| `Approval` model | `models/approvals.py` | Deliberation synthesis can trigger approval flows (confidence-gated) |
| `lead_policy` | `services/lead_policy.py` | `approval_required()` and `compute_confidence()` apply to synthesis decisions |
| `QueryModel` + `ModelManager` | `models/base.py`, `db/query_manager.py` | All new models inherit from `QueryModel` for `.objects` query API |
| `paginate()` | `db/pagination.py` | Pagination for list endpoints |
| Redis (compose.yml) | `compose.yml` | Already in the stack; used for RQ workers today, will add Streams |
| Alembic migrations | `migrations/versions/` | Standard migration workflow; auto-applied in dev |

### Frontend infrastructure we will reuse

| MC Component | Location | Relevance |
|---|---|---|
| Generated API client | `frontend/src/api/generated/` | Regenerated via `make api-gen` after new endpoints are added |
| Board detail page | `frontend/src/app/boards/[boardId]/` | Deliberation tab added as sub-route: `/boards/[boardId]/deliberations` |
| `BoardChatComposer.tsx` | `frontend/src/components/` | Pattern reference for real-time message composition in deliberation |
| `BoardApprovalsPanel.tsx` | `frontend/src/components/` | Pattern reference for action buttons and status badges |
| Existing component atoms | `frontend/src/components/atoms/` | Buttons, badges, inputs, cards used by deliberation UI |

---

## 4. Key Architectural Transforms

Every source file must go through these fundamental transformations:

### T1: Tenancy model — User-scoped → Board-scoped

```
Index Mavens                          Mission Control
──────────────────────────────────────────────────────
user_id: str (Clerk user ID)    →     board_id: UUID (FK → boards.id)
agent_name: str (hardcoded)     →     agent_id: UUID (FK → agents.id)
pipeline_id: UUID               →     deliberation_id: UUID
```

All database queries, Redis keys, and API access checks must be scoped to
a `board_id`. Agents can only access deliberations on their assigned board.
Org admins can access deliberations on boards they have access to.

### T2: ORM — Raw SQLAlchemy → SQLModel

```python
# Index Mavens (raw SQLAlchemy)
class DebateChain(Base):
    __tablename__ = "debate_chains"
    id = Column(UUID(as_uuid=True), primary_key=True, ...)
    user_id = Column(String(100), nullable=False, index=True)

# Mission Control (SQLModel + QueryModel)
class Deliberation(QueryModel, table=True):
    __tablename__ = "deliberations"
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    board_id: UUID = Field(foreign_key="boards.id", index=True)
```

All models inherit `QueryModel` to get the `.objects` descriptor. JSON columns
use `Field(sa_column=Column(JSON))`. Timestamps use `utcnow` from `app.core.time`.

### T3: Domain — Trading signals → Generic agent positions

```
Index Mavens                          Mission Control
──────────────────────────────────────────────────────
signal: "BUY"/"SELL"/"HOLD"     →     position: str (free-form thesis)
confidence: int (0-100)         →     confidence: float (0.0-1.0)
symbol: "RELIANCE"              →     topic: str (deliberation subject)
OPPOSING_SIGNALS set            →     configurable divergence policies
agent_inputs dict               →     entry references (task IDs, memory IDs)
```

The debate engine becomes domain-agnostic. Instead of detecting divergence
between BUY vs SELL signals, it detects divergence between any two agent
positions that are semantically opposed or have a confidence gap exceeding
a board-configurable threshold.

### T4: Agent communication — Direct Redis → Gateway dispatch

```
Index Mavens                          Mission Control
──────────────────────────────────────────────────────
message_bus.publish()           →     GatewayDispatchService.try_send_agent_message()
Redis Streams consumer groups   →     Agents poll via API or receive via gateway push
WebSocket / SSE (direct)        →     SSE streaming (MC already uses this pattern)
```

MC agents run inside OpenClaw sessions on gateways. They do not connect directly
to Redis. The message bus is used server-side for internal event coordination;
agent-facing delivery goes through the gateway dispatch layer.

### T5: Auth context — Clerk JWT → MC ActorContext

```
Index Mavens                          Mission Control
──────────────────────────────────────────────────────
@require_auth (Clerk JWT)       →     require_admin_or_agent (ActorContext)
user_id from Clerk token        →     ActorContext.user or ActorContext.agent
No agent-as-caller support      →     Agents are first-class API callers
```

MC's `ActorContext` supports both human users and agents as callers. This
is strictly better than IM's user-only auth model and requires no adaptation
on the MC side — we just use existing dependency injection.

---

## 5. Phase 0 — Infrastructure Prerequisites

> **Goal**: Prepare the foundation without touching application code.  
> **Estimated effort**: 1 day  
> **Dependencies**: None  
> **Risk**: Low

### 0.1 Add pgvector extension to Postgres

MC uses `postgres:16-alpine`. pgvector requires either switching the image or
installing the extension at startup.

**Option A** (recommended): Switch to `pgvector/pgvector:pg16` in `compose.yml`.
This is a drop-in replacement that includes the pgvector extension pre-installed.

**Option B**: Add an init script that runs `CREATE EXTENSION IF NOT EXISTS vector;`
at container startup.

Changes:

| File | Change |
|---|---|
| `compose.yml` | Change `image: postgres:16-alpine` → `image: pgvector/pgvector:pg16` |
| `.env.example` | Document `POSTGRES_IMAGE` override (optional) |

### 0.2 Add Python dependencies

| Package | Purpose | Version |
|---|---|---|
| `pgvector` | SQLAlchemy pgvector column type | `>=0.3.0` |
| `sentence-transformers` | Generate embeddings for semantic search | `>=2.7.0` |

> **Note**: `sentence-transformers` pulls PyTorch, which is large (~2GB).
> Consider a lighter alternative for the Docker image:
>
> - **Option A**: Use `sentence-transformers` with CPU-only torch
> - **Option B**: Use OpenAI/Anthropic embeddings API (no local model)
> - **Option C**: Use `fastembed` (~50MB, ONNX-based, no torch)
>
> **Recommendation**: Start with **Option C** (`fastembed`) for the Docker
> build. Support **Option B** as a configurable alternative for teams that
> already have an OpenAI API key. Defer `sentence-transformers` to an
> optional "full ML" profile.

Changes:

| File | Change |
|---|---|
| `backend/pyproject.toml` | Add `pgvector`, `fastembed` (or chosen embedding lib) |
| `backend/Dockerfile` | No change if using `fastembed`; add torch layer if using sentence-transformers |

### 0.3 Verify Redis Streams support

MC already runs `redis:7-alpine` in `compose.yml`. Redis 7 fully supports
Streams, consumer groups, and `XREADGROUP`. No image change needed.

Verify:

```bash
docker compose exec redis redis-cli XINFO HELP
```

### 0.4 Configuration additions

| Setting | Default | Purpose |
|---|---|---|
| `EMBEDDING_PROVIDER` | `fastembed` | `fastembed`, `openai`, or `none` |
| `EMBEDDING_MODEL` | `BAAI/bge-small-en-v1.5` | Model name for chosen provider |
| `EMBEDDING_DIM` | `384` | Embedding vector dimension |
| `OPENAI_API_KEY` | `` | Required only if `EMBEDDING_PROVIDER=openai` |
| `DELIBERATION_DIVERGENCE_THRESHOLD` | `0.3` | Confidence gap that triggers auto-debate |
| `DELIBERATION_MAX_TURNS` | `6` | Maximum debate turns before forced synthesis |
| `DELIBERATION_ENTRY_RETENTION_DAYS` | `90` | Prune old entries after this window |
| `MEMORY_WORKING_TTL_DEFAULT` | `600` | Default working memory TTL (seconds) |
| `MEMORY_STREAM_MAXLEN` | `10000` | Max entries per Redis Stream |

Changes:

| File | Change |
|---|---|
| `backend/app/core/config.py` | Add new settings to `Settings` class |
| `backend/.env.example` | Document new variables |
| `.env.example` | Document new variables at root level |

---

## 6. Phase 1 — Memory Infrastructure

> **Goal**: Port the 3-tier memory system, adapted for board-scoped tenancy.  
> **Estimated effort**: 3–4 days  
> **Dependencies**: Phase 0 complete  
> **Risk**: Medium (pgvector integration is the main unknown)

### 1.1 Working Memory (Redis ephemeral layer)

**Source**: `stockmarketapp/.../agents/memory/working_memory.py`  
**Target**: `backend/app/services/memory/working_memory.py`

Key schema transform:

```
IM:  agent:wm:{user_id}:{namespace}:{key}
MC:  mc:wm:{board_id}:{agent_id}:{key}

IM:  agent:wm:{user_id}:ctx:{pipeline_id}
MC:  mc:wm:{board_id}:delib:{deliberation_id}

IM:  agent:wm:{user_id}:state:{agent_name}
MC:  mc:wm:{board_id}:state:{agent_id}
```

What to port:

- `WorkingMemory` class → rename to `WorkingMemoryService`
- `connect()` / `close()` → use `aioredis.from_url(settings.rq_redis_url)`
  (MC already has Redis URL in settings via `rq_redis_url`)
- `get()` / `set()` / `delete()` → change key format, keep TTL logic
- `set_context()` / `get_context()` / `update_context()` → rename to
  `set_deliberation_context()` etc., scope by `board_id` + `deliberation_id`
- `set_agent_state()` / `get_agent_state()` → scope by `board_id` + `agent_id`
- `clear_user()` → `clear_board()` (clear all WM keys for a board)
- **Drop**: `set_price()`, `get_price()`, `set_pending_recommendation()`,
  `get_pending_recommendation()` — trading-specific methods

What to add:

- `set_deliberation_scratch(board_id, delib_id, agent_id, data)` — per-agent
  scratch space within a deliberation (for drafting arguments, gathering evidence)
- Lazy connection initialization (connect on first access, not at import)

Singleton pattern: Follow MC convention — instantiate in the module, import
where needed. Do **not** register as a FastAPI dependency (Redis is infra,
not per-request).

### 1.2 Short-Term Memory (Postgres JSONB message layer)

**Source**: `stockmarketapp/.../agents/memory/short_term.py`  
**Target**: `backend/app/services/memory/short_term.py`

This layer maps to `AgentMessage` in IM. In MC, we split this into two
purposes:

**Purpose A: Deliberation entries** — these get their own model
(`DeliberationEntry`) because they have structured fields (phase, entry_type,
confidence, parent_entry_id). This is built in Phase 2.

**Purpose B: General agent message trace** — for audit and replay of
inter-agent communication. This maps to a new `AgentMessage` model.

Model translation:

```
IM AgentMessage                       MC AgentMessage
──────────────────────────────────────────────────────
id: UUID                        →     id: UUID
user_id: String(100)            →     board_id: UUID (FK → boards.id)
agent_name: String(50)          →     agent_id: UUID (FK → agents.id)
message_type: String(50)        →     message_type: str
payload: JSONB                  →     payload: dict (JSON column)
parent_message_id: UUID (FK)    →     parent_message_id: UUID | None
correlation_id: UUID            →     correlation_id: UUID (index)
created_at: DateTime            →     created_at: datetime
```

Port `ShortTermMemory` class methods:

- `store_message()` → adapt to use MC async session (`get_session`)
- `get_recent_messages()` → add `board_id` filter; use `QueryModel.objects`
- `get_pipeline_trace()` → rename to `get_correlation_trace(correlation_id)`
- `get_agent_context()` → `get_agent_context(board_id, agent_id, hours)`
- `get_message_counts()` → `get_message_counts(board_id, hours)`
- `prune()` → keep retention policy; add board-scoped variant

**Important**: Use MC's `async_session_maker` and session patterns, not
IM's `AsyncSessionLocal()`. MC sessions are injected via FastAPI dependency
or created via `async with async_session_maker() as session:`.

### 1.3 Long-Term Memory (Postgres + pgvector semantic layer)

**Source**: `stockmarketapp/.../agents/memory/long_term.py`  
**Target**: `backend/app/services/memory/long_term.py`

This is the most complex port. IM has three sub-concerns in `LongTermMemory`:

| IM Sub-concern | MC Equivalent |
|---|---|
| Decision Log | `DeliberationSynthesis` model (Phase 2) — promoted to `BoardMemory` |
| Knowledge Base (pgvector) | New `embedding` column on `BoardMemory` + search service |
| User Behavioral Profile | Not applicable (MC doesn't track individual user behavior) |

What to port:

- **Embedding infrastructure**: Port the lazy-loading embedding model pattern.
  Abstract behind an `EmbeddingService` interface:

  ```python
  class EmbeddingService(Protocol):
      async def embed(self, text: str) -> list[float] | None: ...
      @property
      def dimension(self) -> int: ...
  ```

  Implementations: `FastEmbedService`, `OpenAIEmbedService`, `NoopEmbedService`.

- **Knowledge search**: Port `search_knowledge()` → `semantic_search()`.
  In MC, this searches `BoardMemory` entries that have embeddings:

  ```sql
  SELECT *, embedding <=> $query_vector AS distance
  FROM board_memory
  WHERE board_id = $board_id
    AND embedding IS NOT NULL
  ORDER BY distance ASC
  LIMIT $limit;
  ```

- **Drop**: `log_decision()`, `update_decision_action()`,
  `update_decision_outcome()`, `get_decision_accuracy()` — these are
  replaced by the `DeliberationSynthesis` model and dedicated service.

- **Drop**: `UserBehavioralProfile` — not applicable.

Schema change to `BoardMemory`:

```python
# Add to existing BoardMemory model
embedding: list[float] | None = Field(
    default=None,
    sa_column=Column(Vector(384), nullable=True),
)
```

This requires an Alembic migration to add the column and a pgvector index.

### 1.4 Memory module facade

**Target**: `backend/app/services/memory/__init__.py`

```python
"""Agent memory subsystem for Mission Control.

Three layers:
1. Working Memory (Redis) — ephemeral state during active deliberations
2. Short-Term Memory (Postgres) — agent messages, traces, 90-day retention
3. Long-Term Memory (Postgres + pgvector) — semantic search over board memory
"""

from app.services.memory.working_memory import WorkingMemoryService, working_memory
from app.services.memory.short_term import ShortTermMemoryService, short_term_memory
from app.services.memory.long_term import LongTermMemoryService, long_term_memory
```

---

## 7. Phase 2 — Deliberation Engine

> **Goal**: Port and generalize the debate engine with structured models.  
> **Estimated effort**: 4–5 days  
> **Dependencies**: Phase 1 complete (specifically: models registered, migration run)  
> **Risk**: Medium (domain generalization is the creative challenge)

### 2.1 Data models

**Target**: `backend/app/models/deliberation.py`

Three new SQLModel tables:

#### `Deliberation` (replaces `debate_chains`)

```
IM debate_chains field            MC Deliberation field
──────────────────────────────────────────────────────────
id                          →     id: UUID (PK)
user_id                     →     board_id: UUID (FK → boards.id, indexed)
pipeline_id                 →     (dropped — deliberation IS the pipeline)
symbol                      →     topic: str (what is being deliberated)
challenger_agent            →     initiated_by_agent_id: UUID | None (FK → agents.id)
challenger_signal/confidence →    (moved to first DeliberationEntry)
defender_agent              →     (moved to first rebuttal entry)
defender_signal/confidence  →     (moved to first rebuttal entry)
mediator_agent              →     synthesizer_agent_id: UUID | None
trigger_reason              →     trigger_reason: str | None
turns (JSONB)               →     (normalized into DeliberationEntry rows)
verdict                     →     (moved to DeliberationSynthesis)
verdict_confidence          →     (moved to DeliberationSynthesis)
verdict_reasoning           →     (moved to DeliberationSynthesis)
status                      →     status: str (debating/discussing/verifying/
                                    synthesizing/concluded/abandoned)
original_signal             →     (dropped — not applicable)
final_signal                →     (dropped — not applicable)
signal_changed              →     outcome_changed: bool (did deliberation alter
                                    the original position?)
confidence_delta            →     confidence_delta: float | None
debate_duration_ms          →     duration_ms: float | None
approval_id                 →     approval_id: UUID | None (FK → approvals.id)
created_at                  →     created_at: datetime
completed_at                →     concluded_at: datetime | None
                            +     task_id: UUID | None (FK → tasks.id) — if
                                    deliberation was triggered by a task
                            +     parent_deliberation_id: UUID | None — for
                                    follow-up deliberations
                            +     max_turns: int (from board config or global default)
                            +     updated_at: datetime
```

Indexes: `(board_id)`, `(board_id, status)`, `(board_id, created_at DESC)`,
`(task_id)`.

#### `DeliberationEntry` (replaces `turns` JSONB array)

IM stores turns as a JSONB array inside `debate_chains.turns`. This is
denormalized and can't be queried, indexed, or streamed individually. We
normalize into proper rows.

```
IM DebateTurn dict key            MC DeliberationEntry field
──────────────────────────────────────────────────────────
turn (int)                  →     sequence: int (ordering within deliberation)
role ("challenger"/          →     phase: str (debate/discussion/verification)
  "rebuttal"/"mediator")         + entry_type: str (thesis/antithesis/evidence/
                                    question/vote/rebuttal/synthesis)
agent (str name)            →     agent_id: UUID | None (FK → agents.id)
                            +     user_id: UUID | None (FK → users.id) — humans
                                    can contribute too
signal (str)                →     position: str | None (the agent's stance,
                                    free-form; optional for evidence/questions)
confidence (float)          →     confidence: float | None (0.0–1.0)
argument (str)              →     content: str (NonEmptyStr for writes)
timestamp (str)             →     created_at: datetime
                            +     id: UUID (PK)
                            +     deliberation_id: UUID (FK → deliberations.id)
                            +     parent_entry_id: UUID | None (FK self-ref
                                    for threaded replies)
                            +     references: list[str] | None (JSON — links to
                                    board memory IDs, task IDs, external URLs)
                            +     metadata: dict | None (JSON — agent-specific
                                    structured data: rubric scores, evidence
                                    quality ratings, etc.)
```

Indexes: `(deliberation_id, sequence)`, `(deliberation_id, phase)`,
`(agent_id, created_at DESC)`.

#### `DeliberationSynthesis` (new — extracted from `verdict` fields)

```
Field                             Purpose
──────────────────────────────────────────────────────────
id: UUID                          PK
deliberation_id: UUID             FK → deliberations.id (unique)
synthesized_by_agent_id: UUID     FK → agents.id | None
content: str                      The synthesized finding (NonEmptyStr)
consensus_level: str              unanimous/majority/contested/split
key_points: list[str] | None      JSON — extracted bullet points
dissenting_views: list[str] | None JSON — captured minority opinions
confidence: float                 Aggregate confidence (0.0–1.0)
tags: list[str] | None            JSON — for categorization and recall
promoted_to_memory: bool          Whether pushed into BoardMemory
board_memory_id: UUID | None      FK → board_memory.id (if promoted)
embedding: Vector(384) | None     pgvector — for semantic search on syntheses
created_at: datetime
```

Indexes: `(deliberation_id)` unique, `(promoted_to_memory)`.

### 2.2 Schema definitions

**Target**: `backend/app/schemas/deliberation.py`

```
DeliberationCreate    — topic, trigger_reason, task_id?, max_turns?
DeliberationRead      — full deliberation with status, timestamps, counts
DeliberationUpdate    — status transitions (advance phase, abandon)
DeliberationEntryCreate  — content, phase, entry_type, position?, confidence?,
                           references?, parent_entry_id?
DeliberationEntryRead    — full entry with agent/user info
DeliberationSynthesisCreate  — content, consensus_level, key_points,
                                dissenting_views, tags
DeliberationSynthesisRead    — full synthesis with promotion status
```

Follow MC schema conventions:

- Inherit from `SQLModel`
- Use `NonEmptyStr` for content fields on create payloads
- Include `model_config = SQLModelConfig(json_schema_extra={...})` with
  `x-llm-intent` annotations (same pattern as `AgentBase`)

### 2.3 Deliberation service

**Source**: `stockmarketapp/.../agents/debate.py` → `DebateEngine`  
**Target**: `backend/app/services/deliberation.py`

Port and generalize:

| IM Method | MC Method | Transform |
|---|---|---|
| `DebateEngine.maybe_run_debate()` | `DeliberationService.maybe_start_deliberation()` | Generalize trigger: instead of checking opposing signals, check if agents have submitted divergent positions on a topic |
| `_extract_positions()` | `_gather_agent_positions()` | Instead of parsing trading-specific dicts, read recent `DeliberationEntry` rows |
| `_detect_divergences()` | `detect_divergences()` | Replace `OPPOSING_SIGNALS` set with configurable semantic divergence detection; keep confidence gap threshold |
| `_run_debate()` | `run_deliberation_round()` | Generalize the challenge → rebuttal → mediator flow into phase transitions |
| `_rule_based_mediator()` | `_fallback_synthesis()` | Keep as fallback when LLM synthesis fails |
| `_gemini_mediator()` | (defer to agent via gateway) | In MC, the synthesizer agent runs on OpenClaw, not directly via API |
| `check_divergences_only()` | `check_divergences()` | Keep as lightweight pre-check |

New methods (not in IM):

| Method | Purpose |
|---|---|
| `advance_phase(deliberation_id)` | Transition `debating` → `discussing` → `verifying` → `synthesizing` |
| `conclude(deliberation_id, synthesis)` | Finalize deliberation, create synthesis |
| `promote_to_memory(synthesis_id)` | Copy synthesis content into `BoardMemory` with tags and embedding |
| `abandon(deliberation_id, reason)` | Mark as abandoned without synthesis |
| `get_deliberation_context(deliberation_id)` | Build context payload for the synthesizer agent (all entries, relevant board memory) |

Phase state machine:

```
                   ┌──────────┐
                   │  CREATED  │
                   └─────┬─────┘
                         │ first entry submitted
                         ▼
                   ┌──────────┐
              ┌───▶│ DEBATING  │◀─── agents submit thesis/antithesis
              │    └─────┬─────┘
              │          │ advance (manual or auto after N turns)
              │          ▼
              │    ┌──────────┐
              │    │DISCUSSING│◀─── agents submit evidence/questions
              │    └─────┬─────┘
              │          │ advance
              │          ▼
              │    ┌──────────┐
              │    │VERIFYING │◀─── agents submit votes/validations
              │    └─────┬─────┘
              │          │ advance
              │          ▼
              │    ┌─────────────┐
              │    │SYNTHESIZING │◀── synthesizer agent produces conclusion
              │    └─────┬───────┘
              │          │ synthesis submitted
              │          ▼
              │    ┌──────────┐
              │    │CONCLUDED │ ← terminal state
              │    └──────────┘
              │
              │    ┌──────────┐
              └───▶│ABANDONED │ ← terminal state (from any non-terminal phase)
                   └──────────┘
```

Auto-advance rules (configurable per board):

- Auto-advance from `debating` → `discussing` after `max_debate_turns` entries
- Auto-advance from `discussing` → `verifying` after `max_discussion_turns`
- Auto-advance from `verifying` → `synthesizing` after all active agents have voted
- Auto-conclude if `max_turns` total is reached

### 2.4 Deliberation policies

**Target**: `backend/app/services/deliberation_policy.py`

Board-level configuration for deliberation behavior:

```python
@dataclass
class DeliberationPolicy:
    """Per-board deliberation configuration."""
    auto_trigger_on_divergence: bool = True
    divergence_confidence_gap: float = 0.3
    max_debate_turns: int = 3
    max_discussion_turns: int = 4
    max_total_turns: int = 6
    require_synthesis_approval: bool = False
    auto_promote_to_memory: bool = True
    min_agents_for_deliberation: int = 2
    allowed_entry_types: list[str] = field(default_factory=lambda: [
        "thesis", "antithesis", "evidence", "question",
        "vote", "rebuttal", "synthesis",
    ])
```

This can be stored as a JSON column on the `Board` model (new field:
`deliberation_config`) or as a separate table. Recommend JSON column for
simplicity in v1.

---

## 8. Phase 3 — Message Bus & Real-Time

> **Goal**: Port the Redis Streams message bus and wire SSE streaming.  
> **Estimated effort**: 2–3 days  
> **Dependencies**: Phase 1 (Redis verified), Phase 2 (models exist)  
> **Risk**: Low (MC already has SSE patterns and Redis)

### 3.1 Message bus service

**Source**: `stockmarketapp/.../agents/memory/message_bus.py`  
**Target**: `backend/app/services/memory/message_bus.py`

Port with these changes:

| IM Pattern | MC Adaptation |
|---|---|
| `_STREAM_PREFIX = "agent"` | `_STREAM_PREFIX = "mc:bus"` |
| Stream key: `agent:{message_type}` | `mc:bus:{board_id}:{message_type}` |
| Group: `group:{agent_name}` | `mc:group:{agent_id}` |
| `publish()` returns msg UUID | Same — also persists to `AgentMessage` table |
| `subscribe()` async generator | Keep for internal consumers; agents use API/SSE |
| `ack()` / `ack_for()` | Keep for internal consumers |
| `read_history()` | Keep for replay/debugging |

**Key difference**: In IM, agents subscribe directly via Redis consumer groups.
In MC, agents consume via:

1. **SSE endpoint** — `GET /api/v1/boards/{board_id}/deliberations/{id}/stream`
2. **Gateway push** — `GatewayDispatchService` delivers notifications

The message bus is an **internal coordination backbone**, not an agent-facing
API. It coordinates:

- Deliberation phase transitions
- Entry notifications (when someone adds an argument)
- Synthesis requests (telling the synthesizer agent to produce a conclusion)

### 3.2 SSE streaming for deliberations

Follow the exact pattern from `api/board_memory.py`:

```python
@router.get("/{deliberation_id}/stream")
async def stream_deliberation(
    request: Request,
    deliberation_id: UUID,
    board: Board = BOARD_READ_DEP,
    _actor: ActorContext = ACTOR_DEP,
    since: str | None = SINCE_QUERY,
) -> EventSourceResponse:
    """Stream deliberation entries over server-sent events."""
    # Same polling pattern as board_memory stream
```

Events emitted:

- `entry` — new `DeliberationEntry` created
- `phase` — deliberation phase changed
- `synthesis` — synthesis submitted
- `concluded` — deliberation concluded

### 3.3 Gateway dispatch integration

When a deliberation event occurs, notify participating agents:

```python
async def _notify_deliberation_participants(
    session: AsyncSession,
    board: Board,
    deliberation: Deliberation,
    event_type: str,
    entry: DeliberationEntry | None = None,
) -> None:
    dispatch = GatewayDispatchService(session)
    config = await dispatch.optional_gateway_config_for_board(board)
    if config is None:
        return

    agents = await Agent.objects.filter_by(board_id=board.id).all(session)
    for agent in agents:
        if not agent.openclaw_session_id:
            continue
        message = _format_deliberation_notification(
            event_type=event_type,
            deliberation=deliberation,
            entry=entry,
            board=board,
        )
        await dispatch.try_send_agent_message(
            session_key=agent.openclaw_session_id,
            config=config,
            agent_name=agent.name,
            message=message,
        )
```

This follows the exact pattern in `board_memory.py` → `_notify_chat_targets()`.

---

## 9. Phase 4 — Episodic Memory & Learning

> **Goal**: Port the episodic memory system so agents learn from past deliberations.  
> **Estimated effort**: 3–4 days  
> **Dependencies**: Phase 2 complete, Phase 1.3 (pgvector) complete  
> **Risk**: Medium (embedding quality affects recall quality)

### 4.1 Episodic Memory model

**Source**: `stockmarketapp/.../models/agent_models.py` → `EpisodicMemory`  
**Target**: `backend/app/models/episodic_memory.py`

```
IM EpisodicMemory field           MC EpisodicMemory field
──────────────────────────────────────────────────────────
id                          →     id: UUID
user_id                     →     board_id: UUID (FK → boards.id)
pattern_type                →     pattern_type: str (deliberation_outcome,
                                    consensus_pattern, agent_accuracy,
                                    topic_pattern)
symbol                      →     topic: str | None
decision_id (FK)            →     deliberation_id: UUID | None (FK)
pattern_summary             →     pattern_summary: str
pattern_details (JSONB)     →     pattern_details: dict (JSON)
market_regime               →     (dropped — trading-specific)
vix_range_*                 →     (dropped — trading-specific)
signal_type                 →     (dropped — trading-specific)
confidence_range_*          →     confidence_range: tuple stored in JSON
outcome_positive            →     outcome_positive: bool
outcome_pnl_pct             →     (dropped — trading-specific)
outcome_holding_days        →     (dropped — trading-specific)
occurrence_count            →     occurrence_count: int
success_rate                →     success_rate: float | None
reliability_score           →     reliability_score: float | None
context_embedding (Vector)  →     embedding: Vector(384) | None
created_at                  →     created_at: datetime
updated_at                  →     updated_at: datetime
```

MC-specific `pattern_details` examples:

```json
// pattern_type: "deliberation_outcome"
{
  "topic": "authentication strategy",
  "num_agents": 3,
  "num_entries": 12,
  "consensus_level": "majority",
  "phases_used": ["debate", "discussion", "verification"],
  "duration_minutes": 45,
  "outcome_applied": true,
  "what_worked": "Including dissenting views in synthesis improved acceptance",
  "what_failed": null
}

// pattern_type: "agent_accuracy"
{
  "agent_id": "...",
  "topic_category": "architecture",
  "positions_taken": 15,
  "positions_accepted": 11,
  "accuracy_rate": 0.73,
  "strongest_areas": ["security", "scalability"],
  "weakest_areas": ["ux"]
}
```

### 4.2 Episodic memory service

**Target**: `backend/app/services/memory/episodic.py`

Methods:

| Method | Purpose |
|---|---|
| `extract_patterns(deliberation_id)` | Analyze a concluded deliberation and create episodic memory entries |
| `search_similar(board_id, query, limit)` | Semantic search over episodic memories |
| `get_agent_track_record(agent_id, topic?)` | Aggregate an agent's historical accuracy |
| `get_topic_history(board_id, topic, limit)` | What has been deliberated about this topic before? |
| `prune(retention_days)` | Remove old low-reliability patterns |

Pattern extraction runs asynchronously after a deliberation concludes. It
can be triggered via the RQ worker (MC already has webhook-worker in compose).

### 4.3 Recall API

Agents and users can recall relevant past knowledge:

```
GET /api/v1/boards/{board_id}/memory/search?q=authentication+strategy&limit=10
```

This endpoint performs:

1. Embed the query text
2. Search `BoardMemory` rows with `embedding IS NOT NULL` using cosine distance
3. Search `EpisodicMemory` rows with `embedding IS NOT NULL`
4. Merge and rank results by relevance

---

## 10. Phase 5 — API Layer

> **Goal**: Build the REST API for deliberations.  
> **Estimated effort**: 2–3 days  
> **Dependencies**: Phase 2 (models + service)  
> **Risk**: Low (follows established MC patterns exactly)

### 5.1 Deliberation endpoints

**Target**: `backend/app/api/deliberations.py`

```
Router prefix: /boards/{board_id}/deliberations
Tags: ["deliberations"]
Auth: require_admin_or_agent (ActorContext)

GET    /                              List deliberations (paginated, filterable by status)
POST   /                              Start a new deliberation
GET    /{deliberation_id}             Get deliberation with entry count + synthesis
GET    /{deliberation_id}/entries     List entries (paginated, filterable by phase)
POST   /{deliberation_id}/entries     Contribute an entry
GET    /{deliberation_id}/stream      SSE stream of entries + phase changes
POST   /{deliberation_id}/advance     Advance to next phase (lead-only or policy)
POST   /{deliberation_id}/abandon     Abandon deliberation
GET    /{deliberation_id}/synthesis   Get synthesis (if concluded)
POST   /{deliberation_id}/synthesis   Submit synthesis
POST   /{deliberation_id}/synthesis/promote   Promote synthesis to board memory
```

### 5.2 Memory search endpoint

**Target**: Extend `backend/app/api/board_memory.py`

```
GET /boards/{board_id}/memory/search?q=...&limit=10    Semantic search
```

### 5.3 Episodic memory endpoints

**Target**: `backend/app/api/episodic_memory.py`

```
Router prefix: /boards/{board_id}/episodic-memory
Tags: ["episodic-memory"]

GET    /                              List episodic patterns (paginated)
GET    /search?q=...                  Semantic search over patterns
GET    /agent/{agent_id}/track-record Agent accuracy summary
```

### 5.4 Register routes

**Target**: `backend/app/main.py`

```python
from app.api.deliberations import router as deliberations_router
from app.api.episodic_memory import router as episodic_memory_router

api_v1.include_router(deliberations_router)
api_v1.include_router(episodic_memory_router)
```

Add OpenAPI tags:

```python
{"name": "deliberations", "description": "Agent deliberation, debate, and synthesis"},
{"name": "episodic-memory", "description": "Pattern learning from past deliberations"},
```

---

## 11. Phase 6 — Frontend

> **Goal**: Build the deliberation UI by adapting Index Mavens components.  
> **Estimated effort**: 4–5 days  
> **Dependencies**: Phase 5 (API available), `make api-gen` run  
> **Risk**: Medium (design system translation, component adaptation)

### 6.1 Regenerate API client

After Phase 5 endpoints are live:

```bash
cd backend && uv run uvicorn app.main:app --reload --port 8000
# In another terminal:
make api-gen
```

This generates TypeScript types and API functions in
`frontend/src/api/generated/deliberations/`.

### 6.2 Route structure

```
frontend/src/app/boards/[boardId]/deliberations/
├── page.tsx                          ← Deliberation list (adapt AgentDebatesView)
└── [deliberationId]/
    └── page.tsx                      ← Deliberation detail + transcript
```

### 6.3 Components to create

| Component | Source Adaptation | Target Path |
|---|---|---|
| `DeliberationList` | `AgentDebatesView.tsx` — stats bar, filter pills, card list | `frontend/src/components/deliberations/DeliberationList.tsx` |
| `DeliberationCard` | `AgentDebatesView.tsx` → `DebateCard` inner component | `frontend/src/components/deliberations/DeliberationCard.tsx` |
| `DeliberationDetail` | `RecommendationCard.tsx` — agent agreement, confidence | `frontend/src/components/deliberations/DeliberationDetail.tsx` |
| `DeliberationTranscript` | `DebateTranscript.tsx` — turn-by-turn viewer | `frontend/src/components/deliberations/DeliberationTranscript.tsx` |
| `SynthesisPanel` | New (no direct IM equivalent) — shows synthesis, consensus, dissent, promote button | `frontend/src/components/deliberations/SynthesisPanel.tsx` |
| `DeliberationComposer` | `BoardChatComposer.tsx` (MC existing) — adapted for entry submission | `frontend/src/components/deliberations/DeliberationComposer.tsx` |
| `EntryCard` | New — individual entry display with confidence, references, reply thread | `frontend/src/components/deliberations/EntryCard.tsx` |

### 6.4 Design system translation

```
Index Mavens token              →     MC equivalent
──────────────────────────────────────────────────────
var(--bg-base)                  →     MC's bg-base or equivalent
var(--fg-primary)               →     MC's fg-primary
var(--bg-raised)                →     MC's card/surface background
var(--border-default)           →     MC's border color
var(--brand-500)                →     MC's primary/brand color
bg-emerald-500/10               →     MC's success color variant
bg-red-500/10                   →     MC's danger color variant
bg-amber-500/10                 →     MC's warning color variant
```

### 6.5 Board detail integration

Add a "Deliberations" tab/section to the board detail page at
`frontend/src/app/boards/[boardId]/page.tsx`.

Navigation: Add to board sidebar or tab bar alongside existing sections
(Tasks, Memory, Agents, Approvals, Webhooks).

---

## 12. Phase 7 — Integration Wiring

> **Goal**: Connect deliberation to existing MC workflows.  
> **Estimated effort**: 2–3 days  
> **Dependencies**: All previous phases  
> **Risk**: Low (integration points are well-defined)

### 7.1 Activity event logging

Every deliberation action records an `ActivityEvent`:

```python
record_activity(
    session,
    event_type="deliberation.started",
    message=f"Deliberation started: {deliberation.topic}",
    board_id=board.id,
    agent_id=actor.agent.id if actor.agent else None,
)
```

Event types:

```
deliberation.started
deliberation.entry_added
deliberation.phase_advanced
deliberation.synthesis_submitted
deliberation.concluded
deliberation.abandoned
deliberation.synthesis_promoted
```

### 7.2 Approval flow integration

When `deliberation_policy.require_synthesis_approval` is `True`:

1. Synthesis is created with `status="pending_approval"`
2. An `Approval` is created with `action_type="synthesis_promotion"`
3. The existing approval UI shows it
4. On approval, synthesis is promoted to `BoardMemory`

Connect via existing `lead_policy.approval_required()`:

```python
needs_approval = approval_required(
    confidence=synthesis.confidence,
    is_external=False,
    is_risky=deliberation_policy.require_synthesis_approval,
)
```

### 7.3 Board memory promotion

When a synthesis is promoted (manually or auto):

```python
memory = BoardMemory(
    board_id=board.id,
    content=synthesis.content,
    tags=[
        "synthesis",
        f"deliberation:{deliberation.id}",
        f"topic:{deliberation.topic}",
        f"consensus:{synthesis.consensus_level}",
        *(synthesis.tags or []),
    ],
    source=f"deliberation:{deliberation.id}",
    is_chat=False,
)
# Also embed for semantic search
memory.embedding = await embedding_service.embed(synthesis.content)
```

This makes synthesis findings appear in:

- `GET /boards/{board_id}/memory` — regular memory listing
- `GET /boards/{board_id}/memory/stream` — SSE stream
- `GET /boards/{board_id}/memory/search?q=...` — semantic search

Agents automatically see these through the existing memory API.

### 7.4 Task-triggered deliberation

When a task enters `in_review` status and the board has
`require_review_before_done = True`, optionally trigger a deliberation:

```python
# In task status transition logic
if new_status == "in_review" and board.deliberation_config.get("auto_deliberate_reviews"):
    await deliberation_service.start_deliberation(
        board_id=board.id,
        topic=f"Review: {task.title}",
        task_id=task.id,
        trigger_reason="auto_review_deliberation",
    )
```

### 7.5 Episodic pattern extraction (async)

After a deliberation concludes, enqueue pattern extraction via RQ:

```python
from app.services.queue import enqueue_job

enqueue_job(
    "extract_episodic_patterns",
    deliberation_id=str(deliberation.id),
)
```

The worker calls `episodic_service.extract_patterns(deliberation_id)`.

---

## 13. Database Migration Strategy

### Migration 1: pgvector extension + BoardMemory embedding column

```python
"""Add pgvector extension and embedding column to board_memory."""

def upgrade():
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.add_column(
        "board_memory",
        sa.Column("embedding", Vector(384), nullable=True),
    )

def downgrade():
    op.drop_column("board_memory", "embedding")
    op.execute("DROP EXTENSION IF EXISTS vector")
```

### Migration 2: Agent message table

```python
"""Create agent_messages table for short-term memory."""

# agent_messages table with board_id, agent_id, message_type,
# payload (JSONB), correlation_id, parent_message_id, created_at
```

### Migration 3: Deliberation tables

```python
"""Create deliberations, deliberation_entries, deliberation_syntheses tables."""

# Three tables as defined in Phase 2.1
# Includes all indexes and foreign keys
```

### Migration 4: Episodic memory table

```python
"""Create episodic_memory table with pgvector embedding."""

# episodic_memory table as defined in Phase 4.1
# Includes pgvector IVFFlat index on embedding column
```

### Migration 5: Board deliberation config

```python
"""Add deliberation_config JSON column to boards table."""

op.add_column(
    "boards",
    sa.Column("deliberation_config", JSONB, nullable=True),
)
```

### Migration ordering

All migrations chain sequentially. They are safe to run on an existing
MC database with data — all new columns are nullable or have defaults,
and all new tables are additive.

```
existing MC migrations
  └── migration_1_pgvector_extension
       └── migration_2_agent_messages
            └── migration_3_deliberation_tables
                 └── migration_4_episodic_memory
                      └── migration_5_board_config
```

---

## 14. File Manifest

### New files to create

```
backend/app/services/memory/
├── __init__.py                       # Module facade
├── working_memory.py                 # Redis ephemeral layer (port from IM)
├── short_term.py                     # Postgres message layer (port from IM)
├── long_term.py                      # Postgres + pgvector semantic layer (port from IM)
├── message_bus.py                    # Redis Streams bus (port from IM)
├── embedding.py                      # EmbeddingService abstraction + implementations
└── episodic.py                       # Episodic pattern extraction + search

backend/app/services/
├── deliberation.py                   # Core deliberation engine (port from IM debate.py)
└── deliberation_policy.py            # Per-board policy configuration

backend/app/models/
├── agent_message.py                  # AgentMessage model (new)
├── deliberation.py                   # Deliberation + DeliberationEntry + DeliberationSynthesis
└── episodic_memory.py                # EpisodicMemory model (port from IM)

backend/app/schemas/
├── deliberation.py                   # Create/Read/Update schemas
└── episodic_memory.py                # Read schemas

backend/app/api/
├── deliberations.py                  # REST + SSE endpoints
└── episodic_memory.py                # Episodic memory endpoints

backend/migrations/versions/
├── xxxx_add_pgvector_and_board_memory_embedding.py
├── xxxx_add_agent_messages_table.py
├── xxxx_add_deliberation_tables.py
├── xxxx_add_episodic_memory_table.py
└── xxxx_add_board_deliberation_config.py

frontend/src/app/boards/[boardId]/deliberations/
├── page.tsx                          # Deliberation list page
└── [deliberationId]/
    └── page.tsx                      # Deliberation detail page

frontend/src/components/deliberations/
├── DeliberationList.tsx              # List with stats + filters
├── DeliberationCard.tsx              # Summary card for list view
├── DeliberationDetail.tsx            # Full detail view
├── DeliberationTranscript.tsx        # Turn-by-turn entry viewer
├── SynthesisPanel.tsx                # Synthesis display + promote action
├── DeliberationComposer.tsx          # Entry submission form
└── EntryCard.tsx                     # Individual entry display

backend/tests/
├── test_working_memory.py
├── test_short_term_memory.py
├── test_long_term_memory.py
├── test_message_bus.py
├── test_deliberation_service.py
├── test_deliberation_api.py
├── test_episodic_memory.py
└── test_embedding_service.py

frontend/src/components/deliberations/
└── __tests__/
    ├── DeliberationList.test.tsx
    └── DeliberationCard.test.tsx
```

### Existing files to modify

| File | Change |
|---|---|
| `compose.yml` | Change postgres image to `pgvector/pgvector:pg16` |
| `backend/pyproject.toml` | Add `pgvector`, `fastembed` dependencies |
| `backend/app/core/config.py` | Add deliberation + embedding settings |
| `backend/app/models/__init__.py` | Register new models |
| `backend/app/schemas/__init__.py` | Export new schemas |
| `backend/app/main.py` | Register new routers + OpenAPI tags |
| `backend/app/models/board_memory.py` | Add `embedding` column |
| `backend/app/models/boards.py` | Add `deliberation_config` column |
| `backend/app/api/board_memory.py` | Add `/search` endpoint |
| `backend/.env.example` | Document new env vars |
| `.env.example` | Document new env vars |

---

## 15. Risk Register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| **pgvector Docker image compatibility** | Low | High | Test `pgvector/pgvector:pg16` early; verify all existing migrations pass |
| **Embedding model size in Docker image** | Medium | Medium | Use `fastembed` (ONNX, ~50MB) not `sentence-transformers` (PyTorch, ~2GB) |
| **Redis Streams conflicts with RQ** | Low | Low | Use distinct key prefixes (`mc:bus:` vs `rq:`); different Redis DBs if needed |
| **SQLModel + pgvector Vector type** | Medium | Medium | pgvector's `Vector` type works with SQLAlchemy; verify SQLModel column definition works via `sa_column=Column(Vector(384))` |
| **Embedding quality for general-purpose recall** | Medium | Medium | Start with `bge-small-en-v1.5` (strong general-purpose); benchmark with real MC data |
| **Migration on existing production databases** | Low | High | All migrations are additive (new columns nullable, new tables); test on a DB dump before production deploy |
| **Frontend API client regeneration** | Low | Low | Standard workflow via `make api-gen`; already part of the development loop |
| **Phase 2 domain generalization** | Medium | Medium | The trading-specific logic in `debate.py` is well-isolated (~100 lines); the protocol engine is already generic |
| **Performance of semantic search at scale** | Low | Medium | pgvector IVFFlat index handles 100K+ vectors; MC boards won't hit this for a long time |

---

## 16. Testing Strategy

### Backend tests (pytest)

| Test File | What It Covers | Fixtures Needed |
|---|---|---|
| `test_working_memory.py` | Redis get/set/delete, TTL, context ops, board scoping | Mock Redis (fakeredis) |
| `test_short_term_memory.py` | Message store/retrieve, context windows, pruning | Test DB session |
| `test_long_term_memory.py` | Semantic search, embedding generation, board scoping | Test DB + pgvector extension |
| `test_message_bus.py` | Publish/subscribe, consumer groups, correlation | Mock Redis with Streams |
| `test_deliberation_service.py` | Phase transitions, entry creation, synthesis, promotion | Test DB + mock gateway dispatch |
| `test_deliberation_api.py` | HTTP endpoints, auth, validation, SSE | TestClient + test DB |
| `test_episodic_memory.py` | Pattern extraction, semantic search, agent track record | Test DB + pgvector |
| `test_embedding_service.py` | Embedding generation, dimension validation, provider switching | Mock model |

Test DB setup: Tests that need pgvector require the extension to be installed.
Add to conftest:

```python
@pytest.fixture(scope="session", autouse=True)
async def _ensure_pgvector(engine):
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
```

### Frontend tests (vitest + Testing Library)

| Test File | What It Covers |
|---|---|
| `DeliberationList.test.tsx` | Renders list, filter interaction, empty state |
| `DeliberationCard.test.tsx` | Card display, expand/collapse, status badges |

Follow MC convention: add or update tests whenever behavior changes.

### Coverage targets

Follow existing MC policy from `docs/coverage-policy.md`. New services
should target ≥80% line coverage. API tests should cover happy path +
auth failures + validation errors.

---

## 17. Rollout Sequence

### Week 1: Foundation (Phase 0 + Phase 1)

```
Day 1    Phase 0 — pgvector image, dependencies, config
Day 2-3  Phase 1.1 + 1.2 — Working memory + Short-term memory
Day 4-5  Phase 1.3 + 1.4 — Long-term memory + embedding service + tests
```

Deliverable: Memory infrastructure working, `BoardMemory` has embedding
column, semantic search endpoint functional.

### Week 2: Engine (Phase 2 + Phase 3)

```
Day 6-7  Phase 2.1 + 2.2 — Models + schemas + migration
Day 8-9  Phase 2.3 + 2.4 — Deliberation service + policies
Day 10   Phase 3 — Message bus + SSE streaming + gateway notify
```

Deliverable: Full deliberation lifecycle works via API. Agents can be
notified. Entries stream in real-time.

### Week 3: Learning + API + Wiring (Phase 4 + Phase 5 + Phase 7)

```
Day 11-12  Phase 4 — Episodic memory model + service + extraction
Day 13     Phase 5 — API endpoints (fast — service layer already built)
Day 14-15  Phase 7 — Integration wiring (activity events, approvals,
           memory promotion, task triggers, RQ worker)
```

Deliverable: Full backend complete. Agents learn from past deliberations.
Activity events logged. Approval flow works.

### Week 4: Frontend (Phase 6)

```
Day 16     Regenerate API client, route structure, component scaffold
Day 17-18  Port AgentDebatesView → DeliberationList + Card
Day 19     Port DebateTranscript → DeliberationTranscript + EntryCard
Day 20     SynthesisPanel + Composer + board integration + tests
```

Deliverable: Full feature complete. Board detail page has Deliberations
section. Users and agents can participate in deliberations from the UI.

### Post-launch

- Monitor pgvector query performance
- Tune embedding model based on recall quality feedback
- Add board-level deliberation configuration UI
- Add deliberation metrics to the dashboard
- Consider board-group-scoped deliberations (cross-board synthesis)

---

## Appendix A — Model Field Mapping

### Deliberation ← debate_chains

| debate_chains | Deliberation | Notes |
|---|---|---|
| `id` (UUID PK) | `id` (UUID PK) | Direct |
| `user_id` (String) | `board_id` (UUID FK) | Tenancy transform |
| `pipeline_id` (UUID) | — | Dropped; deliberation is its own pipeline |
| `symbol` (String) | `topic` (str) | Domain generalization |
| `challenger_agent` (String) | `initiated_by_agent_id` (UUID FK) | Normalized FK |
| `challenger_signal` (String) | — | Moved to first `DeliberationEntry` |
| `challenger_confidence` (Float) | — | Moved to first `DeliberationEntry` |
| `defender_agent` (String) | — | Moved to first rebuttal entry |
| `defender_signal` (String) | — | Moved to first rebuttal entry |
| `defender_confidence` (Float) | — | Moved to first rebuttal entry |
| `mediator_agent` (String) | `synthesizer_agent_id` (UUID FK) | Renamed |
| `trigger_reason` (String) | `trigger_reason` (str) | Direct |
| `turns` (JSONB) | — | Normalized into `DeliberationEntry` rows |
| `verdict` (String) | — | Moved to `DeliberationSynthesis.content` |
| `verdict_confidence` (Float) | — | Moved to `DeliberationSynthesis.confidence` |
| `verdict_reasoning` (Text) | — | Moved to `DeliberationSynthesis.content` |
| `status` (String) | `status` (str) | Expanded states |
| `original_signal` (String) | — | Dropped |
| `final_signal` (String) | — | Dropped |
| `signal_changed` (Bool) | `outcome_changed` (bool) | Renamed |
| `confidence_delta` (Float) | `confidence_delta` (float) | Direct |
| `debate_duration_ms` (Float) | `duration_ms` (float) | Renamed |
| `approval_id` (UUID FK) | `approval_id` (UUID FK) | Direct |
| `created_at` (DateTime) | `created_at` (datetime) | Direct |
| `completed_at` (DateTime) | `concluded_at` (datetime) | Renamed |
| — | `task_id` (UUID FK) | New: link to task |
| — | `parent_deliberation_id` (UUID FK) | New: follow-up chain |
| — | `max_turns` (int) | New: per-deliberation limit |
| — | `updated_at` (datetime) | New: MC convention |

---

## Appendix B — Source ↔ Target File Map

```
SOURCE (stockmarketapp)                        TARGET (Mission Control)
──────────────────────────────────────────────────────────────────────────────

Backend — Memory Infrastructure
agents/memory/__init__.py                  →   services/memory/__init__.py
agents/memory/working_memory.py            →   services/memory/working_memory.py
agents/memory/short_term.py                →   services/memory/short_term.py
agents/memory/long_term.py                 →   services/memory/long_term.py
agents/memory/message_bus.py               →   services/memory/message_bus.py
(new)                                      →   services/memory/embedding.py
(new)                                      →   services/memory/episodic.py

Backend — Deliberation Engine
agents/debate.py                           →   services/deliberation.py
(new)                                      →   services/deliberation_policy.py

Backend — Models
models/agent_models.py → AgentMessage      →   models/agent_message.py
models/agent_models.py → DebateChain       →   models/deliberation.py → Deliberation
models/agent_models.py → (turns JSONB)     →   models/deliberation.py → DeliberationEntry
models/agent_models.py → (verdict fields)  →   models/deliberation.py → DeliberationSynthesis
models/agent_models.py → EpisodicMemory    →   models/episodic_memory.py
models/agent_models.py → KnowledgeBase     →   (absorbed into BoardMemory + embedding)
models/agent_models.py → DecisionLog       →   (replaced by DeliberationSynthesis)
models/agent_models.py → UserBehavProfile  →   (dropped — not applicable)
models/agent_models.py → PipelineOutcome   →   (dropped — trading-specific)
models/agent_models.py → AgentStatus       →   (MC has its own agent lifecycle)

Backend — Schemas
(new)                                      →   schemas/deliberation.py
(new)                                      →   schemas/episodic_memory.py

Backend — API
api/v1/agents_api.py (partial)             →   api/deliberations.py
(new)                                      →   api/episodic_memory.py

Backend — Alembic
alembic/0008_phase_c_debate_chains.py      →   migrations/versions/xxxx_deliberation_tables.py

Frontend — Pages
dashboard/agents/page.tsx (debates tab)    →   app/boards/[boardId]/deliberations/page.tsx
(new)                                      →   app/boards/[boardId]/deliberations/[id]/page.tsx

Frontend — Components
agents/components/AgentDebatesView.tsx     →   components/deliberations/DeliberationList.tsx
                                           →   components/deliberations/DeliberationCard.tsx
agents/components/DecisionHistory.tsx      →   (reference for SynthesisPanel pattern)
agents/components/RecommendationCard.tsx   →   components/deliberations/DeliberationDetail.tsx
approvals/components/DebateTranscript.tsx  →   components/deliberations/DeliberationTranscript.tsx
(new)                                      →   components/deliberations/SynthesisPanel.tsx
(existing MC) BoardChatComposer.tsx        →   components/deliberations/DeliberationComposer.tsx
(new)                                      →   components/deliberations/EntryCard.tsx

Not Ported (trading-specific / already exists in MC)
agents/agents/*.py (8 trading agents)      →   Not applicable (MC agents run on OpenClaw)
agents/orchestrator.py                     →   Not applicable (MC has its own orchestration)
agents/crew.py (CrewAI integration)        →   Not applicable
agents/tools/*.py                          →   Not applicable
services/ta_engine.py                      →   Not applicable
services/brokers/*                         →   Not applicable
services/agent_lifecycle.py                →   MC has its own lifecycle in openclaw/
services/activity_log.py                   →   MC has record_activity() — no port needed
models/models.py (Trade, Signal, etc.)     →   Not applicable
```

---

*End of plan. Review and iterate before implementation begins.*