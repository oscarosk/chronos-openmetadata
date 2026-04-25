"""
Chronos CLI — the judge-facing entry point.

Usage:
    python -m chronos investigate <test-case-fqn>
    python -m chronos investigate --latest       # auto-pick most recent failure
    python -m chronos list-failures              # show all failing DQ tests
"""
from __future__ import annotations

import os
import argparse
import sys
import time
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.markdown import Markdown
from rich.progress import Progress, SpinnerColumn, TextColumn

from chronos.om_client import OMClient
from chronos.investigator import Investigator, Evidence
from chronos.llm import synthesize, RootCauseReport
from chronos.replay import generate as generate_replay
from chronos import slack as chronos_slack

console = Console()

BANNER = r"""
 [cyan]   ________                             
   / ____/ /_  _________  ____  ____  _____
  / /   / __ \/ ___/ __ \/ __ \/ __ \/ ___/
 / /___/ / / / /  / /_/ / / / / /_/ (__  ) 
 \____/_/ /_/_/   \____/_/ /_/\____/____/  [/cyan]
 [dim]Root-cause analysis for failing data — powered by OpenMetadata[/dim]
"""


def cmd_list_failures() -> int:
    """List all currently-failing DQ tests."""
    c = OMClient()
    failing = c.list_failing_test_cases(limit=50)

    if not failing:
        console.print("[green]✨ No failing tests. All clear.[/green]")
        return 0

    t = Table(title=f"🚨 {len(failing)} Failing Data Quality Test(s)", show_lines=True)
    t.add_column("#", style="dim", width=3)
    t.add_column("Test", style="bold")
    t.add_column("Table", style="yellow")
    t.add_column("Status", style="red")

    for i, tc in enumerate(failing, start=1):
        name = tc.get("name", "?")
        fqn = tc.get("fullyQualifiedName", "?")
        parts = fqn.rsplit(".", 2)
        table_fqn = parts[0] if len(parts) >= 2 else "?"
        status = (tc.get("testCaseResult") or {}).get("testCaseStatus", "?")
        t.add_row(str(i), name, table_fqn, status)

    console.print(t)
    console.print("\n[dim]Run: [bold]python -m chronos investigate <full-fqn>[/bold][/dim]")
    console.print("[dim]Or:  [bold]python -m chronos investigate --latest[/bold][/dim]\n")
    return 0


def _pick_latest_failure(c: OMClient) -> str | None:
    """Return FQN of most recently failed test."""
    failing = c.list_failing_test_cases(limit=50)
    if not failing:
        return None
    def ts(tc):
        return (tc.get("testCaseResult") or {}).get("timestamp", 0)
    failing.sort(key=ts, reverse=True)
    return failing[0].get("fullyQualifiedName")


def cmd_investigate(fqn: str | None, latest: bool, provider: str | None) -> int:
    """Run the full investigation pipeline."""
    console.print(BANNER)

    c = OMClient()

    if latest:
        fqn = _pick_latest_failure(c)
        if not fqn:
            console.print("[yellow]No failing tests found. Nothing to investigate.[/yellow]")
            return 1
        console.print(f"[dim]--latest resolved to:[/dim] [yellow]{fqn}[/yellow]\n")
    elif not fqn:
        console.print("[red]Error: provide a test FQN or use --latest[/red]")
        return 2

    # 1. Gather evidence
    t_start = time.time()
    with Progress(
        SpinnerColumn(),
        TextColumn("{task.description}"),
        console=console,
        transient=True,
    ) as progress:
        progress.add_task("[yellow]Walking lineage & pulling version history...", total=None)
        inv = Investigator()
        evidence = inv.investigate(fqn)
    gather_s = time.time() - t_start

    console.print(
        f"[green]✓[/green] Evidence assembled "
        f"[dim]({gather_s:.1f}s — {len(evidence.upstream)} upstream tables, "
        f"{len(evidence.suspicious_events)} suspicious events)[/dim]"
    )

    # 2. LLM synthesis
    t_llm = time.time()
    with Progress(
        SpinnerColumn(),
        TextColumn("{task.description}"),
        console=console,
        transient=True,
    ) as progress:
        progress.add_task("[yellow]Analyzing evidence with AI...", total=None)
        report = synthesize(evidence, provider=provider)
    llm_s = time.time() - t_llm

    total_s = gather_s + llm_s
    console.print(
        f"[green]✓[/green] Root cause identified "
        f"[dim]({llm_s:.1f}s LLM — {total_s:.1f}s total)[/dim]\n"
    )

    _render_report(report, evidence, total_s)

    # Generate Temporal Replay HTML
    from pathlib import Path
    import webbrowser
    replay_path = generate_replay(
        evidence,
        report,
        Path("replay.html"),
        timings={
            "gather": round(gather_s, 2),
            "llm": round(llm_s, 2),
            "total": round(total_s, 2),
        },
    )
    console.print(
        f"\n[bold cyan]⚡ Temporal Replay generated:[/bold cyan] "
        f"[underline]{replay_path}[/underline]"
    )

    # Auto-open in default browser unless --no-open was passed
    if not getattr(cmd_investigate, "_no_open", False):
        try:
            webbrowser.open(replay_path.as_uri())
            console.print("[dim]Opening in your default browser...[/dim]")
        except Exception as e:
            console.print(f"[dim]Could not auto-open: {e}. Open manually: {replay_path}[/dim]")

    # Deliver to Slack (best-effort, non-fatal)
    replay_url_str = replay_path.as_uri()
    slack_ok = chronos_slack.post(evidence, report, total_s, replay_url=replay_url_str)
    if slack_ok:
        console.print("[bold green]✓ Delivered to Slack[/bold green] [dim](#all-chronos)[/dim]\n")
    else:
        if os.environ.get("SLACK_WEBHOOK_URL"):
            console.print("[yellow]⚠ Slack delivery failed — check webhook URL[/yellow]\n")
        else:
            console.print("[dim]Tip: set SLACK_WEBHOOK_URL in .env to auto-deliver reports to Slack[/dim]\n")

    return 0


def _render_report(report: RootCauseReport, evidence: Evidence, total_s: float) -> None:
    """Pretty-print the full root cause report."""

    conf_color = "green" if report.confidence >= 80 else "yellow" if report.confidence >= 60 else "red"
    verdict_panel = Panel(
        f"[bold white]{report.verdict}[/bold white]\n\n"
        f"Confidence: [{conf_color} bold]{report.confidence}%[/]  "
        f"·  [dim]Time to resolution: [bold]{total_s:.1f}s[/bold] "
        f"(typical manual: 30-60 min)[/dim]",
        title="[bold red]🚨 ROOT CAUSE IDENTIFIED[/bold red]",
        border_style="red",
        padding=(1, 2),
    )
    console.print(verdict_panel)

    console.print("\n[bold]📖 Explanation[/bold]")
    console.print(Markdown(report.root_cause_explanation))

    if report.primary_suspect_event:
        pse = report.primary_suspect_event
        t = Table(title="🎯 Primary Suspect", show_header=False, box=None, padding=(0, 2))
        t.add_column(style="bold yellow", width=18)
        t.add_column()
        for k, v in pse.items():
            t.add_row(str(k), str(v)[:250])
        console.print()
        console.print(t)

    console.print("\n[bold]💥 Blast Radius[/bold]")
    console.print(f"  {report.blast_radius_summary}")
    if evidence.affected_table_tier:
        tier_color = "red" if evidence.affected_table_tier == "Tier1" else "yellow"
        console.print(
            f"  [dim]Affected table tier:[/dim] "
            f"[{tier_color} bold]{evidence.affected_table_tier}[/]"
        )

    console.print("\n[bold]🔧 Suggested Fix[/bold]")
    console.print(Markdown(report.suggested_fix))

    if report.owners_to_notify:
        console.print("\n[bold]📣 Notify[/bold]")
        for o in report.owners_to_notify:
            console.print(f"  • {o}")

    if report.evidence_references:
        console.print("\n[bold]🔎 Evidence Chain[/bold]")
        for ref in report.evidence_references:
            console.print(f"  • {ref}")

    console.print()


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="chronos",
        description="Root-cause analysis for failing data quality tests in OpenMetadata.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_inv = sub.add_parser("investigate", help="Run root-cause analysis on a failing test")
    p_inv.add_argument("fqn", nargs="?", help="Fully-qualified name of the failing test case")
    p_inv.add_argument("--latest", action="store_true", help="Use the most recently failed test")
    p_inv.add_argument("--provider", choices=["openai", "anthropic"], help="LLM provider")
    p_inv.add_argument("--no-open", action="store_true", help="Don't auto-open Temporal Replay")

    sub.add_parser("list-failures", help="List all currently-failing DQ tests")

    args = parser.parse_args()

    if args.command == "investigate":
        cmd_investigate._no_open = args.no_open
        sys.exit(cmd_investigate(args.fqn, args.latest, args.provider))
    elif args.command == "list-failures":
        sys.exit(cmd_list_failures())


if __name__ == "__main__":
    main()