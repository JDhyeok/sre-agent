"""CLI entry point for the SRE Agent system."""

from __future__ import annotations

import json
import time
import warnings
from pathlib import Path
from typing import Annotated, Optional

warnings.filterwarnings("ignore", message="Pydantic serializer warnings")
warnings.filterwarnings("ignore", category=UserWarning, module="pydantic")

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text
from rich.table import Table

from sre_agent.config import load_settings, USER_CONFIG_DIR, USER_CONFIG_PATH

__version__ = "0.1.0"

app = typer.Typer(
    name="sre-agent",
    help="SRE Multi-Agent System for automated Root Cause Analysis",
    invoke_without_command=True,
)
console = Console()

_ANALYSIS_DONE_MARKERS = [
    "## RCA",
    "## Root Cause",
    "immediate_actions",
    "## Solution",
    "## 조치",
    "## 근본 원인",
    "## 분석 완료",
    "Analysis Complete",
]


def _looks_like_final_report(text: str) -> bool:
    return any(marker.lower() in text.lower() for marker in _ANALYSIS_DONE_MARKERS)


def _print_response(text: str) -> None:
    """Render agent response with Markdown formatting, CJK-safe."""
    console.print(Markdown(text), soft_wrap=True)


def _print_elapsed(seconds: float) -> None:
    """Print elapsed time right-aligned at the bottom of the terminal width."""
    label = f"{seconds:.1f}s"
    console.print(Text(label, style="dim"), justify="right")


# ---------------------------------------------------------------------------
# First-run setup
# ---------------------------------------------------------------------------

_DEFAULT_CONFIG_TEMPLATE = """\
anthropic:
  base_url: "{base_url}"
  model_id: "{model_id}"
  max_tokens: 4096

prometheus:
  url: "http://localhost:9090"
  alertmanager_url: "http://localhost:9093"
  default_step: "60s"
  baseline_window_hours: 24

elasticsearch:
  url: "http://localhost:9200"
  default_index: "app-logs-*"
  max_results: 500

ssh:
  timeout_seconds: 10
  hosts: []

servicenow:
  instance_url: ""

mcp_servers:
  prometheus:
    transport: "stdio"
  elasticsearch:
    transport: "stdio"
  ssh:
    transport: "stdio"
  servicenow_cmdb:
    transport: "stdio"
"""


def _run_first_setup() -> bool:
    """Interactive first-run setup. Returns True if setup completed."""
    console.print()
    console.print("[bold yellow]  First-time setup[/bold yellow]")
    console.print("[dim]  No configuration found. Let's set things up.[/dim]")
    console.print()

    try:
        api_key = console.input("  [bold]ANTHROPIC_API_KEY[/bold] [dim](or press Enter to skip)[/dim]: ").strip()
        base_url = console.input(
            "  [bold]ANTHROPIC_BASE_URL[/bold] [dim](press Enter for default: https://api.anthropic.com)[/dim]: "
        ).strip()
        model_id = console.input(
            "  [bold]Model ID[/bold] [dim](press Enter for default: claude-sonnet-4-20250514)[/dim]: "
        ).strip()
    except (KeyboardInterrupt, EOFError):
        console.print("\n[dim]  Setup cancelled.[/dim]")
        return False

    base_url = base_url or "https://api.anthropic.com"
    model_id = model_id or "claude-sonnet-4-20250514"

    USER_CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    config_content = _DEFAULT_CONFIG_TEMPLATE.format(base_url=base_url, model_id=model_id)
    USER_CONFIG_PATH.write_text(config_content)

    if api_key:
        env_file = USER_CONFIG_DIR / ".env"
        env_file.write_text(f"ANTHROPIC_API_KEY={api_key}\n")
        import os
        os.environ["ANTHROPIC_API_KEY"] = api_key
        console.print()
        console.print(f"[green]  Config saved:[/green] [dim]{USER_CONFIG_PATH}[/dim]")
        console.print(f"[green]  API key saved:[/green] [dim]{env_file}[/dim]")
    else:
        console.print()
        console.print(f"[green]  Config saved:[/green] [dim]{USER_CONFIG_PATH}[/dim]")
        console.print("[yellow]  API key skipped.[/yellow] Set it later:")
        console.print(f"[dim]    export ANTHROPIC_API_KEY=\"your-key\"[/dim]")
        console.print(f"[dim]    or add it to {USER_CONFIG_DIR / '.env'}[/dim]")

    console.print()
    return True


def _load_env_file() -> None:
    """Load .env file from user config dir if it exists."""
    import os
    env_file = USER_CONFIG_DIR / ".env"
    if env_file.is_file():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip("'\"")
            if key and key not in os.environ:
                os.environ[key] = value


# ---------------------------------------------------------------------------
# Welcome banner
# ---------------------------------------------------------------------------

def _print_welcome(settings) -> None:
    title = Text()
    title.append("  SRE Agent", style="bold red")
    title.append(f" v{__version__}", style="dim")

    console.print()
    console.print(Panel(title, border_style="red", expand=False, padding=(0, 1)))
    console.print()

    info = Table.grid(padding=(0, 2))
    info.add_column(style="bold", min_width=20)
    info.add_column()

    model_name = settings.anthropic.model_id.split("-")[0].title()
    model_label = f"{model_name} · {settings.anthropic.base_url.replace('https://', '').split('/')[0]}"
    info.add_row("  Model", model_label)

    sources: list[str] = []
    sources.append(f"Prometheus ({settings.prometheus.url})")
    sources.append(f"Elasticsearch ({settings.elasticsearch.url})")
    if settings.servicenow.instance_url:
        sources.append(f"CMDB ({settings.servicenow.instance_url})")
    if settings.ssh.hosts:
        sources.append(f"SSH ({len(settings.ssh.hosts)} hosts)")
    info.add_row("  Data Sources", ", ".join(sources) if sources else "None configured")

    api_key_status = "[green]set[/green]" if settings.anthropic.api_key else "[red]not set[/red]"
    info.add_row("  API Key", api_key_status)

    console.print(info)
    console.print()

    tips = Text()
    tips.append("  Tips: ", style="bold yellow")
    tips.append("Describe an incident to start analysis. ", style="dim")
    tips.append("Type ", style="dim")
    tips.append("/help", style="bold")
    tips.append(" for commands, ", style="dim")
    tips.append("Ctrl+C twice", style="bold")
    tips.append(" to exit.", style="dim")
    console.print(tips)
    console.print()


# ---------------------------------------------------------------------------
# Slash commands
# ---------------------------------------------------------------------------

def _handle_slash_command(cmd: str) -> bool:
    cmd = cmd.strip().lower()
    if cmd in ("/help", "/h", "/?"):
        console.print()
        help_table = Table(show_header=False, box=None, padding=(0, 2))
        help_table.add_column(style="bold cyan", min_width=16)
        help_table.add_column(style="dim")
        help_table.add_row("  /help", "Show this help")
        help_table.add_row("  /check", "Show current configuration")
        help_table.add_row("  /config", f"Open config: {USER_CONFIG_PATH}")
        help_table.add_row("  /clear", "Clear screen")
        help_table.add_row("  /quit", "Exit")
        console.print(help_table)
        console.print()
        return True
    if cmd in ("/quit", "/exit", "/q"):
        raise SystemExit(0)
    if cmd == "/clear":
        console.clear()
        return True
    if cmd == "/check":
        settings = load_settings(None)
        _print_check(settings)
        return True
    if cmd == "/config":
        console.print()
        console.print(f"  [bold]Config file:[/bold] [dim]{USER_CONFIG_PATH}[/dim]")
        console.print(f"  [bold]Env file:[/bold]    [dim]{USER_CONFIG_DIR / '.env'}[/dim]")
        console.print()
        return True
    return False


def _print_check(settings) -> None:
    console.print()
    tbl = Table(show_header=False, box=None, padding=(0, 2))
    tbl.add_column(style="bold", min_width=20)
    tbl.add_column()
    tbl.add_row("  Anthropic", f"{settings.anthropic.model_id} @ {settings.anthropic.base_url}")
    tbl.add_row("  Prometheus", settings.prometheus.url)
    tbl.add_row("  Alertmanager", settings.prometheus.alertmanager_url)
    tbl.add_row("  Elasticsearch", settings.elasticsearch.url)
    tbl.add_row("  ServiceNow", settings.servicenow.instance_url or "[dim]not configured[/dim]")
    tbl.add_row("  SSH Hosts", f"{len(settings.ssh.hosts)} configured")
    api = "[green]set[/green]" if settings.anthropic.api_key else "[red]NOT SET[/red]"
    tbl.add_row("  API Key", api)
    console.print(tbl)
    console.print()


# ---------------------------------------------------------------------------
# Input with double Ctrl+C to exit
# ---------------------------------------------------------------------------

_last_interrupt: float = 0.0


def _read_input() -> str:
    """Read user input. First Ctrl+C returns empty, second within 1.5s exits."""
    global _last_interrupt
    try:
        result = console.input("[bold red]>[/bold red] ")
        _last_interrupt = 0.0
        return result
    except KeyboardInterrupt:
        now = time.time()
        if _last_interrupt and (now - _last_interrupt) < 1.5:
            console.print("\n")
            raise SystemExit(0)
        _last_interrupt = now
        console.print("\n[dim]  Press Ctrl+C again to exit.[/dim]")
        return ""
    except EOFError:
        raise SystemExit(0)


# ---------------------------------------------------------------------------
# Main interactive mode
# ---------------------------------------------------------------------------

@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    config: Annotated[
        Optional[Path],
        typer.Option("--config", "-c", help="Path to settings YAML config"),
    ] = None,
    version: Annotated[
        bool,
        typer.Option("--version", "-v", help="Show version"),
    ] = False,
) -> None:
    """SRE Multi-Agent System for automated Root Cause Analysis."""
    if version:
        console.print(f"sre-agent v{__version__}")
        raise typer.Exit()

    if ctx.invoked_subcommand is not None:
        return

    _load_env_file()

    if config is None and not USER_CONFIG_PATH.is_file():
        has_env_key = bool(__import__("os").environ.get("ANTHROPIC_API_KEY"))
        has_local = Path("configs/settings.yaml").is_file()
        if not has_env_key and not has_local:
            _run_first_setup()

    settings = load_settings(config)
    _print_welcome(settings)

    if not settings.anthropic.api_key:
        console.print("[bold red]  Error:[/bold red] ANTHROPIC_API_KEY is not set.")
        console.print(f"[dim]  Run: export ANTHROPIC_API_KEY=\"your-key\"[/dim]")
        console.print(f"[dim]  Or:  edit {USER_CONFIG_DIR / '.env'}[/dim]")
        console.print()
        raise typer.Exit(1)

    from sre_agent.agents.orchestrator import create_orchestrator
    from sre_agent.callbacks import AgentProgressTracker

    tracker = AgentProgressTracker(console)

    console.print("[dim]  Initializing agents...[/dim]", end="")
    orchestrator = create_orchestrator(
        settings,
        callback_handler=tracker.get_orchestrator_handler(),
        tool_callback_handler=tracker.get_tool_handler(),
    )
    console.print("\r[dim]  Agents ready.          [/dim]")
    console.print()

    while True:
        user_input = _read_input()

        if not user_input.strip():
            continue

        if user_input.strip().startswith("/"):
            if _handle_slash_command(user_input):
                continue
            console.print(f"[dim]  Unknown command: {user_input.strip()}. Type /help[/dim]")
            console.print()
            continue

        tracker.reset()
        console.print()
        start = time.time()
        try:
            response = orchestrator(user_input)
        except KeyboardInterrupt:
            global _last_interrupt
            _last_interrupt = time.time()
            console.print("\n[dim]  Interrupted. Press Ctrl+C again to exit.[/dim]")
            console.print()
            continue
        except Exception as e:
            console.print()
            console.print(f"[bold red]  Error:[/bold red] {e}")
            console.print()
            continue
        elapsed = time.time() - start

        console.print()
        _print_response(str(response))
        _print_elapsed(elapsed)
        console.print()


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------

@app.command()
def analyze(
    incident: Annotated[
        str,
        typer.Argument(help="Incident description or context for analysis"),
    ],
    alert_json: Annotated[
        Optional[Path],
        typer.Option("--alert-json", "-a", help="Path to Alertmanager JSON payload"),
    ] = None,
    config: Annotated[
        Optional[Path],
        typer.Option("--config", "-c", help="Path to settings YAML config"),
    ] = None,
    no_interactive: Annotated[
        bool,
        typer.Option("--no-interactive", help="Skip interactive Q&A, analyze directly"),
    ] = False,
) -> None:
    """Analyze an incident using the multi-agent SRE system."""
    _load_env_file()
    settings = load_settings(config)

    incident_context = incident
    if alert_json and alert_json.exists():
        with open(alert_json) as f:
            alert_data = json.load(f)
        incident_context += f"\n\nAlertmanager Payload:\n{json.dumps(alert_data, indent=2)}"

    if not settings.anthropic.api_key:
        console.print("[bold red]  Error:[/bold red] ANTHROPIC_API_KEY is not set.")
        console.print("[dim]  Run: export ANTHROPIC_API_KEY=\"your-key\"[/dim]")
        raise typer.Exit(1)

    console.print()
    console.print(f"[bold red]  Incident:[/bold red] {incident_context}")
    console.print("[dim]  Initializing agents...[/dim]")
    console.print()

    from sre_agent.agents.orchestrator import create_orchestrator

    orchestrator = create_orchestrator(settings)

    if no_interactive:
        start = time.time()
        response = orchestrator(
            f"Investigate the following incident and produce a complete RCA report. "
            f"Do NOT ask questions - use your best judgment with available info:\n\n{incident_context}"
        )
        elapsed = time.time() - start
        _print_response(str(response))
        _print_elapsed(elapsed)
    else:
        _run_interactive_analysis(orchestrator, incident_context)


def _run_interactive_analysis(orchestrator, initial_message: str) -> None:
    start = time.time()
    turn = 0
    max_turns = 10

    response = orchestrator(initial_message)
    response_text = str(response)
    turn += 1

    while turn < max_turns:
        console.print()
        _print_response(response_text)

        if _looks_like_final_report(response_text):
            break

        console.print()
        user_input = _read_input()

        if not user_input.strip():
            user_input = "제공된 정보로 분석을 진행해주세요."

        response = orchestrator(user_input)
        response_text = str(response)
        turn += 1

    elapsed = time.time() - start
    _print_elapsed(elapsed)
    console.print()

    if not _looks_like_final_report(response_text):
        _print_response(response_text)


@app.command()
def check(
    config: Annotated[
        Optional[Path],
        typer.Option("--config", "-c", help="Path to settings YAML config"),
    ] = None,
) -> None:
    """Validate configuration and check connectivity to data sources."""
    _load_env_file()
    settings = load_settings(config)
    _print_check(settings)


@app.command()
def webhook(
    host: Annotated[str, typer.Option("--host", "-h", help="Host to bind to")] = "0.0.0.0",
    port: Annotated[int, typer.Option("--port", "-p", help="Port to bind to")] = 8080,
    config: Annotated[
        Optional[Path],
        typer.Option("--config", "-c", help="Path to settings YAML config"),
    ] = None,
) -> None:
    """Start the Alertmanager webhook server."""
    try:
        import uvicorn
    except ImportError:
        console.print("[red]  Webhook requires uvicorn. Install with: pip install sre-agent[webhook][/red]")
        raise typer.Exit(1)

    _load_env_file()
    from sre_agent.integrations.webhook import create_webhook_app

    console.print()
    console.print("[bold red]  SRE Agent Webhook Server[/bold red]")
    console.print(f"[dim]  Listening on {host}:{port}[/dim]")
    console.print("[dim]  POST /webhook/alertmanager · GET /health[/dim]")
    console.print()

    webhook_app = create_webhook_app()
    uvicorn.run(webhook_app, host=host, port=port)


@app.command()
def kb_search(
    query: Annotated[str, typer.Argument(help="Search query for past incidents")],
    top_k: Annotated[int, typer.Option("--top", "-k", help="Number of results")] = 5,
) -> None:
    """Search the incident knowledge base for similar past incidents."""
    from sre_agent.integrations.knowledge_base import IncidentKB

    kb = IncidentKB()
    results = kb.search(query, top_k=top_k)

    if not results:
        console.print("[dim]  No matching incidents found.[/dim]")
        return

    console.print()
    console.print(f"[bold]  {len(results)} similar incidents[/bold]")
    console.print()
    for i, inc in enumerate(results, 1):
        summary = inc.get("incident_summary", inc.get("incident_context", ""))
        console.print(f"  [bold]{i}.[/bold] {inc.get('id', 'unknown')} [dim]{inc.get('stored_at', '')}[/dim]")
        console.print(f"     {summary[:200]}")
        if inc.get("primary_root_cause"):
            console.print(f"     [dim]Root Cause: {inc['primary_root_cause'][:200]}[/dim]")
        console.print()


@app.command()
def kb_list(
    count: Annotated[int, typer.Option("--count", "-n", help="Number of recent incidents")] = 10,
) -> None:
    """List recent incidents from the knowledge base."""
    from sre_agent.integrations.knowledge_base import IncidentKB

    kb = IncidentKB()
    results = kb.list_recent(n=count)

    if not results:
        console.print("[dim]  No incidents in knowledge base.[/dim]")
        return

    console.print()
    console.print(f"[bold]  Recent {len(results)} incidents[/bold]")
    console.print()
    for inc in results:
        inc_id = inc.get("id", "unknown")
        stored = inc.get("stored_at", "")
        summary = inc.get("incident_summary", inc.get("incident_context", ""))[:120]
        console.print(f"  {inc_id} [dim]{stored}[/dim] {summary}")


if __name__ == "__main__":
    app()
