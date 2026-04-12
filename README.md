# SRE Multi-Agent System

Automated Root Cause Analysis (RCA) system using [Strands Agents SDK](https://strandsagents.com/) with the **Agents as Tools** pattern. The system coordinates specialist agents to collect data from Prometheus, Elasticsearch, and remote servers via SSH, then performs structured RCA, suggests remediation actions, and matches runbooks for automated execution.

## Architecture

### Interactive CLI

```
sre-agent (Interactive CLI)
  └─→ Orchestrator Agent
        ├─→ Data Collector Agent ──→ Prometheus MCP Server ──→ Prometheus API
        │                        ──→ Elasticsearch MCP Server ──→ Elasticsearch API
        │                        ──→ ServiceNow CMDB MCP Server ──→ CMDB API
        ├─→ SSH Agent ──→ SSH MCP Server ──→ Target Servers (read-only)
        ├─→ RCA Agent (5-Phase Framework, pure reasoning)
        ├─→ Solution Agent (remediation suggestions)
        └─→ Runbook Matcher Agent ──→ runbooks/ (Markdown runbooks)
```

### Automated Pipeline (`sre-agent serve`)

```
Alertmanager / Generic Webhook
  └─→ Intake (dedup, severity routing, grouping)
        └─→ Analyzer (orchestrator invocation)
              └─→ Delivery (Teams notification + approval link)
                    └─→ Approval Gateway (web UI)
                          └─→ Executor (SSH-based runbook execution)
```

Each specialist agent is registered as a tool via `.as_tool()`. The **Data Collector** agent unifies metrics, logs, and topology into a single top-down investigation (L1 Symptom → L6 Platform). The **RCA Agent** applies a 5-Phase Framework: Triage → Timeline → Correlation → Root Cause (5 Whys) → Verification.

## Quick Start

```bash
pip install sre-agent
sre-agent
```

On first run, an interactive setup wizard creates `~/.config/sre-agent/settings.yaml` and prompts for your API key. After setup, just run `sre-agent` to start.

## Installation

### From PyPI (after publishing)

```bash
pip install sre-agent
```

With all optional dependencies (SSH, webhook server):

```bash
pip install "sre-agent[all]"
```

### From GitHub

```bash
pip install git+https://github.com/JDhyeok/sre-agent.git
```

### From source (development)

```bash
git clone https://github.com/JDhyeok/sre-agent.git
cd sre-agent
pip install -e ".[all,dev]"
```

### Using pipx (isolated global install)

```bash
pipx install git+https://github.com/JDhyeok/sre-agent.git
```

After installation, the `sre-agent` command is available globally.

## Configuration

Configuration is stored in `~/.config/sre-agent/settings.yaml` (created automatically on first run).

### API Key

Set via environment variable or the config `.env` file:

```bash
# Option 1: Environment variable
export ANTHROPIC_API_KEY="your-api-key"

# Option 2: Config file (created by setup wizard)
echo 'ANTHROPIC_API_KEY=your-api-key' > ~/.config/sre-agent/.env
```

### Custom API Endpoint

For internal Anthropic proxies, set in `~/.config/sre-agent/settings.yaml`:

```yaml
anthropic:
  base_url: "https://your-internal-proxy.example.com/v1"
```

Or via environment variable:

```bash
export ANTHROPIC_BASE_URL="https://your-internal-proxy.example.com/v1"
```

### Per-Agent Token Limits

Each agent can have its own `max_tokens` setting in `settings.yaml`:

```yaml
agent_tokens:
  orchestrator: 8192
  data_collector: 4096
  ssh: 2048
  rca: 8192       # Higher for reasoning-heavy analysis
  solution: 4096
```

### Data Sources

Edit `~/.config/sre-agent/settings.yaml` to configure Prometheus, Elasticsearch, SSH hosts, and ServiceNow CMDB connections.

## Usage

### Interactive CLI

```bash
sre-agent
```

Type your incident description at the `>` prompt. The system shows real-time progress as agents work:

```
> API 서버 500 에러 급증, service: payment-api

  → Data Collector
    ↳ query_instant
    ↳ get_active_alerts
  ✓ Data Collector (3.2s)
  → Root Cause Analysis
  ✓ Root Cause Analysis (18.5s)
  → Solution
  ✓ Solution (5.1s)
  → Runbook Matcher
    ↳ list_runbooks
    ↳ get_runbook
  ✓ Runbook Matcher (2.8s)

  ## 인시던트 분석 리포트
  ...
                                                                        32.4s
```

### Slash Commands

| Command | Description |
|---------|-------------|
| `/help` | Show available commands |
| `/check` | Display current configuration |
| `/config` | Show config file paths |
| `/clear` | Clear screen |
| `/quit` | Exit |

`Ctrl+C` once cancels the current analysis. `Ctrl+C` twice exits the program.

### Automated Pipeline

```bash
sre-agent serve --port 8080
```

Endpoints:

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/webhook/alertmanager` | Alertmanager webhook receiver |
| `POST` | `/webhook/generic` | Generic webhook (custom JSON) |
| `GET` | `/incidents` | Recent incidents list |
| `GET` | `/incidents/{id}` | Incident detail |
| `GET` | `/approve/{id}` | Approval web UI |
| `GET` | `/health` | Health check |

## Agent Overview

| Agent | Role | Tools / MCP Servers | Default Tokens |
|-------|------|---------------------|----------------|
| **Orchestrator** | Coordinates agents, synthesizes final Markdown report | All agents (as tools) | 8192 |
| **Data Collector** | Unified metrics + logs + topology investigation (top-down L1→L6) | Prometheus, Elasticsearch, ServiceNow CMDB | 4096 |
| **SSH** | Read-only server diagnostics (processes, network, disk, services) | SSH | 2048 |
| **RCA** | 5-Phase root cause analysis (Triage → Timeline → Correlation → Root Cause → Verification) | None (pure reasoning) | 8192 |
| **Solution** | Remediation recommendations (immediate / short-term / long-term) | None (pure reasoning) | 4096 |
| **Runbook Matcher** | Matches solution to Markdown runbooks for automated execution | `list_runbooks`, `get_runbook` (in-process) | 4096 |

## Security

- **SSH Whitelist**: Only pre-approved read-only commands can be executed. See `configs/ssh_allowlist.yaml`.
- **No Write Operations**: No agent can modify infrastructure. All actions are suggestions only.
- **Command Injection Prevention**: Shell metacharacters (`;`, `&&`, `|`, `>`, etc.) are blocked.
- **Human-in-the-Loop**: Runbook execution requires human approval via the web UI with a 10-minute timeout.

## Project Structure

```
src/sre_agent/
├── cli.py                  # Interactive CLI (Typer + Rich) + serve command
├── callbacks.py            # Progress display (CLI: Rich, Pipeline: logging)
├── config.py               # Configuration management (Pydantic)
├── model.py                # LLM model setup (per-agent max_tokens)
├── agents/                 # Specialist agents
│   ├── orchestrator.py     # Coordinates all agents
│   ├── data_collector.py   # Unified metrics + logs + topology
│   ├── ssh.py              # System diagnostics
│   ├── rca.py              # Root cause analysis (5-Phase)
│   ├── solution.py         # Remediation suggestions
│   └── operator.py         # Runbook matcher
├── mcp_servers/            # FastMCP tool servers (stdio subprocesses)
│   ├── prometheus_server.py
│   ├── elasticsearch_server.py
│   ├── servicenow_cmdb_server.py
│   └── ssh_server.py
├── prompts/                # System prompts for each agent
├── pipeline/               # Automated pipeline (sre-agent serve)
│   ├── server.py           # FastAPI application
│   ├── intake.py           # Dedup, severity routing, alert grouping
│   ├── analyzer.py         # Orchestrator wrapper with analysis levels
│   ├── approval.py         # Approval web UI + SSH runbook execution
│   └── delivery.py         # Teams webhook notifications
├── runbooks/               # Markdown runbooks for automated remediation
│   ├── redis-restart.md    # Example runbook (Redis)
│   ├── memory-leak-restart.md  # Memory pressure → docker restart (test stack)
│   └── scripts/            # Shell scripts referenced by runbooks
├── tools/                  # In-process Strands tools
│   └── runbook.py          # list_runbooks, get_runbook
├── templates/              # Jinja2 HTML templates
│   └── approval.html       # Approval web UI
└── defaults/               # Bundled default configs (shipped in wheel)
    ├── settings.yaml
    └── ssh_allowlist.yaml
```

## Test Environment

A Docker Compose test stack is provided for local development:

```bash
docker compose -f docker-compose.test.yaml up -d
```

**Services:** Prometheus, Alertmanager, cAdvisor, `memory-leak-app` (slow cgroup memory fill toward ~90% working set), Elasticsearch, and a small log generator for Data Collector tests.

### Memory-leak scenario (E2E)

1. Bring the stack up (above). `memory-leak-app` runs `configs/memory-leak-daemon.py` and gradually increases memory until cAdvisor shows high working-set ratio vs the **256Mi** cgroup limit.
2. After **~2–4 minutes**, open Prometheus **Alerts** — `ContainerMemoryPressure` fires when working set exceeds **85%** of the limit for **60s** (rules are baked into the Prometheus container at start; after editing compose, recreate Prometheus: `docker compose -f docker-compose.test.yaml up -d --force-recreate prometheus memory-leak-app`).
3. Alertmanager POSTs to `http://host.docker.internal:8080/webhook/alertmanager` when `sre-agent serve` is running on the host with API key configured.
4. **Runbook:** `memory-leak-restart` (`src/sre_agent/runbooks/memory-leak-restart.md`) matches container memory pressure + periodic leak narrative; the script `scripts/restart-memory-leak-app.sh` runs `docker restart sre-memory-leak-app` on the SSH target host.
5. For **approval / SSH execution**, configure `ssh.hosts` in settings so the pipeline can SSH to the machine that runs Docker (often `127.0.0.1` on Linux; use your user and key). See commented example in `configs/settings.yaml`.

To reset the workload manually: `docker restart sre-memory-leak-app`.

## References

- [Strands Agents SDK - Agents as Tools](https://strandsagents.com/docs/user-guide/concepts/multi-agent/agents-as-tools/)
- [Samsung Account AIOps Case Study](https://aws.amazon.com/ko/blogs/tech/part2-agentic-aiops-samsung-account-service/)
- [FastMCP](https://github.com/jlowin/fastmcp)
