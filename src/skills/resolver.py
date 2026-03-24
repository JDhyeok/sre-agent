"""
Skill Resolver — 온톨로지 조건을 평가해서 "어떤 서버에 어떤 스킬을 적용할 수 있는지" 결정.

SKILL.md의 scope/preconditions를 GraphStore(SQLite + NetworkX) 쿼리로 평가하여
해당 조건에 맞는 서버 목록을 반환한다.
"""

from __future__ import annotations

import logging
from typing import Any

from src.graph.store import GraphStore
from src.models.schemas import SkillMeta

logger = logging.getLogger(__name__)


class SkillResolver:
    def __init__(self, graph: GraphStore) -> None:
        self._g = graph

    def find_applicable_servers(self, skill: SkillMeta) -> list[dict[str, Any]]:
        """스킬의 scope 조건에 맞는 서버 목록을 반환한다."""
        all_servers = self._g.find_nodes("Server")
        matched: list[dict[str, Any]] = []
        scope = skill.scope

        for server in all_servers:
            if not self._matches_scope(server, scope):
                continue

            hostname = server.get("hostname", "")
            server_id = f"server:{hostname}"

            hosted_edges = self._g.get_edges_to(server_id, "HOSTED_ON")
            services = []
            for e in hosted_edges:
                svc = self._g.get_node(e["source"])
                if svc:
                    services.append(svc.get("name", ""))

            matched.append(
                {
                    "hostname": hostname,
                    "environment": server.get("environment"),
                    "business_impact": server.get("business_impact"),
                    "team_owner": server.get("team_owner"),
                    "maintenance_window": server.get("maintenance_window"),
                    "services": services,
                }
            )

        # staging/dev 먼저, business_impact 낮은 것 먼저
        env_order = {"dev": 0, "staging": 1}
        impact_order = {"low": 0, "medium": 1, "high": 2, "critical": 3}
        matched.sort(
            key=lambda s: (
                env_order.get(s.get("environment", ""), 2),
                impact_order.get(s.get("business_impact", ""), 4),
            )
        )

        logger.info(
            "skill_resolved",
            extra={"skill_id": skill.id, "matching_servers": len(matched)},
        )
        return matched

    def _matches_scope(self, server: dict[str, Any], scope: dict[str, Any]) -> bool:
        """서버가 스킬의 scope 조건에 맞는지 확인한다."""
        # OS 조건
        if "os" in scope:
            os_list = scope["os"]
            if isinstance(os_list, list):
                server_os = server.get("os", "")
                if not any(server_os.startswith(os_name) for os_name in os_list):
                    return False

        # 패키지 조건
        if "package" in scope:
            pkg_name = scope["package"]
            hostname = server.get("hostname", "")
            server_id = f"server:{hostname}"
            pkg_edges = self._g.get_edges_to(server_id, "INSTALLED_ON")
            installed_packages = []
            for e in pkg_edges:
                pkg = self._g.get_node(e["source"])
                if pkg:
                    installed_packages.append(pkg.get("name", ""))
            if pkg_name not in installed_packages:
                return False

        # 환경 조건
        if "environment" in scope:
            if server.get("environment") != scope["environment"]:
                return False

        return True

    def suggest_execution_order(
        self, skill: SkillMeta, servers: list[dict[str, Any]]
    ) -> list[list[dict[str, Any]]]:
        """서버를 실행 순서 그룹으로 나눈다.

        원칙:
        1. staging/dev 먼저 (canary)
        2. business_impact 낮은 것 먼저
        3. 같은 그룹 내에서는 병렬 실행 가능

        Returns:
            [group1, group2, ...] 순서대로 실행
        """
        groups: dict[int, list[dict[str, Any]]] = {}

        for server in servers:
            env = server.get("environment", "unknown")
            impact = server.get("business_impact", "medium")

            if env in ("dev", "staging"):
                priority = 0
            elif impact == "low":
                priority = 1
            elif impact == "medium":
                priority = 2
            elif impact == "high":
                priority = 3
            else:  # critical
                priority = 4

            groups.setdefault(priority, []).append(server)

        return [groups[k] for k in sorted(groups.keys())]
