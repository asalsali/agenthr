# AgentHR

**Workforce management for AI agent swarms.**

When you're running 5 agents, you can manage them by hand. When you're running 50 — spawning children, forming teams, consuming tokens across parallel workstreams — you can't. AgentHR is the control plane for agent swarms: it governs who gets spawned, prevents duplicate work, detects runaway agents, tracks what every agent learned, and ensures the swarm's institutional memory compounds instead of evaporating.

Not another observability tool. LangSmith tells you what an agent *did*. Braintrust tells you if the output was *good*. AgentHR tells you whether **the swarm is healthy** — whether agents are accumulating faster than they're completing, whether the system is producing output nobody reads, whether an agent type has degraded, and whether the next spawn should happen at all.

## The Problem

Agent swarms fail in ways that individual agents don't:

- **Zombie accumulation** — agents fail silently and keep consuming tokens. At scale, this bleeds budgets.
- **Duplicate work** — two agents in different teams research the same thing without knowing the other exists.
- **Institutional amnesia** — Agent #47 rediscovers what Agent #12 learned three hours ago because nobody transferred the knowledge.
- **Runaway spawning** — a parent agent keeps spawning children until the system is saturated, with no backpressure signal.
- **Invisible waste** — agents complete mandates, produce output, and that output is never read by anyone. The swarm is busy but not productive.

AgentHR solves these structurally, not reactively.

## Quick Start

```bash
pip install -e .
agenthr init      # create the database
agenthr serve     # start the API server on :8420
```

API docs at [http://localhost:8420/docs](http://localhost:8420/docs).

## How It Works

### Spawn Gates — Swarm Growth Control

Every agent registration passes through four gates before the swarm grows by one:

1. **Overlap detection** — scans the active roster for agents with similar mandates. Cross-team overlap blocks the spawn; same-team overlap warns. Prevents duplicate work across the swarm.
2. **Resource pressure** — a continuous 0.0-1.0 score computed from agent density, token burn rate, turnover ratio, and handoff utilization. At green (< 0.4), spawns proceed freely. At yellow, they're logged with justification. At orange, they require explicit justification. At red (> 0.9), spawning halts and consolidation triggers. This is backpressure for agent swarms.
3. **Memory retrieval** — searches the swarm's accumulated exit reports for relevant prior learnings. If Agent #12 already researched this topic, Agent #47 gets those findings at spawn time. No agent starts from scratch.
4. **Integrity check** — verifies the agent type definition hasn't been tampered with.

### Semantic Heartbeats — Swarm Liveness

Every active agent emits heartbeats — not just "I'm alive" pings, but structured progress reports: what action was taken, what phase the agent is in, whether the work is converging or diverging, and which tools are being used.

Five anomaly detectors run on every heartbeat across the entire swarm:

| Pattern | What it catches |
|---|---|
| **Territory drift** | Agent operating outside its assigned domain |
| **Token hemorrhage** | Agent burning tokens at 3x the expected rate |
| **Stuck loops** | Agent repeating the same action 3 times in sequence |
| **Role drift** | Analyst doing writer work, writer doing research — tool profile diverges >40% from type baseline |
| **Output contradiction** | Agent overwriting its own recent output (fighting itself) |

Two distinct anomaly signals on the same agent within 5 heartbeats triggers automatic escalation. At swarm scale, this is the difference between catching a runaway agent in 30 seconds vs. discovering it burned $50 of tokens after the fact.

Agents that stop emitting heartbeats are flagged as stale. This is the **default-dead** primitive: existence requires continuous proof of progress. No heartbeat, no agent. Zombie processes are impossible by construction.

### Trust Progression — Swarm Capability Governance

Agent types earn operational latitude through track record, not declaration:

| Level | Name | Earned by | Unlocks |
|---|---|---|---|
| 0 | **Untested** | First spawn | Low token budget only |
| 1 | **Proven** | 3+ completed mandates, 0 violations | Medium budget |
| 2 | **Trusted** | 10+ mandates, token efficiency within 20% of baseline, >85% completion rate | High budget, cross-team mandates |
| 3 | **Veteran** | 25+ mandates, explicit endorsement | No cap, domain elder status, auto-approved same-team spawns |

Demotion is graduated: first violation is noted (no impact), second freezes promotion, third drops one level. This means a single bad run doesn't destroy a track record — but a pattern of failure does.

**Candor bonus:** agent types whose exit reports consistently include honest `what_failed`, `gaps`, and `dissent` fields earn a 20% reduction in promotion thresholds. The swarm rewards honesty over self-preservation.

### Exit Reports — Swarm Memory

When an agent shuts down, it writes a structured exit report:

```json
{
  "mandate_completed": true,
  "key_findings": ["ECE enrollment up 12% YoY"],
  "what_worked": "Direct SQL queries against Quest extract",
  "what_failed": "PowerBI API too slow for ad-hoc queries",
  "recommendations": "Build a cached view for enrollment metrics",
  "gaps": [{"domain": "retention", "description": "No data linked"}],
  "decisions": [{"id": "d-001", "summary": "Used SQL over API"}],
  "dissent": [{"position": "Use GraphQL", "outcome": "rejected"}],
  "contrarian": "Enrollment growth may plateau as housing crisis worsens",
  "tokens_consumed": 2400
}
```

Exit reports are searchable and feed into spawn gates (memory retrieval), baselines (performance tracking), and trust evaluation. Every agent that shuts down makes the swarm smarter. Every agent that shuts down without a report is institutional memory lost.

### Vital Signs — Swarm Health at a Glance

Five system-level health metrics tell you whether the swarm is healthy:

| Vital Sign | Healthy | Unhealthy Signal |
|---|---|---|
| **Turnover ratio** (archived / spawned, 24h) | 0.6 - 1.0 | Below 0.6: swarm is growing faster than it's completing work |
| **Token efficiency** (avg tokens per mandate) | Within 30% of baseline | Deviation: something changed, investigate |
| **Handoff utilization** (exit reports read / total) | Above 50% | Below 50%: agents are ignoring their predecessors |
| **Team memory freshness** | Under 7 days | Beyond 7 days: domain knowledge is decaying |
| **Output consumption** (outputs that were read / total) | Above 30% | Below 30%: the swarm is producing waste |

A swarm with low handoff utilization and high agent accumulation is sick in a way that no individual agent metric captures. Vital signs surface swarm-level pathology.

### Futility Detection

The hardest failure to catch: an agent completes its mandate correctly, produces correct output, and nobody ever uses it. No error. No crash. Just waste.

AgentHR tracks which exit reports are referenced by subsequent agents and which outputs are consumed. Exit reports that sit untouched for 24+ hours are flagged as futility candidates. The swarm learns what kinds of work produce value and what kinds produce noise.

## API Reference

### Swarm Registry

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/agents` | Spawn an agent (runs all 4 gates) |
| `GET` | `/agents` | List swarm roster (filter by status, team, type, trust) |
| `GET` | `/agents/:id` | Agent profile |
| `PATCH` | `/agents/:id` | Update agent |
| `DELETE` | `/agents/:id` | Terminate agent |
| `GET` | `/agents/roster/stats` | Swarm summary (counts by status, type, team, trust) |
| `GET` | `/agents/pressure` | Current resource pressure |

### Heartbeat

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/agents/:id/heartbeat` | Record heartbeat (runs anomaly detection) |
| `GET` | `/agents/:id/heartbeat` | Latest heartbeat |
| `GET` | `/agents/:id/heartbeats` | Heartbeat history |
| `GET` | `/heartbeats/stale` | Find zombie agents |

### Evaluation

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/agents/:id/exit-report` | Submit exit report (updates baselines + trust) |
| `GET` | `/agents/:id/exit-report` | Get exit report |
| `GET` | `/exit-reports/search?q=` | Search swarm knowledge base |
| `GET` | `/baselines/:type` | Performance baseline for agent type |
| `GET` | `/baselines/:type/degradation` | Regression drift check |
| `GET` | `/trust/:type` | Trust evaluation with promotion criteria |
| `POST` | `/trust/:type/promote` | Promote agent type |

### Analytics

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/analytics/vital-signs` | Swarm health dashboard |
| `GET` | `/analytics/hotspots` | Overloaded agent types (high fan-out/fan-in) |
| `GET` | `/analytics/futility` | Wasted work detection |
| `GET` | `/analytics/teams/:id` | Team-level health |
| `GET` | `/analytics/types/:type` | Full agent type report |

### Onboarding

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/agents/:id/onboard` | Run Genesis Phase, deliver swarm context |

## Usage

### Spawn an agent into the swarm

```bash
curl -X POST http://localhost:8420/agents \
  -H "Content-Type: application/json" \
  -d '{
    "type": "analyst",
    "name": "enrollment-scout",
    "framework": "langgraph",
    "mandate": "Analyze enrollment trends in ECE program",
    "mandate_type": "research",
    "team_id": "engineering-data"
  }'
```

The response includes the spawn gate results — whether overlaps were found, what the swarm pressure is, and what prior agents learned about similar mandates.

### Monitor the swarm

```bash
# Are any agents stale?
curl http://localhost:8420/heartbeats/stale

# Is the swarm healthy?
curl http://localhost:8420/analytics/vital-signs

# Are we producing waste?
curl http://localhost:8420/analytics/futility

# Which agent types are overloaded?
curl http://localhost:8420/analytics/hotspots
```

### Search swarm knowledge

```bash
# What has the swarm learned about enrollment?
curl "http://localhost:8420/exit-reports/search?q=enrollment"

# Is the analyst type degrading?
curl http://localhost:8420/baselines/analyst/degradation

# Should we promote the analyst type?
curl http://localhost:8420/trust/analyst
```

## Architecture

```
agenthr/
  api.py               # FastAPI (18 endpoints)
  cli.py               # CLI (serve, init)
  database.py          # SQLAlchemy 2.0 async ORM (7 tables)
  models.py            # Pydantic v2 models (19 models)
  services/
    registry.py        # Spawn gates, roster, pressure
    heartbeat.py       # Liveness, staleness, anomaly detection
    evaluate.py        # Exit reports, baselines, trust, violations
    analytics.py       # Vital signs, hotspots, futility
```

**Database:** SQLite for dev, Postgres for production (`DATABASE_URL` env var).

**Framework-agnostic:** AgentHR doesn't run agents — it governs them. Works with any swarm framework. Adapters for LangGraph, CrewAI, and Claude Code are planned.

## Theoretical Foundation

AgentHR implements two formally proven design axioms:

**Default-dead semantics.** Agents must continuously produce verifiable proof of progress or be terminated. This eliminates zombie agents by construction — no external monitor required. At swarm scale, this is the difference between bounded waste and unbounded token burn. Formally: default-dead systems satisfy a bounded-waste safety property as a structural invariant that default-alive systems can only achieve through external monitoring at cost Omega(n/delta).

**Earned irrevocability.** Trust is graduated, not binary. The trust progression system implements irrevocable delegation that is earned through demonstrated reliability, avoiding the "flag-based irrevocability" trap where declared-but-unenforced delegation is provably dominated by either pure strategy.

See: *Two Design Axioms for Multi-Agent AI Systems* (Salsali, 2026).

## License

MIT
