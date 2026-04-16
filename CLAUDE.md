# CLAUDE.md - SRE Multi-Agent System

## Project Overview

SRE Multi-Agent System for automated Root Cause Analysis (RCA). Uses the **Strands Agents SDK** with an "Agents as Tools" pattern to coordinate specialist AI agents that collect observability data, perform root cause analysis, and suggest/execute remediation via runbooks.

**Two operating modes:**
- **Interactive CLI** (`sre-agent`) - Real-time incident investigation with REPL and slash commands
- **Automated Pipeline** (`sre-agent serve`) - Webhook-driven incident response with two-phase analysis and human approval workflow

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
| Publish to PyPI | `make publish` |
| Publish to TestPyPI | `make publish-test` |
| Start test stack | `docker compose -f docker-compose.test.yaml up -d` |

## Tech Stack

- **Python 3.11+** (supports 3.11, 3.12, 3.13)
- **Strands Agents SDK** (`strands-agents[anthropic]`, `strands-agents-tools`) - Multi-agent orchestration
- **FastMCP** + **mcp[cli]** - MCP tool servers (stdio subprocess transport)
- **Typer + Rich** - CLI framework and terminal rendering
- **FastAPI + Uvicorn** - Pipeline webhook server (optional `webhook` extra)
- **Jinja2 + Markdown** - Approval web UI rendering (optional `webhook` extra)
- **Paramiko** - SSH operations (optional `ssh` extra)
- **httpx** - Async/sync HTTP client (Teams webhooks, MCP servers)
- **Pydantic v2 + pydantic-settings** - Configuration and validation
- **PyYAML** - Config file loading
- **Hatchling** - Build system
- **Ruff** - Linter and formatter
- **pytest + pytest-asyncio** - Test framework

## Repository Structure

```
.env.example                # Environment variable template
Makefile                    # Build, lint, format, publish targets
pyproject.toml              # Package metadata, dependencies, ruff config
docker-compose.test.yaml    # E2E test stack

src/sre_agent/
├── __init__.py
├── cli.py                  # Entry point: interactive CLI + serve command
├── config.py               # Pydantic settings, YAML loading, env var overlays
├── model.py                # LLM model setup (AnthropicModel wrapper)
├── callbacks.py            # Progress display (AgentProgressTracker for CLI,
│                           #   LoggingProgressTracker for pipeline)
├── agents/                 # Specialist agents (Strands Agent instances)
│   ├── orchestrator.py     # Master orchestrator, creates all sub-agents
│   ├── phase_a_orchestrator.py  # Phase A: data collection + runbook matching
│   ├── phase_b_orchestrator.py  # Phase B: RCA + solution (on-demand)
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
│   ├── orchestrator.py     # Master orchestrator prompt
│   ├── data_collector.py   # Data collector prompt
│   ├── operator.py         # Runbook matcher prompt
│   ├── rca.py              # Root cause analysis prompt
│   ├── ssh.py              # SSH agent prompt
│   ├── solution.py         # Solution agent prompt
│   ├── phase_a.py          # Phase A orchestrator prompt
│   └── phase_b.py          # Phase B orchestrator prompt
├── pipeline/               # Automated pipeline (FastAPI)
│   ├── server.py           # FastAPI app with webhook + incident endpoints
│   ├── intake.py           # Alert dedup, severity routing, alert grouping
│   ├── analyzer.py         # Two-phase orchestration (A auto → B on-demand)
│   ├── delivery.py         # Teams webhook notifications (MessageCard format)
│   └── approval.py         # Web UI + SSH-based runbook execution
├── tools/                  # In-process Strands tools
│   └── runbook.py          # Runbook listing, loading, and matching
├── runbooks/               # Markdown runbooks (only _template.md tracked)
│   └── _template.md        # Runbook authoring template
├── templates/              # Jinja2 HTML templates
│   └── approval.html       # Approval/incident detail web UI
└── defaults/               # Bundled config defaults (shipped in wheel)
    ├── settings.yaml
    └── ssh_allowlist.yaml

tests/
├── conftest.py             # Shared fixtures (alert payloads, runbook samples)
└── unit/
    ├── test_config.py      # Configuration loading
    ├── test_callbacks.py   # Progress tracking
    ├── test_intake.py      # Alert deduplication and severity routing
    ├── test_analyzer.py    # Pipeline analysis phases
    ├── test_approval.py    # Approval workflow and runbook execution
    ├── test_runbook.py     # Runbook loading and matching
    └── mcp_servers/        # MCP server unit tests
        ├── test_prometheus.py
        ├── test_elasticsearch.py
        └── test_ssh_diagnostic.py

configs/                    # Development configuration
├── settings.yaml           # Dev settings (data source URLs, SSH hosts, etc.)
├── ssh_allowlist.yaml      # SSH command whitelist
└── memory-leak-daemon.py   # Test workload for E2E stack
```

## Architecture

### Agent Hierarchy

The system uses "Agents as Tools" - each specialist agent is registered as a callable tool on its parent orchestrator:

```
Orchestrator (master) — interactive CLI mode
├── Data Collector Agent → Prometheus, Elasticsearch, ServiceNow CMDB,
│                          SSH diagnostics (via MCP, read-only)
├── SSH Agent → Target servers (via MCP, operational commands only)
├── RCA Agent → 5-phase root cause analysis (reasoning only, no tools)
├── Solution Agent → Remediation recommendations (reasoning only, no tools)
└── Runbook Matcher Agent → list_runbooks, get_runbook tools

Phase A Orchestrator — pipeline mode (auto)
├── Data Collector Agent
└── Runbook Matcher Agent

Phase B Orchestrator — pipeline mode (on-demand, after approval)
├── RCA Agent
└── Solution Agent
```

### Pipeline Flow (Automated Mode)

```
Webhook → Intake (dedup/severity routing/grouping)
  → Phase A (data collection + runbook matching) [automatic]
    → If runbook matched: Teams notification with approval link
      → Human approves via web UI → SSH runbook execution
    → If no match: Teams notification (report only)
  → Phase B (RCA + solution) [on-demand, triggered from approval UI]
    → Teams notification with detailed RCA report
```

### Pipeline Server Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/webhook/alertmanager` | Alertmanager webhook receiver |
| POST | `/webhook/generic` | Generic alert webhook |
| GET | `/health` | Health check |
| GET | `/incidents` | List recent incidents |
| GET | `/incidents/{id}` | Incident detail |
| GET | `/approve/{id}` | Approval web UI (HTML) |
| POST | `/approve/{id}` | Handle approval/rejection/RCA actions |

### Configuration

#### Config File Resolution

Settings are loaded from a **single** config file — the first match from this priority list:

1. `SRE_AGENT_CONFIG` env var (explicit path, highest priority)
2. `configs/settings.yaml` or `configs/settings.yml` (local project config)
3. `~/.config/sre-agent/settings.yaml` (user config)
4. Bundled defaults (`src/sre_agent/defaults/settings.yaml`)

After the config file is loaded, these environment variables **overlay** specific fields:
- `ANTHROPIC_API_KEY` → `anthropic.api_key`
- `ANTHROPIC_BASE_URL` → `anthropic.base_url`
- `ANTHROPIC_MODEL_ID` → `anthropic.model_id`

Additionally, the `SRE_AGENT_` prefix with `__` delimiter supports arbitrary nested settings via pydantic-settings (e.g., `SRE_AGENT_PROMETHEUS__URL`).

#### Config Sections

| Section | Model Class | Description |
|---------|-------------|-------------|
| `anthropic` | `AnthropicConfig` | API key, base URL, model ID, max_tokens |
| `agent_tokens` | `AgentTokenLimits` | Per-agent max_tokens overrides |
| `prometheus` | `PrometheusConfig` | Prometheus/Alertmanager URLs, query defaults |
| `elasticsearch` | `ElasticsearchConfig` | ES URL, default index, max results |
| `ssh` | `SSHConfig` | SSH timeout, host list |
| `servicenow` | `ServiceNowConfig` | ServiceNow CMDB instance URL |
| `hmg_apm` | `HmgApmConfig` | APM URL, API key, timeout |
| `intake` | `IntakeConfig` | Dedup window, group window, severity routing |
| `delivery` | `DeliveryConfig` | Teams webhook URL, public base URL |
| `mcp_servers` | `MCPServersConfig` | Transport config per MCP server |

### CLI Interactive Commands

| Command | Description |
|---------|-------------|
| `/help` | Show available commands |
| `/check` | Show current configuration and connection status |
| `/config` | Show config file paths |
| `/clear` | Clear screen |
| `/quit` | Exit (also: Ctrl+C twice within 1.5s) |

### CLI Flags

- `sre-agent --config/-c <path>` — Specify settings YAML
- `sre-agent --version/-v` — Show version
- `sre-agent serve --port/-p <port>` — Server port (default: 8080)
- `sre-agent serve --host/-h <host>` — Server host (default: 0.0.0.0)
- `sre-agent serve --config/-c <path>` — Specify settings YAML

## Code Style and Conventions

- **Line length:** 120 characters
- **Formatter/Linter:** Ruff (target Python 3.11)
- **Always run `make lint` before committing** to check for issues
- **Run `make format` to auto-fix** style issues
- **No pre-commit hooks** are configured; lint manually
- Config models use **Pydantic v2 BaseModel/BaseSettings** with typed defaults
- Each agent has a **dedicated system prompt module** in `src/sre_agent/prompts/`
- Each agent is created via a `create_<name>_agent()` factory function
- MCP servers use **FastMCP** with `@mcp.tool()` decorators
- Sync HTTP uses **`httpx.Client`**, async HTTP uses **`httpx.AsyncClient`**
- Tests use **`pytest-asyncio`** with async fixtures
- Pipeline modules that define FastAPI endpoints do **NOT** use `from __future__ import annotations` (PEP 563 breaks FastAPI's type hint resolution for endpoints in closures)
- UI text and prompts are in **Korean** (Korean is the primary user-facing language)

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
- `sample_alertmanager_payload` — Mock Alertmanager webhook (version 4)
- `sample_runbook_markdown` — Runbook with YAML frontmatter
- `sample_match_found_report` / `sample_no_match_report` — Incident analysis reports (Korean)

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
| `SERVICENOW_USERNAME` | No | ServiceNow basic auth username |
| `SERVICENOW_PASSWORD` | No | ServiceNow basic auth password |
| `SRE_AGENT_<SECTION>__<KEY>` | No | Override any nested config via pydantic-settings |

## Key Entry Points

- **CLI app:** `src/sre_agent/cli.py` → `app` (Typer application)
- **Agent creation:** `src/sre_agent/agents/orchestrator.py` → `create_orchestrator()`
- **Phase A agent:** `src/sre_agent/agents/phase_a_orchestrator.py` → `create_phase_a_orchestrator()`
- **Phase B agent:** `src/sre_agent/agents/phase_b_orchestrator.py` → `create_phase_b_orchestrator()`
- **Config loading:** `src/sre_agent/config.py` → `load_settings()`
- **Pipeline server:** `src/sre_agent/pipeline/server.py` → `create_pipeline_app()`
- **Package entry point:** `sre-agent` CLI command → `sre_agent.cli:app`

## Security Notes

- SSH commands are restricted by a whitelist (`configs/ssh_allowlist.yaml`)
- Shell metacharacters (`;`, `&&`, `|`, `>`, etc.) are blocked in SSH commands
- Runbook execution requires human approval via web UI (10-minute timeout)
- Runbook scripts are piped via `ssh host bash -s` (bypasses MCP allowlist intentionally, as this is a post-approval mutating path)
- No write operations to monitored systems outside approved runbooks; all other suggestions are advisory
- Never commit `.env`, `*.pem`, or `*.key` files (excluded in `.gitignore`)
- First-run setup stores API key in `~/.config/sre-agent/.env`, not in the config YAML
