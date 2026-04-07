# SRE Multi-Agent System

Automated Root Cause Analysis (RCA) system using [Strands Agents SDK](https://strandsagents.com/) with the **Agents as Tools** pattern. The system coordinates specialist agents to collect data from Prometheus, Elasticsearch, and remote servers via SSH, then performs structured RCA and suggests remediation actions.

## Architecture

```
Trigger (CLI / Alertmanager Webhook)
  └─→ Orchestrator Agent
        ├─→ Data Collector Agent ──→ Prometheus MCP Server ──→ Prometheus API
        │                        ──→ Elasticsearch MCP Server ──→ Elasticsearch API
        │                        ──→ ServiceNow CMDB MCP Server ──→ CMDB API
        ├─→ SSH Agent ──→ SSH MCP Server ──→ Target Servers (read-only)
        ├─→ RCA Agent (5-Phase Framework, pure reasoning)
        └─→ Solution Agent (remediation suggestions)
```

Each specialist agent is registered as a tool via `.as_tool()`. The **Data Collector** agent unifies metrics, logs, and topology into a single top-down investigation (L1 Symptom → L6 Platform). The **RCA Agent** applies a 5-Phase Framework: Triage → Timeline → Correlation → Root Cause (5 Whys) → Verification.

## Installation

### From PyPI (after publishing)

```bash
pip install sre-agent
```

With all optional dependencies:

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

1. Set your API key:

```bash
export ANTHROPIC_API_KEY="your-api-key"
```

2. (Optional) Override Anthropic endpoint for internal proxies:

```bash
export ANTHROPIC_BASE_URL="https://your-internal-proxy.example.com/v1"
```

3. (Optional) Customize data source connections by editing `configs/settings.yaml`.

## Usage

**Interactive Chat:**

```bash
sre-agent chat
```

**Incident Analysis (with interactive Q&A):**

```bash
sre-agent analyze "API server returning 5xx errors, service: payment-api"
sre-agent analyze --alert-json alert_payload.json "High error rate detected"
sre-agent analyze --no-interactive "OOM killed on app-server-1"
```

**Configuration Check:**

```bash
sre-agent check
```

**Alertmanager Webhook:**

```bash
sre-agent webhook --port 8080
```

**Knowledge Base:**

```bash
sre-agent kb-search "payment service timeout"
sre-agent kb-list --count 10
```

## Agent Overview

| Agent | Role | MCP Servers |
|-------|------|-------------|
| **Orchestrator** | Coordinates all agents, manages investigation workflow | All agents (as tools) |
| **Data Collector** | Unified metrics + logs + topology investigation (top-down L1-L6) | Prometheus, Elasticsearch, ServiceNow CMDB |
| **SSH** | Read-only server diagnostics (processes, network, disk, services) | SSH |
| **RCA** | 5-Phase root cause analysis (Triage → Timeline → Correlation → Root Cause → Verification) | None (pure reasoning) |
| **Solution** | Remediation recommendations (immediate / short-term / long-term) | None (pure reasoning) |

## Security

- **SSH Whitelist**: Only pre-approved read-only commands can be executed. See `configs/ssh_allowlist.yaml`.
- **No Write Operations**: No agent can modify infrastructure. All actions are suggestions only.
- **Command Injection Prevention**: Shell metacharacters (`;`, `&&`, `|`, `>`, etc.) are blocked.
- **Human-in-the-Loop**: All remediation actions require human approval and execution.

## Project Structure

```
src/sre_agent/
├── cli.py                  # CLI entry point (Typer)
├── config.py               # Configuration management (Pydantic)
├── model.py                # LLM model setup (Anthropic with custom base_url)
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
└── integrations/           # External integrations
    ├── webhook.py          # Alertmanager webhook (FastAPI)
    ├── knowledge_base.py   # Incident KB storage/search
    └── otel.py             # OpenTelemetry tracing
```

## References

- [Strands Agents SDK - Agents as Tools](https://strandsagents.com/docs/user-guide/concepts/multi-agent/agents-as-tools/)
- [Samsung Account AIOps Case Study](https://aws.amazon.com/ko/blogs/tech/part2-agentic-aiops-samsung-account-service/)
- [FastMCP](https://github.com/jlowin/fastmcp)
