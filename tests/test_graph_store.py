"""GraphStore 테스트 — SQLite + NetworkX 동기화, blast radius, SPOF."""

import os
import tempfile

import pytest

# 테스트용 data directory 설정
_tmpdir = tempfile.mkdtemp()
os.environ["DATA_DIRECTORY"] = _tmpdir

from src.database import init_all_databases  # noqa: E402
from src.graph.store import GraphStore  # noqa: E402


@pytest.fixture
def graph():
    init_all_databases()
    g = GraphStore()

    # 기본 토폴로지 구성: user → nginx → springboot → postgres
    g.upsert_node("server:web-042", "Server", {"hostname": "web-042", "environment": "production"})
    g.upsert_node("server:api-010", "Server", {"hostname": "api-010", "environment": "production"})
    g.upsert_node("server:db-001", "Server", {"hostname": "db-001", "environment": "production"})

    g.upsert_node("service:nginx@web-042", "Service", {"name": "nginx", "server": "web-042"})
    g.upsert_node("service:spring@api-010", "Service", {"name": "spring", "server": "api-010"})
    g.upsert_node("service:pg@db-001", "Service", {"name": "pg", "server": "db-001"})

    g.upsert_edge("service:nginx@web-042", "server:web-042", "HOSTED_ON", {})
    g.upsert_edge("service:spring@api-010", "server:api-010", "HOSTED_ON", {})
    g.upsert_edge("service:pg@db-001", "server:db-001", "HOSTED_ON", {})

    g.upsert_edge("service:nginx@web-042", "service:spring@api-010", "CALLS", {"port": 8080})
    g.upsert_edge("service:spring@api-010", "service:pg@db-001", "CALLS", {"port": 5432})

    return g


def test_node_crud(graph: GraphStore):
    node = graph.get_node("server:web-042")
    assert node is not None
    assert node["hostname"] == "web-042"


def test_find_nodes(graph: GraphStore):
    servers = graph.find_nodes("Server", environment="production")
    assert len(servers) == 3


def test_edges(graph: GraphStore):
    edges = graph.get_edges_from("service:nginx@web-042", "CALLS")
    assert len(edges) == 1
    assert edges[0]["target"] == "service:spring@api-010"


def test_blast_radius(graph: GraphStore):
    """api-010을 내리면 nginx가 영향받아야 한다."""
    result = graph.blast_radius("server:api-010")
    assert result["node"] == "server:api-010"
    assert len(result["services"]) >= 1

    # nginx가 upstream으로 잡혀야 함
    affected_nodes = [a["node"] for a in result["affected"]]
    assert "service:nginx@web-042" in affected_nodes


def test_topology(graph: GraphStore):
    topo = graph.get_full_topology()
    assert topo["count"] == 2  # nginx→spring, spring→pg


def test_upsert_updates(graph: GraphStore):
    """같은 ID로 upsert하면 속성이 갱신된다."""
    graph.upsert_node("server:web-042", "Server", {"hostname": "web-042", "memo": "test"})
    node = graph.get_node("server:web-042")
    assert node["memo"] == "test"
    assert node["hostname"] == "web-042"  # 기존 속성 유지


def test_networkx_sync(graph: GraphStore):
    """SQLite에 쓰면 NetworkX에도 반영된다."""
    assert graph.nx.has_node("server:web-042")
    assert graph.nx.has_edge("service:nginx@web-042", "service:spring@api-010")
    assert graph.nx.number_of_nodes() == 6  # 3 servers + 3 services
    assert graph.nx.number_of_edges() == 5  # 3 HOSTED_ON + 2 CALLS
