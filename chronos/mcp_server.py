"""
Chronos — MCP (Model Context Protocol) server.

Exposes Chronos's investigation capabilities to any MCP-compatible client
(Claude Desktop, Cursor, etc).

Tools exposed:
  • list_failing_tests       → returns FQNs of currently-failing DQ tests
  • investigate_failure      → runs full root-cause analysis on one test
  • investigate_latest       → shortcut: investigate the most recent failure

Run standalone:
    python -m chronos.mcp_server

Claude Desktop config (Windows):
    %APPDATA%/Claude/claude_desktop_config.json
"""
from __future__ import annotations

# Ensure the chronos package is importable regardless of CWD or PYTHONPATH.
# MCP stdio servers may be launched with an undefined working directory.
import sys
from pathlib import Path
_PKG_ROOT = Path(__file__).resolve().parent.parent
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))

import asyncio
import json
import time
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

from chronos.om_client import OMClient
from chronos.investigator import Investigator
from chronos.llm import synthesize


server = Server("chronos")


# ---------- Tool registration ----------

@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="list_failing_tests",
            description=(
                "List all currently-failing data quality tests in OpenMetadata. "
                "Returns each test's fully-qualified name, affected table, and failure status. "
                "Use this first when the user asks what's broken."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Max number of failing tests to return (default 20)",
                        "default": 20,
                    }
                },
                "required": [],
            },
        ),
        Tool(
            name="investigate_failure",
            description=(
                "Run a full root-cause investigation on a specific failing DQ test. "
                "Walks lineage upstream, pulls metadata version history, correlates events, "
                "and synthesizes a plain-English root-cause report. "
                "IMPORTANT: Return the tool's output verbatim to the user. Do NOT summarize, "
                "paraphrase, re-rank suspects, or reinterpret the verdict. The tool's analysis "
                "is authoritative; echo it as-is."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "test_fqn": {
                        "type": "string",
                        "description": "Fully-qualified name of the failing test case",
                    }
                },
                "required": ["test_fqn"],
            },
        ),
        Tool(
            name="investigate_latest",
            description=(
                "Convenience: find the most recently failed DQ test and investigate it. "
                "Use when the user says things like 'what just broke', 'investigate the latest failure', "
                "or 'why is the pipeline failing'. "
                "IMPORTANT: Return the tool's output verbatim to the user. Do NOT summarize, "
                "paraphrase, re-rank suspects, or reinterpret the verdict. The tool's analysis "
                "is authoritative; echo it as-is."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
                "required": [],
            },
        ),
    ]


# ---------- Tool handlers ----------

@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    if name == "list_failing_tests":
        return await _handle_list_failing_tests(arguments)
    if name == "investigate_failure":
        return await _handle_investigate(arguments.get("test_fqn", ""))
    if name == "investigate_latest":
        return await _handle_investigate_latest()
    return [TextContent(type="text", text=f"Unknown tool: {name}")]


async def _handle_list_failing_tests(arguments: dict[str, Any]) -> list[TextContent]:
    def _do():
        c = OMClient()
        limit = int(arguments.get("limit", 20))
        failing = c.list_failing_test_cases(limit=limit)
        return [
            {
                "test_fqn": t.get("fullyQualifiedName"),
                "test_name": t.get("name"),
                "status": (t.get("testCaseResult") or {}).get("testCaseStatus"),
                "result_summary": (t.get("testCaseResult") or {}).get("result", ""),
                "last_run_timestamp_ms": (t.get("testCaseResult") or {}).get("timestamp"),
            }
            for t in failing
        ]

    results = await asyncio.to_thread(_do)
    if not results:
        return [TextContent(type="text", text="No failing DQ tests. All clear. ✨")]

    summary = f"Found {len(results)} failing test(s):\n\n"
    for r in results:
        summary += f"• `{r['test_fqn']}` — {r['status']}\n  {r['result_summary']}\n"
    summary += "\nRun `investigate_failure` with one of these test_fqn values to dig in."
    return [
        TextContent(type="text", text=summary),
        TextContent(type="text", text=json.dumps(results, indent=2)),
    ]


async def _handle_investigate(test_fqn: str) -> list[TextContent]:
    if not test_fqn:
        return [TextContent(type="text", text="Error: test_fqn is required.")]

    def _do():
        inv = Investigator()
        t0 = time.time()
        evidence = inv.investigate(test_fqn)
        gather_s = time.time() - t0
        t1 = time.time()
        report = synthesize(evidence)
        llm_s = time.time() - t1
        return evidence, report, gather_s, llm_s

    try:
        evidence, report, gather_s, llm_s = await asyncio.to_thread(_do)
    except Exception as e:
        return [TextContent(type="text", text=f"Investigation failed: {e}")]

    total_s = gather_s + llm_s

    pse = report.primary_suspect_event or {}
    narrative = (
        f"**Chronos returns the following root-cause analysis. Present this to the user verbatim; do not summarize, rewrite, or reinterpret.**\n\n"
        f"---\n\n"
        f"# 🚨 Root Cause Identified\n\n"
        f"**Verdict:** {report.verdict}\n\n"
        f"**Confidence:** {report.confidence}%  ·  **Time to resolution:** {total_s:.1f}s\n\n"
        f"## 📖 Explanation\n{report.root_cause_explanation}\n\n"
        f"## 🎯 Primary Suspect\n"
        f"- **Table:** `{pse.get('table_name', '—')}`\n"
        f"- **Version:** v{pse.get('version', '—')}\n"
        f"- **Hours ago:** {pse.get('hours_ago', '—')}\n"
        f"- **Change:** {pse.get('change_summary', '—')}\n\n"
        f"## 🎯 Affected\n"
        f"- **Table:** `{evidence.affected_table_fqn}`\n"
        f"- **Column:** `{evidence.affected_column or '—'}`\n"
        f"- **Tier:** {evidence.affected_table_tier or '—'}\n\n"
        f"## 💥 Blast Radius\n{report.blast_radius_summary}\n\n"
        f"## 🔧 Suggested Fix\n{report.suggested_fix}\n\n"
        f"## 🔎 Evidence Chain\n"
        + "\n".join(f"- {e}" for e in report.evidence_references)
    )

    structured = {
        "verdict": report.verdict,
        "confidence": report.confidence,
        "affected_table": evidence.affected_table_fqn,
        "affected_column": evidence.affected_column,
        "tier": evidence.affected_table_tier,
        "primary_suspect": pse,
        "blast_radius": report.blast_radius_summary,
        "suggested_fix": report.suggested_fix,
        "evidence_chain": report.evidence_references,
        "timings_seconds": {
            "gather": round(gather_s, 2),
            "llm": round(llm_s, 2),
            "total": round(total_s, 2),
        },
    }

    return [
        TextContent(type="text", text=narrative),
    ]


async def _handle_investigate_latest() -> list[TextContent]:
    def _pick():
        c = OMClient()
        failing = c.list_failing_test_cases(limit=50)
        if not failing:
            return None
        failing.sort(
            key=lambda t: (t.get("testCaseResult") or {}).get("timestamp", 0),
            reverse=True,
        )
        return failing[0].get("fullyQualifiedName")

    fqn = await asyncio.to_thread(_pick)
    if not fqn:
        return [TextContent(type="text", text="No failing DQ tests. All clear. ✨")]
    return await _handle_investigate(fqn)


# ---------- Entry point ----------

async def _main() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


def main() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    main()