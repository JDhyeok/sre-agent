"""
Graph Store — SQLite ↔ NetworkX 동기화.

쓰기: SQLite에 먼저 저장 → NetworkX 그래프도 갱신 (dual write)
읽기: 단순 조회 = SQLite, 그래프 순회 = NetworkX
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import UTC, datetime
from typing import Any

import networkx as nx

from src.database import get_ontology_db

logger = logging.getLogger(__name__)


class GraphStore:
    """SQLite 영속성 + NetworkX 인메모리 그래프."""

    def __init__(self) -> None:
        self._db: sqlite3.Connection = get_ontology_db()
        self._graph: nx.DiGraph = nx.DiGraph()
        self._load_from_db()

    def _load_from_db(self) -> None:
        """앱 시작 시 SQLite에서 전체 그래프를 NetworkX로 로드."""
        nodes = self._db.execute("SELECT id, type, properties FROM nodes").fetchall()
        for row in nodes:
            try:
                props = json.loads(row["properties"])
            except (json.JSONDecodeError, TypeError):
                logger.warning("corrupt_node_properties", extra={"id": row["id"]})
                props = {}
            props["_type"] = row["type"]
            self._graph.add_node(row["id"], **props)

        edges = self._db.execute(
            "SELECT source_id, target_id, type, properties FROM edges"
        ).fetchall()
        for row in edges:
            try:
                props = json.loads(row["properties"])
            except (json.JSONDecodeError, TypeError):
                logger.warning(
                    "corrupt_edge_properties",
                    extra={"source": row["source_id"], "target": row["target_id"]},
                )
                props = {}
            props["_type"] = row["type"]
            self._graph.add_edge(row["source_id"], row["target_id"], **props)

        logger.info(
            "graph_loaded",
            extra={"nodes": self._graph.number_of_nodes(), "edges": self._graph.number_of_edges()},
        )

    # ─── Node CRUD ────────────────────────

    def upsert_node(self, node_id: str, node_type: str, properties: dict[str, Any]) -> None:
        """노드를 생성하거나 갱신한다."""
        props_json = json.dumps(properties, default=str, ensure_ascii=False)
        now = datetime.now(UTC).isoformat()

        self._db.execute(
            """INSERT INTO nodes (id, type, properties, updated_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                 properties = json_patch(nodes.properties, excluded.properties),
                 updated_at = excluded.updated_at
            """,
            (node_id, node_type, props_json, now),
        )
        self._db.commit()

        # NetworkX 갱신
        merged = {**self._graph.nodes.get(node_id, {}), **properties, "_type": node_type}
        self._graph.add_node(node_id, **merged)

    def get_node(self, node_id: str) -> dict[str, Any] | None:
        """노드 속성을 반환한다."""
        row = self._db.execute(
            "SELECT type, properties FROM nodes WHERE id = ?", (node_id,)
        ).fetchone()
        if not row:
            return None
        props = json.loads(row["properties"])
        props["_id"] = node_id
        props["_type"] = row["type"]
        return props

    def find_nodes(self, node_type: str, **filters: Any) -> list[dict[str, Any]]:
        """타입으로 노드를 검색한다. filters는 properties JSON 내 필드 매칭."""
        rows = self._db.execute(
            "SELECT id, properties FROM nodes WHERE type = ?", (node_type,)
        ).fetchall()

        results = []
        for row in rows:
            props = json.loads(row["properties"])
            props["_id"] = row["id"]

            if all(props.get(k) == v for k, v in filters.items()):
                results.append(props)

        return results

    # ─── Edge CRUD ────────────────────────

    def upsert_edge(
        self, source_id: str, target_id: str, edge_type: str, properties: dict[str, Any]
    ) -> None:
        """엣지를 생성하거나 갱신한다."""
        props_json = json.dumps(properties, default=str, ensure_ascii=False)
        now = datetime.now(UTC).isoformat()

        self._db.execute(
            """INSERT INTO edges (source_id, target_id, type, properties, updated_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(source_id, target_id, type) DO UPDATE SET
                 properties = json_patch(edges.properties, excluded.properties),
                 updated_at = excluded.updated_at
            """,
            (source_id, target_id, edge_type, props_json, now),
        )
        self._db.commit()

        # NetworkX 갱신
        existing = self._graph.edges.get((source_id, target_id), {})
        merged = {**existing, **properties, "_type": edge_type}
        self._graph.add_edge(source_id, target_id, **merged)

    def get_edges_from(self, node_id: str, edge_type: str | None = None) -> list[dict]:
        """노드에서 나가는 엣지를 반환한다."""
        if edge_type:
            rows = self._db.execute(
                "SELECT target_id, type, properties FROM edges WHERE source_id = ? AND type = ?",
                (node_id, edge_type),
            ).fetchall()
        else:
            rows = self._db.execute(
                "SELECT target_id, type, properties FROM edges WHERE source_id = ?",
                (node_id,),
            ).fetchall()

        return [
            {"target": r["target_id"], "type": r["type"], **json.loads(r["properties"])}
            for r in rows
        ]

    def get_edges_to(self, node_id: str, edge_type: str | None = None) -> list[dict]:
        """노드로 들어오는 엣지를 반환한다."""
        if edge_type:
            rows = self._db.execute(
                "SELECT source_id, type, properties FROM edges WHERE target_id = ? AND type = ?",
                (node_id, edge_type),
            ).fetchall()
        else:
            rows = self._db.execute(
                "SELECT source_id, type, properties FROM edges WHERE target_id = ?",
                (node_id,),
            ).fetchall()

        return [
            {"source": r["source_id"], "type": r["type"], **json.loads(r["properties"])}
            for r in rows
        ]

    # ─── NetworkX 그래프 쿼리 ─────────────

    def blast_radius(self, node_id: str, max_depth: int = 5) -> dict:
        """노드를 제거했을 때 영향받는 모든 upstream을 찾는다.

        서버의 서비스들을 찾고, 각 서비스로 들어오는 CALLS 체인을 역추적.
        CALLS edge만 따라가며 BFS로 upstream caller를 탐색한다.
        """
        if node_id not in self._graph:
            return {"node": node_id, "affected": []}

        services = []
        for src, tgt, data in self._graph.in_edges(node_id, data=True):
            if data.get("_type") == "HOSTED_ON":
                services.append(src)

        affected = []
        for svc in services:
            visited: set[str] = set()
            queue: list[tuple[str, int]] = [(svc, 0)]
            while queue:
                current, depth = queue.pop(0)
                if depth >= max_depth:
                    continue
                for src, _, data in self._graph.in_edges(current, data=True):
                    if data.get("_type") == "CALLS" and src not in visited:
                        visited.add(src)
                        affected.append(
                            {
                                "node": src,
                                "type": self._graph.nodes[src].get("_type"),
                                "depth": depth + 1,
                                "path_to": svc,
                            }
                        )
                        queue.append((src, depth + 1))

        return {"node": node_id, "services": services, "affected": affected}

    def find_spof(self, min_fan_in: int = 3) -> list[dict]:
        """Single Point of Failure를 탐지한다.

        CALLS edge의 in-degree가 높은 서비스 중 단일 서버에만 있는 것.
        """
        spof_candidates = []

        for node in self._graph.nodes:
            node_data = self._graph.nodes[node]
            if node_data.get("_type") != "Service":
                continue

            # CALLS edge만 카운트
            calls_in = [
                src
                for src, _, d in self._graph.in_edges(node, data=True)
                if d.get("_type") == "CALLS"
            ]

            if len(calls_in) >= min_fan_in:
                spof_candidates.append(
                    {
                        "service": node,
                        "fan_in": len(calls_in),
                        "callers": calls_in,
                    }
                )

        return spof_candidates

    def get_full_topology(self) -> dict:
        """전체 서비스 토폴로지를 반환한다."""
        edges_out = []
        for src, tgt, data in self._graph.edges(data=True):
            if data.get("_type") == "CALLS":
                edges_out.append(
                    {
                        "source": src,
                        "target": tgt,
                        **{k: v for k, v in data.items() if not k.startswith("_")},
                    }
                )
        return {"edges": edges_out, "count": len(edges_out)}

    @property
    def nx(self) -> nx.DiGraph:
        """직접 NetworkX 그래프에 접근 (고급 쿼리용)."""
        return self._graph
