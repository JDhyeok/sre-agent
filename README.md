# SRE Multi-Agent System

Automated Root Cause Analysis (RCA) system using [Strands Agents SDK](https://strandsagents.com/) with the **Agents as Tools** pattern. The system coordinates specialist agents to collect data from Prometheus, Elasticsearch, and remote servers via SSH, then performs structured RCA and suggests remediation actions.

## Architecture

```
sre-agent (Interactive CLI)
  └─→ Orchestrator Agent
        ├─→ Data Collector Agent ──→ Prometheus MCP Server ──→ Prometheus API
        │                        ──→ Elasticsearch MCP Server ──→ Elasticsearch API
        │                        ──→ ServiceNow CMDB MCP Server ──→ CMDB API
        ├─→ SSH Agent ──→ SSH MCP Server ──→ Target Servers (read-only)
        ├─→ RCA Agent (5-Phase Framework, pure reasoning)
        └─→ Solution Agent (remediation suggestions)
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

With all optional dependencies (SSH):

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

```bash
sre-agent
```

Type your incident description at the `>` prompt. The system shows real-time progress as agents work:

```
> API 서버 500 에러 급증, service: payment-api

  → Data Collector (3.2s)
    ↳ query_instant
    ↳ get_active_alerts
  → Root Cause Analysis (18.5s)
  → Solution (5.1s)

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

## Agent Overview

| Agent | Role | MCP Servers | Default Tokens |
|-------|------|-------------|----------------|
| **Orchestrator** | Coordinates agents, synthesizes final Markdown report | All agents (as tools) | 8192 |
| **Data Collector** | Unified metrics + logs + topology investigation (top-down L1→L6) | Prometheus, Elasticsearch, ServiceNow CMDB | 4096 |
| **SSH** | Read-only server diagnostics (processes, network, disk, services) | SSH | 2048 |
| **RCA** | 5-Phase root cause analysis (Triage → Timeline → Correlation → Root Cause → Verification) | None (pure reasoning) | 8192 |
| **Solution** | Remediation recommendations (immediate / short-term / long-term) | None (pure reasoning) | 4096 |

## Security

- **SSH Whitelist**: Only pre-approved read-only commands can be executed. See `configs/ssh_allowlist.yaml`.
- **No Write Operations**: No agent can modify infrastructure. All actions are suggestions only.
- **Command Injection Prevention**: Shell metacharacters (`;`, `&&`, `|`, `>`, etc.) are blocked.
- **Human-in-the-Loop**: All remediation actions require human approval and execution.

## Project Structure

```
src/sre_agent/
├── cli.py                  # Interactive CLI (Typer + Rich)
├── callbacks.py            # Real-time agent/tool progress display
├── config.py               # Configuration management (Pydantic)
├── model.py                # LLM model setup (per-agent max_tokens)
├── agents/                 # Specialist agents
│   ├── orchestrator.py     # Coordinates all agents
│   ├── data_collector.py   # Unified metrics + logs + topology
│   ├── ssh.py              # System diagnostics
│   ├── rca.py              # Root cause analysis (5-Phase)
│   └── solution.py         # Remediation suggestions
├── mcp_servers/            # FastMCP tool servers
│   ├── prometheus_server.py
│   ├── elasticsearch_server.py
│   ├── servicenow_cmdb_server.py
│   └── ssh_server.py
├── prompts/                # System prompts for each agent
├── schemas/                # Pydantic models for structured output
├── defaults/               # Bundled default settings.yaml
└── integrations/           # External integrations
    ├── webhook.py          # Alertmanager webhook (FastAPI)
    ├── knowledge_base.py   # Incident KB storage/search
    └── otel.py             # OpenTelemetry tracing
```

## References

- [Strands Agents SDK - Agents as Tools](https://strandsagents.com/docs/user-guide/concepts/multi-agent/agents-as-tools/)
- [Samsung Account AIOps Case Study](https://aws.amazon.com/ko/blogs/tech/part2-agentic-aiops-samsung-account-service/)
- [FastMCP](https://github.com/jlowin/fastmcp)
