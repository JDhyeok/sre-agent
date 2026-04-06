"""CLI entry point for the SRE Agent system."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Prompt

from sre_agent.config import load_settings

app = typer.Typer(
    name="sre-agent",
    help="SRE Multi-Agent System for automated Root Cause Analysis",
    no_args_is_help=True,
)
console = Console()

# Markers the orchestrator uses to signal it needs user input vs. analysis is done
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
    """Heuristic: does the response look like a completed analysis report?"""
    return any(marker.lower() in text.lower() for marker in _ANALYSIS_DONE_MARKERS)


def _run_interactive_analysis(orchestrator, initial_message: str) -> None:
    """Run a multi-turn conversation loop with the orchestrator.

    The orchestrator will ask clarifying questions when it needs more context.
    The user can answer, and the orchestrator continues until the analysis is complete.
    """
    console.print("[bold green]Starting analysis...[/bold green]\n")
    start = time.time()
    turn = 0
    max_turns = 10

    response = orchestrator(initial_message)
    response_text = str(response)
    turn += 1

    while turn < max_turns:
        console.print()
        console.print(Markdown(response_text))

        if _looks_like_final_report(response_text):
            break

        console.print()
        try:
            user_input = Prompt.ask(
                "[bold cyan]>> 답변 (분석 시작: Enter만, 종료: q)[/bold cyan]"
            )
        except (KeyboardInterrupt, EOFError):
            console.print("\n[yellow]Analysis cancelled.[/yellow]")
            return

        if user_input.strip().lower() in ("q", "quit", "exit"):
            console.print("[yellow]Analysis cancelled.[/yellow]")
            return

        if not user_input.strip():
            user_input = "제공된 정보로 분석을 진행해주세요."

        response = orchestrator(user_input)
        response_text = str(response)
        turn += 1

    elapsed = time.time() - start
    console.print("\n")
    console.print(Panel("[bold]Analysis Complete[/bold]", style="green"))
    console.print(f"[dim]Elapsed: {elapsed:.1f}s | Turns: {turn}[/dim]\n")

    if not _looks_like_final_report(response_text):
        console.print(Markdown(response_text))


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
    """Analyze an incident using the multi-agent SRE system.

    By default, the orchestrator will ask clarifying questions if the incident
    description is vague. Use --no-interactive to skip Q&A and analyze directly.
    """
    settings = load_settings(config)

    incident_context = incident
    if alert_json and alert_json.exists():
        with open(alert_json) as f:
            alert_data = json.load(f)
        incident_context += f"\n\nAlertmanager Payload:\n{json.dumps(alert_data, indent=2)}"

    console.print(Panel("[bold]SRE Agent - Incident Analysis[/bold]", style="blue"))
    console.print(f"\n[bold]Incident:[/bold] {incident_context}\n")
    console.print("[dim]Initializing agents...[/dim]\n")

    from sre_agent.agents.orchestrator import create_orchestrator

    orchestrator = create_orchestrator(settings)

    if no_interactive:
        console.print("[bold green]Starting analysis (non-interactive)...[/bold green]\n")
        start = time.time()
        response = orchestrator(
            f"Investigate the following incident and produce a complete RCA report. "
            f"Do NOT ask questions - use your best judgment with available info:\n\n{incident_context}"
        )
        elapsed = time.time() - start
        console.print(Panel("[bold]Analysis Complete[/bold]", style="green"))
        console.print(f"[dim]Elapsed: {elapsed:.1f}s[/dim]\n")
        console.print(Markdown(str(response)))
    else:
        _run_interactive_analysis(orchestrator, incident_context)


@app.command()
def chat(
    config: Annotated[
        Optional[Path],
        typer.Option("--config", "-c", help="Path to settings YAML config"),
    ] = None,
) -> None:
    """Start an interactive chat session with the SRE agent.

    Free-form conversation mode where you can describe incidents,
    ask follow-up questions, and get analysis on demand.
    """
    settings = load_settings(config)

    console.print(Panel("[bold]SRE Agent - Interactive Chat[/bold]", style="blue"))
    console.print("Describe an incident or ask a question. Type 'q' to quit.\n")

    from sre_agent.agents.orchestrator import create_orchestrator

    orchestrator = create_orchestrator(settings)

    while True:
        try:
            user_input = Prompt.ask("[bold cyan]you[/bold cyan]")
        except (KeyboardInterrupt, EOFError):
            console.print("\n[dim]Goodbye.[/dim]")
            break

        if user_input.strip().lower() in ("q", "quit", "exit"):
            console.print("[dim]Goodbye.[/dim]")
            break

        if not user_input.strip():
            continue

        response = orchestrator(user_input)
        console.print()
        console.print(Markdown(str(response)))
        console.print()


@app.command()
def check(
    config: Annotated[
        Optional[Path],
        typer.Option("--config", "-c", help="Path to settings YAML config"),
    ] = None,
) -> None:
    """Validate configuration and check connectivity to data sources."""
    settings = load_settings(config)

    console.print(Panel("[bold]SRE Agent - Configuration Check[/bold]", style="blue"))

    console.print(f"  Anthropic base_url: {settings.anthropic.base_url}")
    console.print(f"  Anthropic model:    {settings.anthropic.model_id}")
    console.print(f"  Prometheus URL:     {settings.prometheus.url}")
    console.print(f"  Alertmanager URL:   {settings.prometheus.alertmanager_url}")
    console.print(f"  Elasticsearch URL:  {settings.elasticsearch.url}")
    console.print(f"  SSH hosts:          {len(settings.ssh.hosts)} configured")
    for host in settings.ssh.hosts:
        console.print(f"    - {host.name} ({host.hostname}:{host.port})")

    api_key_set = bool(settings.anthropic.api_key)
    console.print(f"\n  ANTHROPIC_API_KEY:  {'[green]set[/green]' if api_key_set else '[red]NOT SET[/red]'}")

    if not api_key_set:
        console.print("\n[yellow]Warning: Set ANTHROPIC_API_KEY environment variable before running analysis.[/yellow]")


@app.command()
def webhook(
    host: Annotated[str, typer.Option("--host", "-h", help="Host to bind to")] = "0.0.0.0",
    port: Annotated[int, typer.Option("--port", "-p", help="Port to bind to")] = 8080,
    config: Annotated[
        Optional[Path],
        typer.Option("--config", "-c", help="Path to settings YAML config"),
    ] = None,
) -> None:
    """Start the Alertmanager webhook server.

    Listens for Alertmanager webhook payloads and triggers analysis automatically.
    """
    try:
        import uvicorn
    except ImportError:
        console.print("[red]Webhook requires uvicorn. Install with: pip install sre-agent[webhook][/red]")
        raise typer.Exit(1)

    from sre_agent.integrations.webhook import create_webhook_app

    console.print(Panel("[bold]SRE Agent - Webhook Server[/bold]", style="blue"))
    console.print(f"Starting webhook server on {host}:{port}...")
    console.print(f"  POST /webhook/alertmanager  - Receive alerts")
    console.print(f"  GET  /webhook/status/{{id}}   - Check analysis status")
    console.print(f"  GET  /health               - Health check\n")

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
        console.print("[yellow]No matching incidents found.[/yellow]")
        return

    console.print(Panel(f"[bold]Found {len(results)} similar incidents[/bold]", style="blue"))
    for i, inc in enumerate(results, 1):
        console.print(f"\n[bold]#{i}[/bold] {inc.get('id', 'unknown')} - {inc.get('stored_at', '')}")
        summary = inc.get("incident_summary", inc.get("incident_context", ""))
        console.print(f"  Summary: {summary[:200]}")
        if inc.get("primary_root_cause"):
            console.print(f"  Root Cause: {inc['primary_root_cause'][:200]}")


@app.command()
def kb_list(
    count: Annotated[int, typer.Option("--count", "-n", help="Number of recent incidents")] = 10,
) -> None:
    """List recent incidents from the knowledge base."""
    from sre_agent.integrations.knowledge_base import IncidentKB

    kb = IncidentKB()
    results = kb.list_recent(n=count)

    if not results:
        console.print("[yellow]No incidents in knowledge base.[/yellow]")
        return

    console.print(Panel(f"[bold]Recent {len(results)} incidents[/bold]", style="blue"))
    for inc in results:
        inc_id = inc.get("id", "unknown")
        stored = inc.get("stored_at", "")
        summary = inc.get("incident_summary", inc.get("incident_context", ""))[:120]
        console.print(f"  {inc_id} [{stored}] {summary}")


if __name__ == "__main__":
    app()
