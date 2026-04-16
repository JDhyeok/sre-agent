# CLAUDE.md - SRE Multi-Agent System

## Project Overview

SRE Multi-Agent System for automated Root Cause Analysis (RCA). Uses the **Strands Agents SDK** with an "Agents as Tools" pattern to coordinate specialist AI agents that collect observability data, perform root cause analysis, and suggest/execute remediation via runbooks.

**Two operating modes:**
- **Interactive CLI** (`sre-agent`) - Real-time incident investigation
- **Automated Pipeline** (`sre-agent serve`) - Webhook-driven incident response with approval workflow

## Quick Reference

| Action | Command |
|--------|---------|
| Install (basic) | `pip install .` |
| Install (all extras) | `pip install ".[all]"` |
| Install (dev) | `pip install -e ".[all,dev]"` or `make install-dev` |
| Run CLI | `sre-agent` |
| Run pipeline server | `sre-agent serve` |
| Run tests | `pytest tests/` |
| Lint (check only) | `make lint` |
| Format (auto-fix) | `make format` |
| Build package | `make build` |
| Clean artifacts | `make clean` |
| Start test stack | `docker compose -f docker-compose.test.yaml up -d` |

## Tech Stack

- **Python 3.11+** (supports 3.11, 3.12, 3.13)
- **Strands Agents SDK** (`strands-agents[anthropic]`) - Multi-agent orchestration
- **FastMCP** - MCP tool servers (stdio subprocess transport)
- **Typer + Rich** - CLI framework and terminal rendering
- **FastAPI** - Pipeline webhook server (optional `webhook` extra)
- **Paramiko** - SSH operations (optional `ssh` extra)
- **Pydantic v2 + pydantic-settings** - Configuration and validation
- **Hatchling** - Build system
- **Ruff** - Linter and formatter
- **pytest + pytest-asyncio** - Test framework

## Repository Structure

```
src/sre_agent/
├── cli.py                  # Entry point: interactive CLI + serve command
├── config.py               # Pydantic settings, YAML loading, env var overlays
├── model.py                # LLM model setup (Anthropic)
├── callbacks.py            # Progress display (Rich for CLI, logging for pipeline)
├── agents/                 # Specialist agents (Strands Agent instances)
│   ├── orchestrator.py     # Master orchestrator, creates all sub-agents
│   ├── phase_a_orchestrator.py  # Phase A: data collection + runbook matching
│   ├── phase_b_orchestrator.py  # Phase B: full analysis + approval handling
│   ├── data_collector.py   # Unified metrics/logs/topology collection
│   ├── ssh.py              # SSH diagnostics agent
│   ├── rca.py              # Root cause analysis (5-phase framework)
│   ├── solution.py         # Remediation recommendation
│   └── operator.py         # Runbook matching
├── mcp_servers/            # FastMCP tool servers (each runs as stdio subprocess)
│   ├── prometheus_server.py      # Prometheus metrics + alerts
│   ├── elasticsearch_server.py   # Log search
│   ├── apm_server.py             # APM integration
│   ├── servicenow_cmdb_server.py # CMDB topology
│   ├── ssh_diagnostic_server.py  # Read-only SSH diagnostics
│   └── ssh_server.py             # SSH command execution
├── prompts/                # System prompt modules (one per agent)
├── pipeline/               # Automated pipeline (FastAPI)
│   ├── server.py           # FastAPI app with webhook + incident endpoints
│   ├── intake.py           # Alert dedup + severity routing
│   ├── analyzer.py         # Phase orchestration (A → B)
│   ├── delivery.py         # Teams webhook notifications
│   └── approval.py         # Web UI + SSH execution for runbooks
├── tools/                  # In-process Strands tools
│   └── runbook.py          # Runbook listing and matching
├── runbooks/               # Markdown runbooks (only _template.md tracked)
├── templates/              # Jinja2 HTML templates (approval.html)
└── defaults/               # Bundled config defaults (shipped in wheel)
    ├── settings.yaml
    └── ssh_allowlist.yaml

tests/
├── conftest.py             # Shared fixtures (alert payloads, runbook samples)
└── unit/
    ├── test_config.py      # Configuration loading
    ├── test_callbacks.py   # Progress tracking
    ├── test_intake.py      # Alert deduplication
    ├── test_analyzer.py    # Pipeline analysis
    ├── test_approval.py    # Approval workflow
    ├── test_runbook.py     # Runbook matching
    └── mcp_servers/        # MCP server unit tests

configs/                    # Development configuration
├── settings.yaml           # Dev settings (data source URLs, SSH hosts, etc.)
├── ssh_allowlist.yaml      # SSH command whitelist
└── memory-leak-daemon.py   # Test workload for E2E stack
```

## Architecture

### Agent Hierarchy

The system uses "Agents as Tools" - each specialist agent is registered as a callable tool on its parent orchestrator:

```
Orchestrator (master)
├── Data Collector Agent → Prometheus, Elasticsearch, ServiceNow CMDB (via MCP)
├── SSH Agent → Target servers (via MCP, read-only)
├── RCA Agent → 5-phase root cause analysis (reasoning only, no tools)
├── Solution Agent → Remediation recommendations (reasoning only, no tools)
└── Runbook Matcher Agent → list_runbooks, get_runbook tools
```

### Pipeline Flow (Automated Mode)

```
Webhook → Intake (dedup/routing) → Phase A (data collection + runbook match)
  → If runbook matched: Phase B (approval UI → SSH execution)
  → If no match: Full orchestrator analysis → Teams notification
```

### Configuration Precedence

Settings are loaded in this order (later overrides earlier):
1. Bundled defaults (`src/sre_agent/defaults/settings.yaml`)
2. User config (`~/.config/sre-agent/settings.yaml`)
3. Local config (`configs/settings.yaml` or `configs/settings.yml`)
4. `SRE_AGENT_CONFIG` env var (explicit path)
5. Environment variable overlays: `ANTHROPIC_API_KEY`, `ANTHROPIC_BASE_URL`, `ANTHROPIC_MODEL_ID`

## Code Style and Conventions

- **Line length:** 120 characters
- **Formatter/Linter:** Ruff (target Python 3.11)
- **Always run `make lint` before committing** to check for issues
- **Run `make format` to auto-fix** style issues
- **No pre-commit hooks** are configured; lint manually
- Config models use **Pydantic v2 BaseModel/BaseSettings** with typed defaults
- Each agent has a **dedicated system prompt module** in `src/sre_agent/prompts/`
- MCP servers use **FastMCP** with `@mcp.tool()` decorators
- Async code uses **`httpx.AsyncClient`** for HTTP calls
- Tests use **`pytest-asyncio`** with async fixtures

## Testing

```bash
# Run all tests
pytest tests/

# Run specific test file
pytest tests/unit/test_config.py -v

# Run with async support
pytest tests/unit/test_runbook.py --asyncio-mode=auto
```

**Test fixtures** are in `tests/conftest.py` and provide:
- `sample_alertmanager_payload` - Mock Alertmanager webhook (version 4)
- `sample_runbook_markdown` - Runbook with YAML frontmatter
- `sample_match_found_report` / `sample_no_match_report` - Incident analysis reports

### E2E Test Stack

```bash
docker compose -f docker-compose.test.yaml up -d
```

Starts Prometheus, Alertmanager, cAdvisor, Elasticsearch, and a memory-leak test app. The memory-leak app fills memory toward 86% of its 256M limit, triggering a `ContainerMemoryPressure` alert that flows through the full pipeline.

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `ANTHROPIC_API_KEY` | Yes | Anthropic API key |
| `ANTHROPIC_BASE_URL` | No | Override API base URL (default: `https://api.anthropic.com`) |
| `ANTHROPIC_MODEL_ID` | No | Override model (default: `claude-sonnet-4-20250514`) |
| `SRE_AGENT_CONFIG` | No | Explicit config file path |
| `SERVICENOW_INSTANCE_URL` | No | ServiceNow CMDB URL |
| `SERVICENOW_API_TOKEN` | No | ServiceNow API token |

## Key Entry Points

- **CLI app:** `src/sre_agent/cli.py` → `app` (Typer application)
- **Agent creation:** `src/sre_agent/agents/orchestrator.py` → `create_orchestrator()`
- **Config loading:** `src/sre_agent/config.py` → `load_settings()`
- **Pipeline server:** `src/sre_agent/pipeline/server.py` → FastAPI app
- **Package entry point:** `sre-agent` CLI command → `sre_agent.cli:app`

## Security Notes

- SSH commands are restricted by a whitelist (`configs/ssh_allowlist.yaml`)
- Shell metacharacters (`;`, `&&`, `|`, `>`, etc.) are blocked in SSH commands
- Runbook execution requires human approval via web UI (10-minute timeout)
- No write operations to monitored systems; all suggestions are advisory
- Never commit `.env`, `*.pem`, or `*.key` files (excluded in `.gitignore`)
