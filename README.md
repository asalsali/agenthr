# AgentHR

**Workforce management for AI agent fleets.**

AgentHR is the HR system your agents never had. It manages the full agent lifecycle — hiring, onboarding, performance evaluation, trust progression, and termination — the same way Workday manages human employees, but for AI agents.

Not another observability tool. LangSmith tells you what an agent *did*. Braintrust tells you if the output was *good*. AgentHR tells you whether you should **keep employing this agent type**, whether it needs retraining, whether it should be promoted to handle harder tasks, and whether its output was ever actually used.

## Quick Start

```bash
pip install -e .
agenthr init      # create the database
agenthr serve     # start the API server on :8420
```

API docs at [http://localhost:8420/docs](http://localhost:8420/docs).

## Core Concepts

### Agent Lifecycle

Every agent moves through a governed lifecycle:

```
Hire → Onboard → Active → Shutdown → Archived
       (Genesis)  (Heartbeats, Evaluation)  (Exit Report)
```

**Hiring** runs spawn gates before any agent is registered — overlap detection, resource pressure checks, and memory retrieval from predecessors. No agent starts from scratch if the system has relevant prior learnings.

**Onboarding** (the Genesis Phase) delivers context: the mandate, team memory, predecessor exit reports, and compliance rules. The agent forms a world model before taking action.

**Active** agents emit semantic heartbeats — proof of progress, not just proof of existence. Five anomaly detectors run on every heartbeat, catching territory drift, token hemorrhage, stuck loops, role drift, and output contradictions.

**Shutdown** produces a structured exit report: what worked, what failed, gaps, decisions, dissent. This becomes institutional memory for the next generation.

### Trust Progression

Trust is earned, not declared. Agent types progress through four levels based on demonstrated reliability:

| Level | Name | Criteria | Unlocks |
|---|---|---|---|
| 0 | **Untested** | New agent type | Low token budget |
| 1 | **Proven** | 3+ completed mandates, 0 violations | Medium budget |
| 2 | **Trusted** | 10+ mandates, efficiency within 20% of baseline | High budget, cross-team mandates |
| 3 | **Veteran** | 25+ mandates, explicit endorsement | No token cap, domain elder status |

Demotion is graduated: first violation is noted, second freezes promotion, third drops one level. Agents that honestly self-report failures (non-empty `what_failed`, `gaps`, `dissent` in exit reports) get a 20% reduction in promotion thresholds.

### Spawn Gates

Every `POST /agents` passes through four gates:

1. **Overlap detection** — finds active agents with similar mandates via keyword intersection. Cross-team overlap blocks; same-team overlap warns.
2. **Resource pressure** — a continuous 0.0-1.0 score from agent density, token consumption, turnover ratio, and handoff utilization. Green/yellow/orange/red graduated response.
3. **Memory retrieval** — searches predecessor exit reports for relevant learnings. No mandate starts from scratch.
4. **Integrity check** — verifies agent type definition hash.

### Innate Detection

Five anomaly patterns run on every heartbeat:

| Pattern | What it catches |
|---|---|
| Territory drift | Agent writing outside its domain |
| Token hemorrhage | Consumption at 3x expected rate |
| Stuck loops | 3 identical actions in sequence |
| Role drift | Tool usage diverges >40% from type baseline |
| Output contradiction | Agent overwriting its own recent output |

Two distinct signals on the same agent within 5 heartbeats triggers automatic escalation.

### Vital Signs

System-level health metrics computed from agent telemetry:

| Vital Sign | Healthy Range | Unhealthy Signal |
|---|---|---|
| Agent turnover ratio | 0.6 - 1.0 | Below 0.6: agents accumulating |
| Token efficiency trend | Within 30% of baseline | Deviation: regression drift |
| Handoff utilization | Above 50% | Below 50%: institutional memory ignored |
| Team memory freshness | Under 7 days | Beyond 7 days: knowledge decaying |
| Output consumption | Above 30% | Below 30%: system producing waste |

## API Reference

### Registry

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/agents` | Hire a new agent (runs spawn gates) |
| `GET` | `/agents` | List roster (filter by status, team, type, trust) |
| `GET` | `/agents/:id` | Get agent profile |
| `PATCH` | `/agents/:id` | Update agent (status, phase, team, tokens) |
| `DELETE` | `/agents/:id` | Terminate (archive) agent |
| `GET` | `/agents/roster/stats` | Roster summary |
| `GET` | `/agents/pressure` | Current resource pressure |

### Heartbeat

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/agents/:id/heartbeat` | Record heartbeat (runs anomaly detection) |
| `GET` | `/agents/:id/heartbeat` | Latest heartbeat |
| `GET` | `/agents/:id/heartbeats` | Heartbeat history |
| `GET` | `/heartbeats/stale` | Find agents with stale heartbeats |

### Evaluation

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/agents/:id/exit-report` | Submit exit report |
| `GET` | `/agents/:id/exit-report` | Get exit report |
| `GET` | `/exit-reports/search?q=` | Search exit reports by keyword |
| `GET` | `/baselines/:type` | Performance baseline for agent type |
| `GET` | `/baselines/:type/degradation` | Check for regression drift |
| `GET` | `/trust/:type` | Trust evaluation with promotion criteria |
| `POST` | `/trust/:type/promote` | Promote agent type trust level |

### Analytics

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/analytics/vital-signs` | System health dashboard |
| `GET` | `/analytics/hotspots` | High-connectivity agent types |
| `GET` | `/analytics/futility` | Agents whose output was never consumed |
| `GET` | `/analytics/teams/:id` | Per-team health |
| `GET` | `/analytics/types/:type` | Full agent type report |

### Onboarding

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/agents/:id/onboard` | Run Genesis Phase, deliver context |

## Usage Examples

### Hire an agent

```bash
curl -X POST http://localhost:8420/agents \
  -H "Content-Type: application/json" \
  -d '{
    "type": "analyst",
    "name": "enrollment-scout",
    "framework": "claude-code",
    "mandate": "Analyze enrollment trends in ECE program",
    "mandate_type": "research"
  }'
```

### Send a heartbeat

```bash
curl -X POST http://localhost:8420/agents/{id}/heartbeat \
  -H "Content-Type: application/json" \
  -d '{
    "last_action": "Querying enrollment database",
    "phase": "execution",
    "action_count": 5,
    "momentum": "converging",
    "tool_breakdown": {"Read": 3, "Bash": 2}
  }'
```

### Submit an exit report

```bash
curl -X POST http://localhost:8420/agents/{id}/exit-report \
  -H "Content-Type: application/json" \
  -d '{
    "mandate_completed": true,
    "key_findings": ["ECE enrollment up 12% YoY"],
    "what_worked": "Direct SQL queries",
    "what_failed": "PowerBI API too slow",
    "recommendations": "Build a cached view",
    "gaps": [],
    "decisions": [],
    "dissent": [],
    "tokens_consumed": 2400
  }'
```

### Check system health

```bash
curl http://localhost:8420/analytics/vital-signs
```

## Architecture

```
agenthr/
  __init__.py          # Package exports
  api.py               # FastAPI application (18 endpoints)
  cli.py               # CLI (serve, init)
  database.py          # SQLAlchemy 2.0 async ORM (7 tables)
  models.py            # Pydantic v2 data models (19 models)
  services/
    registry.py        # Hire, terminate, spawn gates, pressure
    heartbeat.py       # Heartbeats, staleness, anomaly detection
    evaluate.py        # Exit reports, baselines, trust, violations
    analytics.py       # Vital signs, hotspots, futility, team health
```

**Database:** SQLite for development, Postgres for production (set `DATABASE_URL` env var).

**Framework-agnostic:** AgentHR manages agents from any framework. Adapters for LangGraph, CrewAI, and Claude Code are planned.

## Theoretical Foundation

AgentHR's design is grounded in two formal results:

- **Default-dead agent semantics** — agents must continuously produce verifiable proof of progress or be terminated. Eliminates zombie agents by construction. Based on the Alpern-Schneider safety/liveness decomposition.

- **Irrevocable delegation** — flag-based irrevocability (declaring delegation irrevocable without enforcement) is strictly dominated by either pure revocable or pure irrevocable delegation. The trust progression system implements graduated, earned irrevocability.

See: *Two Design Axioms for Multi-Agent AI Systems* (Salsali, 2026).

## License

MIT
