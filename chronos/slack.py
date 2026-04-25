"""
Chronos — Slack delivery.

Posts the root-cause report to a Slack channel via Incoming Webhook.
Uses Block Kit for a production-quality layout. Gracefully degrades:
  - If SLACK_WEBHOOK_URL isn't set → no-op, returns False
  - If the POST fails → logs and returns False
  - Never raises. Slack is a side-channel; investigation success comes first.
"""
from __future__ import annotations

import os
from typing import Any

import requests
from dotenv import load_dotenv

from chronos.investigator import Evidence
from chronos.llm import RootCauseReport

load_dotenv()


# ---------- Block Kit builder ----------

def _confidence_emoji(confidence: int) -> str:
    if confidence >= 85:
        return "🟢"
    if confidence >= 65:
        return "🟡"
    return "🔴"


def _tier_emoji(tier: str | None) -> str:
    if tier == "Tier1":
        return "🔥"
    if tier == "Tier2":
        return "⚠️"
    return "•"


def build_message(
    evidence: Evidence,
    report: RootCauseReport,
    total_seconds: float,
    replay_url: str | None = None,
) -> dict[str, Any]:
    """
    Return a Slack Block Kit payload.
    replay_url: optional public URL to the Temporal Replay HTML.
                If None, we still render a mention of local replay file.
    """
    confidence_label = f"{_confidence_emoji(report.confidence)} {report.confidence}%"
    tier_badge = (
        f"{_tier_emoji(evidence.affected_table_tier)} *{evidence.affected_table_tier}*"
        if evidence.affected_table_tier
        else "•"
    )

    affected_name = evidence.affected_table_fqn.rsplit(".", 1)[-1]
    column_str = f".`{evidence.affected_column}`" if evidence.affected_column else ""

    # Primary suspect block
    pse = report.primary_suspect_event or {}
    suspect_lines = [
        f"*Table:* `{pse.get('table_name', '—')}`",
        f"*Version:* v{pse.get('version', '—')}",
        f"*When:* {pse.get('hours_ago', '—')}h ago",
    ]
    suspect_summary = str(pse.get("change_summary", "—"))
    if len(suspect_summary) > 280:
        suspect_summary = suspect_summary[:277] + "…"

    blocks: list[dict[str, Any]] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "🚨 Chronos · Root Cause Identified"},
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*{report.verdict}*\n\n"
                    f"Confidence: {confidence_label}  ·  "
                    f"Resolved in *{total_seconds:.1f}s*  ·  "
                    f"_typical manual time: 30-60 min_"
                ),
            },
        },
        {"type": "divider"},
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Affected*\n`{affected_name}`{column_str}"},
                {"type": "mrkdwn", "text": f"*Tier*\n{tier_badge}"},
                {"type": "mrkdwn", "text": f"*Test*\n`{evidence.test_name}`"},
                {"type": "mrkdwn", "text": f"*Fired*\n{evidence.failure_hours_ago:.1f}h ago"},
            ],
        },
        {"type": "divider"},
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*📖 Explanation*\n{report.root_cause_explanation}",
            },
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    "*🎯 Primary Suspect*\n"
                    + "\n".join(suspect_lines)
                    + f"\n```{suspect_summary}```"
                ),
            },
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*💥 Blast Radius*\n{report.blast_radius_summary}",
            },
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*🔧 Suggested Fix*\n{report.suggested_fix}",
            },
        },
    ]

    # Owners / Evidence chain
    if report.owners_to_notify:
        owners_str = " ".join(report.owners_to_notify)
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*📣 Notify*\n{owners_str}"},
        })

    if report.evidence_references:
        chain = "\n".join(f"• {e}" for e in report.evidence_references)
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*🔎 Evidence Chain*\n{chain}"},
        })

    blocks.append({"type": "divider"})

    # Context / link to Temporal Replay
    context_elements = [
        {
            "type": "mrkdwn",
            "text": (
                f"🕐 *Temporal Replay*: `{replay_url}`"
                if replay_url
                else "🕐 *Temporal Replay* generated locally — open `replay.html` to explore the timeline"
            ),
        },
    ]
    blocks.append({"type": "context", "elements": context_elements})
    blocks.append({
        "type": "context",
        "elements": [
            {
                "type": "mrkdwn",
                "text": "_Chronos — semantic intelligence for failing data · flux capacitor engaged_",
            }
        ],
    })

    # Fallback text for notifications + accessibility
    fallback = (
        f"🚨 Chronos: {report.verdict} "
        f"(conf {report.confidence}%, {total_seconds:.1f}s)"
    )

    return {
        "text": fallback,
        "blocks": blocks,
    }


# ---------- Public API ----------

def post(
    evidence: Evidence,
    report: RootCauseReport,
    total_seconds: float,
    replay_url: str | None = None,
    webhook_url: str | None = None,
) -> bool:
    """
    Post the report to Slack. Returns True on success.
    Never raises — Slack delivery is best-effort.
    """
    url = webhook_url or os.environ.get("SLACK_WEBHOOK_URL")
    if not url:
        return False

    payload = build_message(evidence, report, total_seconds, replay_url)

    try:
        r = requests.post(url, json=payload, timeout=10)
        return r.status_code == 200 and r.text.strip() == "ok"
    except Exception:
        return False