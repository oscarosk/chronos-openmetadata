"""
Chronos — LLM root-cause synthesizer.

Takes an Evidence bundle (from investigator.py) and returns a structured
RootCauseReport. Supports both OpenAI and Anthropic via a `provider` flag.
Output is STRICTLY JSON so the CLI / Slack / demo can render it deterministically.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, asdict
from typing import Any

from dotenv import load_dotenv

from chronos.investigator import Evidence

load_dotenv()


# ---------- Report schema ----------

@dataclass
class RootCauseReport:
    """Structured root-cause analysis. Serializable, demo-ready."""
    verdict: str                       # 1-line summary, <140 chars
    confidence: int                    # 0-100
    root_cause_explanation: str        # ~2-3 sentences, plain English
    primary_suspect_event: dict[str, Any]  # which VersionEvent (or {} if none)
    blast_radius_summary: str          # what will break downstream
    suggested_fix: str                 # actionable next step
    owners_to_notify: list[str]        # ["@marketing-data"]
    evidence_references: list[str]     # human-readable bullet chain of reasoning

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------- Prompts ----------

SYSTEM_PROMPT = """You are Chronos, an expert data incident investigator. You specialize in root-cause analysis for data quality failures in modern data warehouses. You reason like a senior data engineer: methodical, evidence-first, always connecting CAUSATION with timing and lineage.

You will be given a JSON evidence bundle describing a failing data quality test and the recent history of upstream tables. Your job is to identify the MOST LIKELY root cause.

CAUSAL RANKING RULES — THESE OVERRIDE ALL OTHER REASONING:

Class A — CAN CAUSE DATA FAILURES (can produce nulls, mismatches, test failures):
- Column renames (e.g. "cust_id renamed to customer_identifier")
- Column deletions or additions
- Schema changes, data type changes
- Breaking changes announced in a description diff (look for phrases like "BREAKING CHANGE", "renamed", "migrated", "dropped")
- Updates to transformation/SQL logic referenced in descriptions

Class B — CANNOT DIRECTLY CAUSE DATA FAILURES (metadata-only events):
- Owner additions or changes
- Tag additions (PII, Tier1, etc.)
- Tier changes
- Description edits that are purely documentation (not announcing a schema change)
- Classification changes

HARD RULE: If ANY Class A event exists in the evidence window, it MUST be chosen as the primary_suspect_event. Never pick a Class B event as primary suspect when a Class A event is present. Temporal proximity does NOT override this — a schema rename from 6 hours ago is always a better suspect than an owner add from 1 hour ago.

You may only pick a Class B event as primary suspect if NO Class A event exists anywhere in the evidence. In that case, lower your confidence to 30-50 and state clearly that you are guessing.

OUTPUT RULES:
1. ALWAYS respond with a single JSON object matching the schema provided. Do NOT include markdown fences, commentary, or prose outside JSON.
2. Confidence calibration:
   - 85-95: a Class A event clearly correlates in time with the failure
   - 60-84: a Class A event exists but timing is less clear
   - 30-59: only Class B events exist; best guess
   - Below 30: genuinely insufficient evidence
3. Keep the verdict under 140 characters. Be specific — name the table and the change.
4. The root_cause_explanation should be 2-3 sentences explaining the causal chain: what changed, why it broke things, what you see in the evidence.
5. The suggested_fix should be a concrete action a data engineer can take today.
6. If no owners are mentioned in the evidence, return an empty list for owners_to_notify. Do not invent owners.

EXAMPLE OF CORRECT REASONING:
Evidence contains:
  - raw_orders v0.2 (6h ago): description updated to "BREAKING CHANGE: column 'cust_id' renamed to 'customer_identifier'"
  - dim_customer v0.3 (1h ago): added owners
  - fact_order v0.3 (30min ago): added owners

Correct primary_suspect_event = raw_orders v0.2 (Class A, schema rename). Confidence ~90%.
INCORRECT primary_suspect_event = dim_customer v0.3 (Class B, owner add cannot cause nulls in fact_order.customer_id).
"""

USER_PROMPT_TEMPLATE = """Investigate this incident and return a RootCauseReport as JSON.

# Schema (return EXACTLY this shape):
{{
  "verdict": "string, max 140 chars",
  "confidence": 0-100 integer,
  "root_cause_explanation": "2-3 sentences, plain English",
  "primary_suspect_event": {{
      "table_name": "...",
      "version": "...",
      "hours_ago": float,
      "change_summary": "..."
  }},
  "blast_radius_summary": "what downstream breaks",
  "suggested_fix": "concrete action",
  "owners_to_notify": ["@team-a", "@team-b"],
  "evidence_references": ["bullet 1", "bullet 2", "bullet 3"]
}}

# Evidence:
{evidence_json}
"""


# ---------- LLM client factory ----------

def _strip_fences(text: str) -> str:
    """Some models wrap JSON in ```json ... ``` despite instructions. Strip them."""
    text = text.strip()
    # Match ```[lang]? ... ``` fences
    m = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    return m.group(1).strip() if m else text


def _call_openai(user_prompt: str, model: str) -> str:
    from openai import OpenAI
    client = OpenAI()
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        response_format={"type": "json_object"},
        temperature=0.2,
    )
    return resp.choices[0].message.content or ""


def _call_anthropic(user_prompt: str, model: str) -> str:
    from anthropic import Anthropic
    client = Anthropic()
    resp = client.messages.create(
        model=model,
        max_tokens=1500,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )
    # Concatenate text blocks
    return "".join(b.text for b in resp.content if b.type == "text")


# ---------- Public API ----------

def synthesize(
    evidence: Evidence,
    provider: str | None = None,
    model: str | None = None,
) -> RootCauseReport:
    """Run LLM synthesis on evidence. Returns a parsed RootCauseReport."""

    # Decide provider
    if provider is None:
        if os.getenv("OPENAI_API_KEY"):
            provider = "openai"
        elif os.getenv("ANTHROPIC_API_KEY"):
            provider = "anthropic"
        else:
            raise RuntimeError(
                "No LLM API key found. Set OPENAI_API_KEY or ANTHROPIC_API_KEY in .env"
            )

    # Decide model
    if model is None:
        model = (
            "gpt-4o-mini" if provider == "openai"
            else "claude-sonnet-4-5"
        )

    # Assemble prompt
    evidence_json = json.dumps(evidence.to_dict(), indent=2, default=str)
    user_prompt = USER_PROMPT_TEMPLATE.format(evidence_json=evidence_json)

    # Call the LLM
    if provider == "openai":
        raw = _call_openai(user_prompt, model)
    elif provider == "anthropic":
        raw = _call_anthropic(user_prompt, model)
    else:
        raise ValueError(f"Unknown provider: {provider}")

    # Parse JSON
    cleaned = _strip_fences(raw)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"LLM returned non-JSON output:\n{raw[:500]}\n\nError: {e}"
        )

    # Defensive defaults
    return RootCauseReport(
        verdict=str(data.get("verdict", ""))[:140],
        confidence=int(data.get("confidence", 0)),
        root_cause_explanation=str(data.get("root_cause_explanation", "")),
        primary_suspect_event=data.get("primary_suspect_event") or {},
        blast_radius_summary=str(data.get("blast_radius_summary", "")),
        suggested_fix=str(data.get("suggested_fix", "")),
        owners_to_notify=list(data.get("owners_to_notify") or []),
        evidence_references=list(data.get("evidence_references") or []),
    )