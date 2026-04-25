"""
Chronos — OpenMetadata REST API client.

Thin, typed wrapper around the 6 OpenMetadata endpoints Chronos needs:
  • list failing DQ tests
  • fetch a single test case
  • get a table (with tags, owners, columns)
  • walk lineage upstream/downstream
  • pull version history for a table
  • search (for downstream asset blast-radius lookup)

Design goals:
  • Explicit errors — never return None when something's wrong
  • Consistent typed dicts — callers don't guess at field names
  • Cheap retries on transient 5xx — OpenMetadata sometimes returns 502 during migrations
"""
from __future__ import annotations

import os
import time
from typing import Any
from urllib.parse import quote

import requests
from dotenv import load_dotenv

load_dotenv()


class OMError(RuntimeError):
    """Raised when OpenMetadata returns a non-transient error."""


class OMClient:
    def __init__(self, url: str | None = None, token: str | None = None):
        self.url = (url or os.environ["OM_URL"]).rstrip("/")
        self.token = token or os.environ["OM_TOKEN"]
        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

    # ---------- core HTTP ----------

    def _request(self, method: str, path: str, **kwargs) -> dict[str, Any]:
        url = f"{self.url}{path}"
        last_err: Exception | None = None
        for attempt in range(3):
            try:
                r = requests.request(method, url, headers=self.headers, timeout=15, **kwargs)
                if r.status_code == 404:
                    raise OMError(f"404 Not Found: {method} {path}")
                if 500 <= r.status_code < 600:
                    last_err = OMError(f"{r.status_code} on {path}: {r.text[:200]}")
                    time.sleep(0.5 * (attempt + 1))
                    continue
                if r.status_code >= 400:
                    raise OMError(f"{r.status_code} on {path}: {r.text[:400]}")
                return r.json() if r.content else {}
            except requests.RequestException as e:
                last_err = e
                time.sleep(0.5 * (attempt + 1))
        raise OMError(f"Giving up on {method} {path}: {last_err}")

    def get(self, path: str, **params) -> dict[str, Any]:
        return self._request("GET", path, params=params)

    # ---------- DQ tests ----------

    def list_failing_test_cases(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return test cases whose latest result is Failed."""
        resp = self.get(
            "/dataQuality/testCases",
            limit=limit,
            fields="testCaseResult,testDefinition",
            testCaseStatus="Failed",
        )
        return resp.get("data", [])

    def get_test_case(self, fqn: str) -> dict[str, Any]:
        """Fetch a single test case by fully-qualified name."""
        encoded = quote(fqn, safe="")
        return self.get(f"/dataQuality/testCases/name/{encoded}", fields="testCaseResult,testDefinition")

    # ---------- tables ----------

    def get_table(self, fqn: str) -> dict[str, Any]:
        """Fetch a table with columns, owners, tags, and version info."""
        encoded = quote(fqn, safe="")
        return self.get(
            f"/tables/name/{encoded}",
            fields="columns,owners,tags,usageSummary,domains",
        )

    def get_table_by_id(self, table_id: str) -> dict[str, Any]:
        return self.get(f"/tables/{table_id}", fields="columns,owners,tags,usageSummary,domains")

    # ---------- lineage ----------

    def get_lineage(self, table_fqn: str, upstream_depth: int = 3, downstream_depth: int = 3) -> dict[str, Any]:
        """Get lineage graph for a table."""
        encoded = quote(table_fqn, safe="")
        return self.get(
            f"/lineage/table/name/{encoded}",
            upstreamDepth=upstream_depth,
            downstreamDepth=downstream_depth,
        )

    def walk_upstream(self, table_fqn: str, max_depth: int = 3) -> list[dict[str, Any]]:
        """
        Walk upstream and return all upstream tables as a flat list.
        Each entry: {fqn, id, name, depth}
        """
        graph = self.get_lineage(table_fqn, upstream_depth=max_depth, downstream_depth=0)

        # OpenMetadata returns upstreamEdges as list of {fromEntity, toEntity}
        # where toEntity is the table closer to our root.
        nodes: dict[str, dict[str, Any]] = {}
        for node in graph.get("nodes", []) or []:
            nodes[node["id"]] = node

        root_id = graph.get("entity", {}).get("id")
        upstream_edges = graph.get("upstreamEdges", []) or []

        # BFS from root, follow upstreamEdges backwards
        visited: set[str] = set()
        result: list[dict[str, Any]] = []
        # depth 0 = root, we want depth 1..max_depth
        frontier = [(root_id, 0)]
        while frontier:
            current_id, depth = frontier.pop(0)
            if current_id in visited:
                continue
            visited.add(current_id)
            if depth > 0:
                node = nodes.get(current_id, {"id": current_id})
                result.append({
                    "id": current_id,
                    "fqn": node.get("fullyQualifiedName", node.get("name", "?")),
                    "name": node.get("name", "?"),
                    "depth": depth,
                })
            if depth < max_depth:
                # find all edges where toEntity.id == current_id; add fromEntity.id
                for edge in upstream_edges:
                    to_id = edge.get("toEntity", edge.get("toId"))
                    from_id = edge.get("fromEntity", edge.get("fromId"))
                    # handle both dict and bare-id shapes
                    if isinstance(to_id, dict):
                        to_id = to_id.get("id")
                    if isinstance(from_id, dict):
                        from_id = from_id.get("id")
                    if to_id == current_id and from_id:
                        frontier.append((from_id, depth + 1))

        return result

    def walk_downstream(self, table_fqn: str, max_depth: int = 3) -> list[dict[str, Any]]:
        """Mirror of walk_upstream — used for blast-radius reporting."""
        graph = self.get_lineage(table_fqn, upstream_depth=0, downstream_depth=max_depth)

        nodes: dict[str, dict[str, Any]] = {}
        for node in graph.get("nodes", []) or []:
            nodes[node["id"]] = node

        root_id = graph.get("entity", {}).get("id")
        downstream_edges = graph.get("downstreamEdges", []) or []

        visited: set[str] = set()
        result: list[dict[str, Any]] = []
        frontier = [(root_id, 0)]
        while frontier:
            current_id, depth = frontier.pop(0)
            if current_id in visited:
                continue
            visited.add(current_id)
            if depth > 0:
                node = nodes.get(current_id, {"id": current_id})
                result.append({
                    "id": current_id,
                    "fqn": node.get("fullyQualifiedName", node.get("name", "?")),
                    "name": node.get("name", "?"),
                    "depth": depth,
                })
            if depth < max_depth:
                for edge in downstream_edges:
                    from_id = edge.get("fromEntity", edge.get("fromId"))
                    to_id = edge.get("toEntity", edge.get("toId"))
                    if isinstance(from_id, dict):
                        from_id = from_id.get("id")
                    if isinstance(to_id, dict):
                        to_id = to_id.get("id")
                    if from_id == current_id and to_id:
                        frontier.append((to_id, depth + 1))
        return result

    # ---------- version history (powers Temporal Replay) ----------

    def list_table_versions(self, table_id: str) -> list[dict[str, Any]]:
        """
        Return all historical versions of a table, most recent first.
        Each entry has: version, updatedAt (ms), updatedBy, changeDescription
        """
        resp = self.get(f"/tables/{table_id}/versions")
        versions = resp.get("versions", [])

        # OpenMetadata sometimes returns versions as JSON-encoded strings — decode if so
        import json
        parsed: list[dict[str, Any]] = []
        for v in versions:
            if isinstance(v, str):
                try:
                    parsed.append(json.loads(v))
                except json.JSONDecodeError:
                    continue
            elif isinstance(v, dict):
                parsed.append(v)

        # Sort by updatedAt descending
        parsed.sort(key=lambda x: x.get("updatedAt", 0), reverse=True)
        return parsed

    # ---------- health check ----------

    def whoami(self) -> dict[str, Any]:
        return self.get("/users/loggedInUser")