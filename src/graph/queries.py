"""
그래프 쿼리 헬퍼 — AI Agent와 API가 공통으로 사용하는 고수준 쿼리.
GraphStore 위에서 동작한다.
"""

from __future__ import annotations

import json

from src.graph.store import GraphStore


class GraphQueries:
    """GraphStore 위에서 동작하는 비즈니스 레벨 쿼리 모음."""

    def __init__(self, graph: GraphStore) -> None:
        self._g = graph

    def get_server_full(self, hostname: str) -> dict | None:
        """서버의 전체 맥락 (서비스, 패키지, 인증서, 주의사항) 반환."""
        server = self._g.get_node(f"server:{hostname}")
        if not server:
            return None

        # HOSTED_ON edge를 따라 서비스 찾기
        hosted_edges = self._g.get_edges_to(f"server:{hostname}", "HOSTED_ON")
        services = []
        for e in hosted_edges:
            svc = self._g.get_node(e["source"])
            if svc:
                services.append(svc)

        # INSTALLED_ON edge를 따라 패키지 찾기
        pkg_edges = self._g.get_edges_to(f"server:{hostname}", "INSTALLED_ON")
        packages = []
        for e in pkg_edges:
            pkg = self._g.get_node(e["source"])
            if pkg:
                packages.append(pkg)

        # AFFECTED edge를 따라 장애 이력 찾기
        inc_edges = self._g.get_edges_to(f"server:{hostname}", "AFFECTED")
        incidents = []
        for e in inc_edges:
            inc = self._g.get_node(e["source"])
            if inc:
                incidents.append(inc)

        return {
            "server": server,
            "services": services,
            "packages": packages,
            "incidents": incidents,
        }

    def find_servers_with_package(self, package_name: str) -> list[dict]:
        """특정 패키지가 설치된 서버 목록."""
        pkg_nodes = self._g.find_nodes("Package", name=package_name)
        results = []

        for pkg in pkg_nodes:
            edges = self._g.get_edges_from(pkg["_id"], "INSTALLED_ON")
            for e in edges:
                srv = self._g.get_node(e["target"])
                if srv:
                    results.append(
                        {
                            "hostname": srv.get("hostname"),
                            "environment": srv.get("environment"),
                            "business_impact": srv.get("business_impact"),
                            "package_version": pkg.get("version"),
                            "team_owner": srv.get("team_owner"),
                        }
                    )

        # critical 먼저 정렬
        impact_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        results.sort(key=lambda x: impact_order.get(x.get("business_impact", ""), 9))
        return results

    def find_unencrypted_calls(self, environment: str | None = None) -> list[dict]:
        """암호화되지 않은 서비스 간 통신 검색."""
        results = []
        for src, tgt, data in self._g.nx.edges(data=True):
            if data.get("_type") != "CALLS":
                continue
            if data.get("encrypted") is False or data.get("protocol") == "TCP":
                src_node = self._g.get_node(src) or {}
                self._g.get_node(tgt) or {}

                if environment:
                    src_srv = self._g.get_node(f"server:{src_node.get('server')}")
                    if src_srv and src_srv.get("environment") != environment:
                        continue

                results.append(
                    {
                        "source": src,
                        "target": tgt,
                        "protocol": data.get("protocol"),
                        "port": data.get("port"),
                    }
                )
        return results

    def get_dependency_chain(self, hostname: str, max_depth: int = 5) -> list[dict]:
        """서버를 기점으로 의존성 체인을 추적한다.

        이 서버의 서비스를 호출하는 upstream과
        이 서버의 서비스가 호출하는 downstream을 모두 반환.
        """
        server_id = f"server:{hostname}"

        # 이 서버에 호스팅된 서비스들
        hosted = self._g.get_edges_to(server_id, "HOSTED_ON")
        service_ids = [e["source"] for e in hosted]

        upstream_chains = []
        downstream_chains = []

        for svc_id in service_ids:
            # Upstream: 이 서비스를 호출하는 서비스들 (역방향)
            callers = self._g.get_edges_to(svc_id, "CALLS")
            for caller in callers:
                caller_node = self._g.get_node(caller["source"])
                upstream_chains.append(
                    {
                        "caller": caller["source"],
                        "caller_name": caller_node.get("name") if caller_node else None,
                        "callee": svc_id,
                        "protocol": caller.get("protocol"),
                        "port": caller.get("port"),
                    }
                )

            # Downstream: 이 서비스가 호출하는 서비스들
            callees = self._g.get_edges_from(svc_id, "CALLS")
            for callee in callees:
                callee_node = self._g.get_node(callee["target"])
                downstream_chains.append(
                    {
                        "caller": svc_id,
                        "callee": callee["target"],
                        "callee_name": callee_node.get("name") if callee_node else None,
                        "protocol": callee.get("protocol"),
                        "port": callee.get("port"),
                    }
                )

        return {
            "hostname": hostname,
            "services": service_ids,
            "upstream": upstream_chains,
            "downstream": downstream_chains,
            "blast_radius": self._g.blast_radius(server_id, max_depth),
        }

    def search_incidents(
        self, keyword: str | None = None, hostname: str | None = None
    ) -> list[dict]:
        """과거 장애를 검색한다."""
        all_incidents = self._g.find_nodes("Incident")

        if hostname:
            # hostname에 AFFECTED edge가 있는 것만 필터
            server_id = f"server:{hostname}"
            filtered = []
            for inc in all_incidents:
                edges = self._g.get_edges_from(inc["_id"], "AFFECTED")
                if any(e["target"] == server_id for e in edges):
                    filtered.append(inc)
            all_incidents = filtered

        if keyword:
            kw = keyword.lower()
            all_incidents = [
                inc
                for inc in all_incidents
                if kw in json.dumps(inc, default=str, ensure_ascii=False).lower()
            ]

        return all_incidents

    def get_expiring_certs(self, days: int = 30) -> list[dict]:
        """N일 내 만료 예정 인증서 목록."""
        from datetime import UTC, datetime, timedelta

        cutoff = (datetime.now(UTC) + timedelta(days=days)).isoformat()

        certs = self._g.find_nodes("Certificate")
        expiring = []
        for cert in certs:
            not_after = cert.get("not_after", "")
            if not_after and not_after <= cutoff:
                # 어떤 서비스에 바인딩되어 있는지
                edges = self._g.get_edges_to(cert["_id"], "BOUND_TO")
                bound_services = [e["source"] for e in edges]
                cert["bound_to_services"] = bound_services
                expiring.append(cert)

        return expiring
