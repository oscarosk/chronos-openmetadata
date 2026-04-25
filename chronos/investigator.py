"""
Chronos — Incident Investigator.

Given a failing DQ test case FQN, produce a structured Evidence report:
  • The failure itself (what, when, how many rows)
  • The affected table and its column
  • The lineage upstream (candidate culprits)
  • Recent version-history events on each upstream table
  • Downstream blast radius (what breaks if we don't fix this)

The Evidence object is LLM-agnostic. llm.py turns it into a root-cause explanation.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from typing import Any

from chronos.om_client import OMClient


# ---------- Data classes ----------

@dataclass
class VersionEvent:
    table_fqn: str
    table_name: str
    version: str
    timestamp_ms: int
    updated_by: str
    change_summary: str  # rendered from changeDescription
    hours_ago: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class UpstreamTable:
    fqn: str
    name: str
    depth: int
    version_events: list[VersionEvent] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "fqn": self.fqn,
            "name": self.name,
            "depth": self.depth,
            "version_events": [v.to_dict() for v in self.version_events],
        }


@dataclass
class DownstreamAsset:
    fqn: str
    name: str
    depth: int
    tier: str | None = None  # "Tier1" etc.

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Evidence:
    """Full evidence bundle Chronos hands to the LLM."""

    # The failure
    test_fqn: str
    test_name: str
    test_definition: str  # e.g. "columnValuesToBeNotNull"
    test_status: str
    test_result_summary: str
    failure_timestamp_ms: int
    failure_hours_ago: float

    # The affected entity
    affected_table_fqn: str
    affected_table_description: str
    affected_column: str | None
    affected_table_tier: str | None

    # Lineage
    upstream: list[UpstreamTable]
    downstream: list[DownstreamAsset]

    # Correlation hints (pre-computed for the LLM)
    suspicious_events: list[VersionEvent]  # events in the 7 days before the failure

    def to_dict(self) -> dict[str, Any]:
        return {
            "failure": {
                "test_fqn": self.test_fqn,
                "test_name": self.test_name,
                "test_definition": self.test_definition,
                "test_status": self.test_status,
                "result_summary": self.test_result_summary,
                "fired_at_ms": self.failure_timestamp_ms,
                "hours_ago": round(self.failure_hours_ago, 1),
            },
            "affected_table": {
                "fqn": self.affected_table_fqn,
                "description": self.affected_table_description,
                "column": self.affected_column,
                "tier": self.affected_table_tier,
            },
            "upstream": [u.to_dict() for u in self.upstream],
            "downstream": [d.to_dict() for d in self.downstream],
            "suspicious_events": [e.to_dict() for e in self.suspicious_events],
        }


# ---------- Helpers ----------

def _now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def _hours_between(later_ms: int, earlier_ms: int) -> float:
    return (later_ms - earlier_ms) / (1000 * 60 * 60)


def _extract_tier(tags: list[dict[str, Any]]) -> str | None:
    for tag in tags or []:
        fqn = tag.get("tagFQN", "")
        if fqn.startswith("Tier."):
            return fqn.split(".", 1)[1]
    return None


def _extract_table_fqn_from_entity_link(entity_link: str) -> tuple[str, str | None]:
    """
    Parse OpenMetadata's entityLink format:
      <#E::table::FQN>
      <#E::table::FQN::columns::COLUMN_NAME>
    Returns (table_fqn, column_name_or_none).
    """
    # Strip the surrounding <#E:: and >
    inner = entity_link.strip().removeprefix("<#E::").removesuffix(">")
    parts = inner.split("::")
    # parts = ["table", FQN, ...]
    if len(parts) < 2 or parts[0] != "table":
        return ("", None)
    table_fqn = parts[1]
    column = None
    if len(parts) >= 4 and parts[2] == "columns":
        column = parts[3]
    return (table_fqn, column)


def _summarize_change(change_description: Any) -> str:
    """
    OpenMetadata's changeDescription has fieldsAdded / fieldsUpdated / fieldsDeleted arrays.
    Turn it into a single human-readable line.
    """
    if not isinstance(change_description, dict):
        return "unknown change"

    pieces: list[str] = []

    for field_name, change_list in (
        ("added", change_description.get("fieldsAdded", []) or []),
        ("updated", change_description.get("fieldsUpdated", []) or []),
        ("deleted", change_description.get("fieldsDeleted", []) or []),
    ):
        for ch in change_list:
            name = ch.get("name", "?")
            if field_name == "updated":
                old = str(ch.get("oldValue", ""))[:120]
                new = str(ch.get("newValue", ""))[:120]
                pieces.append(f"{field_name} {name}: {old!r} → {new!r}")
            else:
                pieces.append(f"{field_name} {name}")

    return "; ".join(pieces) if pieces else "version bump (no field diff recorded)"


# ---------- Main investigator ----------

class Investigator:
    """Chronos's investigation engine."""

    def __init__(self, client: OMClient | None = None, suspicious_window_days: int = 7):
        self.client = client or OMClient()
        self.suspicious_window_ms = suspicious_window_days * 24 * 60 * 60 * 1000

    def investigate(self, test_case_fqn: str) -> Evidence:
        """Run full investigation for a given failing test FQN."""

        # 1. Pull the test case + its latest result
        tc = self.client.get_test_case(test_case_fqn)
        result = tc.get("testCaseResult") or {}
        status = result.get("testCaseStatus", "Unknown")
        result_summary = result.get("result", "") or "(no summary)"
        failure_ts_ms = result.get("timestamp") or _now_ms()

        # testDefinition can be a string or a nested {name, ...}
        td = tc.get("testDefinition")
        if isinstance(td, dict):
            test_definition = td.get("name", "unknown")
        else:
            test_definition = str(td or "unknown")

        # 2. Locate the affected table + column
        entity_link = tc.get("entityLink", "")
        table_fqn, column = _extract_table_fqn_from_entity_link(entity_link)
        if not table_fqn:
            raise RuntimeError(
                f"Could not parse entity link from test case: {entity_link!r}"
            )

        table = self.client.get_table(table_fqn)
        table_description = table.get("description", "") or ""
        table_tier = _extract_tier(table.get("tags", []) or [])

        # 3. Walk upstream + downstream
        upstream_nodes = self.client.walk_upstream(table_fqn, max_depth=3)
        downstream_nodes = self.client.walk_downstream(table_fqn, max_depth=3)

        # 4. For each upstream table, fetch version history
        upstream: list[UpstreamTable] = []
        all_events: list[VersionEvent] = []

        for node in upstream_nodes:
            ut = UpstreamTable(fqn=node["fqn"], name=node["name"], depth=node["depth"])
            try:
                versions = self.client.list_table_versions(node["id"])
            except Exception:
                versions = []
            for v in versions:
                ts = v.get("updatedAt", 0)
                ve = VersionEvent(
                    table_fqn=node["fqn"],
                    table_name=node["name"],
                    version=str(v.get("version", "?")),
                    timestamp_ms=ts,
                    updated_by=v.get("updatedBy", "?"),
                    change_summary=_summarize_change(v.get("changeDescription", {})),
                    hours_ago=round(_hours_between(_now_ms(), ts), 1) if ts else -1.0,
                )
                ut.version_events.append(ve)
                all_events.append(ve)
            upstream.append(ut)

        # 5. Also pull versions on the affected table itself (column changes etc)
        try:
            self_versions = self.client.list_table_versions(table["id"])
            for v in self_versions:
                ts = v.get("updatedAt", 0)
                ve = VersionEvent(
                    table_fqn=table_fqn,
                    table_name=table.get("name", "?"),
                    version=str(v.get("version", "?")),
                    timestamp_ms=ts,
                    updated_by=v.get("updatedBy", "?"),
                    change_summary=_summarize_change(v.get("changeDescription", {})),
                    hours_ago=round(_hours_between(_now_ms(), ts), 1) if ts else -1.0,
                )
                all_events.append(ve)
        except Exception:
            pass

        # 6. Downstream blast radius — fetch tier for each
        downstream: list[DownstreamAsset] = []
        for node in downstream_nodes:
            try:
                d_table = self.client.get_table_by_id(node["id"])
                tier = _extract_tier(d_table.get("tags", []) or [])
            except Exception:
                tier = None
            downstream.append(
                DownstreamAsset(fqn=node["fqn"], name=node["name"], depth=node["depth"], tier=tier)
            )

        # 7. Correlate — which events happened "around" the failure window?
        # We include events in the 7 days BEFORE the failure + a small grace window AFTER
        # (to handle test-reseed scenarios where version bumps are recorded near the failure).
        window_start = failure_ts_ms - self.suspicious_window_ms
        grace_after_ms = 60 * 60 * 1000  # 1 hour grace after failure timestamp
        window_end = failure_ts_ms + grace_after_ms
        suspicious = sorted(
            [e for e in all_events if window_start <= e.timestamp_ms <= window_end],
            key=lambda e: e.timestamp_ms,
            reverse=True,
        )
        
        return Evidence(
            test_fqn=test_case_fqn,
            test_name=tc.get("name", "unknown"),
            test_definition=test_definition,
            test_status=status,
            test_result_summary=result_summary,
            failure_timestamp_ms=failure_ts_ms,
            failure_hours_ago=round(_hours_between(_now_ms(), failure_ts_ms), 1),
            affected_table_fqn=table_fqn,
            affected_table_description=table_description,
            affected_column=column,
            affected_table_tier=table_tier,
            upstream=upstream,
            downstream=downstream,
            suspicious_events=suspicious,
        )