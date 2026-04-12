"""CLI entry point for the SRE Agent system."""

from __future__ import annotations

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


def _print_response(text: str) -> None:
    """Render agent response with Markdown formatting, CJK-safe."""
    console.print(Markdown(text), soft_wrap=True)


def _print_elapsed(seconds: float) -> None:
    """Print elapsed time right-aligned at the bottom of the terminal width."""
    console.print(Text(f"{seconds:.1f}s", style="dim"), justify="right")


# ---------------------------------------------------------------------------
# Error hints
# ---------------------------------------------------------------------------

_ERROR_HINTS: list[tuple[str, str]] = [
    ("Could not resolve authentication", "ANTHROPIC_API_KEY가 설정되지 않았거나 만료되었습니다.\n  → export ANTHROPIC_API_KEY=\"your-key\""),
    ("api_key", "API key 관련 오류입니다.\n  → export ANTHROPIC_API_KEY=\"your-key\""),
    ("Connection refused", "데이터 소스에 연결할 수 없습니다.\n  → /check 로 연결 설정을 확인하세요."),
    ("Connection error", "네트워크 연결 오류입니다.\n  → 대상 서버가 실행 중인지 확인하세요."),
    ("timed out", "요청 시간이 초과되었습니다.\n  → 데이터 소스 응답 상태를 확인하세요."),
    ("rate limit", "API 호출 한도를 초과했습니다.\n  → 잠시 후 다시 시도하세요."),
    ("401", "인증 실패입니다. API key를 확인하세요."),
    ("403", "권한이 없습니다. API key 권한을 확인하세요."),
    ("404", "요청한 리소스를 찾을 수 없습니다."),
    ("500", "서버 내부 오류입니다. 잠시 후 다시 시도하세요."),
    ("MCP", "MCP 서버 통신 오류입니다.\n  → MCP 서버 프로세스 상태를 확인하세요."),
]


def _print_error(exc: Exception) -> None:
    """Print error with actionable hint if a known pattern matches."""
    msg = str(exc)
    console.print()
    console.print(f"  [bold red]Error:[/bold red] {msg}")

    err_lower = msg.lower()
    for pattern, hint in _ERROR_HINTS:
        if pattern.lower() in err_lower:
            for line in hint.split("\n"):
                console.print(f"  [dim]{line}[/dim]")
            break

    console.print()


# ---------------------------------------------------------------------------
# First-run setup
# ---------------------------------------------------------------------------

_DEFAULT_CONFIG_TEMPLATE = """\
anthropic:
  base_url: "{base_url}"
  model_id: "{model_id}"
  max_tokens: 4096

agent_tokens:
  orchestrator: 8192
  data_collector: 4096
  ssh: 2048
  rca: 8192
  solution: 4096

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
# Main entry point — interactive mode only
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

    with console.status("[dim]  Initializing agents & MCP servers...[/dim]", spinner="dots"):
        orchestrator = create_orchestrator(
            settings,
            callback_handler=tracker.get_orchestrator_handler(),
            tool_callback_handler=tracker.get_tool_handler(),
        )
    console.print("[dim]  Agents ready.[/dim]")
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
            tracker.finish()
        except KeyboardInterrupt:
            elapsed = time.time() - start
            global _last_interrupt
            _last_interrupt = time.time()
            console.print()
            console.print(
                f"  [yellow]Interrupted[/yellow] [dim]({elapsed:.1f}s elapsed). "
                f"Press Ctrl+C again to exit.[/dim]"
            )
            try:
                orchestrator.messages.clear()
            except Exception:
                pass
            console.print("[dim]  Context cleared — next message starts a fresh conversation.[/dim]")
            console.print()
            continue
        except Exception as e:
            _print_error(e)
            continue
        elapsed = time.time() - start

        console.print()
        _print_response(str(response))
        _print_elapsed(elapsed)
        console.print()


# ---------------------------------------------------------------------------
# Pipeline server mode
# ---------------------------------------------------------------------------

@app.command()
def serve(
    port: Annotated[
        int,
        typer.Option("--port", "-p", help="Port to bind to"),
    ] = 8080,
    host: Annotated[
        str,
        typer.Option("--host", "-h", help="Host to bind to"),
    ] = "0.0.0.0",
    config: Annotated[
        Optional[Path],
        typer.Option("--config", "-c", help="Path to settings YAML config"),
    ] = None,
) -> None:
    """Start the automated SRE pipeline server (webhook receiver + analyzer)."""
    try:
        import uvicorn
    except ImportError:
        console.print("[red]  Pipeline server requires uvicorn. Install with: pip install sre-agent[webhook][/red]")
        raise typer.Exit(1)

    # Enable INFO-level output for sre_agent modules so the delivery log
    # fallback (_log_card) and runbook execution logs actually reach stdout.
    # uvicorn's log_level only affects its own loggers.
    import logging as _logging
    _logging.basicConfig(
        level=_logging.INFO,
        format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    _logging.getLogger("sre_agent").setLevel(_logging.INFO)

    _load_env_file()
    settings = load_settings(config)

    if not settings.anthropic.api_key:
        console.print("[bold red]  Error:[/bold red] ANTHROPIC_API_KEY is not set.")
        console.print(f"[dim]  Run: export ANTHROPIC_API_KEY=\"your-key\"[/dim]")
        raise typer.Exit(1)

    from sre_agent.pipeline.server import create_pipeline_app

    pipeline_app = create_pipeline_app(settings)

    console.print()
    console.print(Panel(
        Text("  SRE Agent Pipeline Server", style="bold red"),
        border_style="red", expand=False, padding=(0, 1),
    ))
    console.print()
    console.print(f"  [bold]Listening:[/bold] http://{host}:{port}")
    console.print(f"  [bold]Endpoints:[/bold]")
    console.print(f"  [dim]  POST /webhook/alertmanager — Alertmanager receiver[/dim]")
    console.print(f"  [dim]  POST /webhook/generic     — Generic webhook[/dim]")
    console.print(f"  [dim]  GET  /incidents            — Incident list[/dim]")
    console.print(f"  [dim]  GET  /approve/{{id}}        — Approval web UI[/dim]")
    console.print(f"  [dim]  GET  /health               — Health check[/dim]")
    console.print()

    if settings.delivery.teams_webhook_url:
        console.print(f"  [bold]Teams:[/bold] [green]configured[/green]")
    else:
        console.print(f"  [bold]Teams:[/bold] [yellow]not configured[/yellow] (reports will be logged only)")
    console.print()

    uvicorn.run(pipeline_app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    app()
