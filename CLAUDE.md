# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

SRE Multi-Agent System for automated Root Cause Analysis. Built on the **Strands Agents SDK** using the "Agents as Tools" pattern — specialist agents are registered as callable tools on a parent orchestrator via `.as_tool()`.

Two operating modes share the same agent graph:
- **Interactive CLI** (`sre-agent`) — Typer + Rich terminal UI for manual incident investigation.
- **Automated Pipeline** (`sre-agent serve`) — FastAPI webhook receiver with a 2-phase split (A: always-on data collection + runbook match; B: on-demand RCA + solution after human approval).

## Common Commands

```bash
# Install for development (all optional extras + dev deps)
pip install -e ".[all,dev]"   # or: make install-dev

# Lint / format (Ruff; line length 120, target py311). No pre-commit hooks — run manually.
make lint        # ruff check src/ && ruff format --check src/
make format      # ruff check --fix src/ && ruff format src/

# Build / clean
make build       # python -m build (hatchling)
make clean

# Run
sre-agent                     # interactive CLI
sre-agent serve --port 8080   # pipeline webhook server

# Tests (pytest + pytest-asyncio)
pytest tests/
pytest tests/unit/test_config.py -v              # single file
pytest tests/unit/test_runbook.py::test_name     # single test
pytest tests/unit/test_runbook.py --asyncio-mode=auto

# E2E stack (Prometheus + Alertmanager + cAdvisor + Elasticsearch + memory-leak workload)
docker compose -f docker-compose.test.yaml up -d
```

Optional extras gate specific features: `ssh` pulls in `paramiko` (required for runbook execution), `webhook` pulls in `fastapi`/`uvicorn`/`jinja2`/`markdown` (required for `sre-agent serve`). `all` bundles both.

## Architecture

### Agent graph

Each specialist is wrapped as a Strands `Agent` and exposed as a tool on its parent orchestrator. There are three orchestrators:

- **`agents/orchestrator.py`** — master orchestrator used by the interactive CLI. Registers all specialists (Data Collector, SSH, RCA, Solution, Runbook Matcher) as tools.
- **`agents/phase_a_orchestrator.py`** — pipeline Phase A. Runs on every incident; only has Data Collector + Runbook Matcher. Does **not** perform RCA.
- **`agents/phase_b_orchestrator.py`** — pipeline Phase B. Invoked on user approval; runs RCA + Solution on data already collected by Phase A.

RCA and Solution agents are **pure reasoning** — they have no tools. Data Collector and SSH reach out via MCP subprocess servers.

### MCP servers (`mcp_servers/`)

Each server is a FastMCP stdio subprocess spawned by the Strands agent that owns it. Tools are declared with `@mcp.tool()`. Servers: Prometheus (metrics + alerts), Elasticsearch (logs), ServiceNow CMDB (topology), APM, plus two SSH servers — `ssh_diagnostic_server.py` (read-only diagnostics for Data Collector) and `ssh_server.py` (runbook command execution).

### Pipeline flow (`pipeline/`)

```
Alertmanager / generic webhook
 → intake.py     (dedup key, severity routing, grouping)
 → analyzer.py   (Phase A orchestrator → report with runbook match)
 → delivery.py   (Teams notification with approval link)
 → approval.py   (web UI → Phase B → SSH runbook execution)
```

Phase A always runs. Phase B only runs after a human clicks approval in the web UI (`templates/approval.html`, 10-minute timeout). Runbook output from Phase A includes a structured `visualization_json` block used by the approval page to render Chart.js metric charts.

### Configuration loading (`config.py` → `load_settings()`)

Sources applied in order (later wins):
1. Bundled `src/sre_agent/defaults/settings.yaml` (shipped in the wheel).
2. `~/.config/sre-agent/settings.yaml`.
3. `configs/settings.yaml` (or `.yml`) in the repo.
4. `SRE_AGENT_CONFIG` env var (explicit path).
5. Env overlays: `ANTHROPIC_API_KEY`, `ANTHROPIC_BASE_URL`, `ANTHROPIC_MODEL_ID`, `SERVICENOW_INSTANCE_URL`, `SERVICENOW_API_TOKEN`.

First-run CLI triggers an interactive wizard that seeds `~/.config/sre-agent/settings.yaml` + `.env`.

### Per-agent tokens and prompts

- `settings.yaml` has an `agent_tokens` block setting `max_tokens` per agent (RCA defaults highest at 8192 — it is reasoning-heavy).
- Every agent has a dedicated prompt module in `prompts/` (`orchestrator.py`, `phase_a.py`, `phase_b.py`, `data_collector.py`, `rca.py`, `solution.py`, `operator.py`, `ssh.py`). System prompts are in Korean; incident output is a structured Markdown report.

### Callbacks (`callbacks.py`)

Two progress-reporting styles share the same agent code: a Rich-based live display for the CLI, and a plain-logging version for the pipeline. Orchestrators accept `callback_handler` (agent-level) and `tool_callback_handler` (forwarded to sub-agents for MCP tool calls) separately — keep that split when adding new agents.

## Conventions

- **Ruff** is the only lint/format tool (line length 120, target `py311`). No other linters, no pre-commit — run `make lint` before committing.
- **Pydantic v2** models (`BaseModel` / `BaseSettings`) with explicit typed defaults for all config.
- **HTTP** calls use `httpx.AsyncClient`; async tests use `pytest-asyncio`.
- **MCP tools** are declared with FastMCP `@mcp.tool()` decorators; servers run as stdio subprocesses, not HTTP.
- **Runbooks** live in `src/sre_agent/runbooks/*.md` with YAML frontmatter; only `_template.md` is committed (real runbooks are site-specific). Referenced shell scripts go under `runbooks/scripts/`.

## Security model

- SSH commands must appear in the whitelist (`configs/ssh_allowlist.yaml` / bundled default). Shell metacharacters (`;`, `&&`, `|`, `>`, backticks, etc.) are rejected before execution.
- No agent writes to monitored systems; all output is advisory. The only write path is runbook execution, which requires human approval in the web UI.
- When adding new MCP tools, keep them read-only unless they are gated by the approval flow.

## Key entry points

- CLI app: `src/sre_agent/cli.py` → `app` (Typer)
- Interactive orchestrator: `src/sre_agent/agents/orchestrator.py` → `create_orchestrator()`
- Pipeline server: `src/sre_agent/pipeline/server.py` (FastAPI)
- Config loader: `src/sre_agent/config.py` → `load_settings()`
- Package console script: `sre-agent` → `sre_agent.cli:app`

## Testing notes

Shared fixtures in `tests/conftest.py` cover Alertmanager v4 payloads, runbook markdown with frontmatter, and sample match-found / no-match incident reports. MCP server unit tests live under `tests/unit/mcp_servers/`. The E2E memory-leak scenario (docker compose stack) drives a `ContainerMemoryPressure` alert at ~85% of a 256Mi cgroup limit and exercises the full Alertmanager → pipeline → approval → SSH path; the matching runbook is `memory-leak-restart`.
