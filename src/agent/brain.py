"""
AI Agent Brain — Claude API + GraphStore(SQLite/NetworkX) 컨텍스트 주입.
"""

from __future__ import annotations

import json
import logging
import uuid

import anthropic

from src.config import settings
from src.graph.store import GraphStore
from src.models.schemas import ApprovalStatus, ExecutionPlan
from src.skills.loader import load_all_skills

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
당신은 SRE 운영 자동화 에이전트입니다. 수십~수백 대 서버 인프라의 장애 대응, 
보안 패치, EoS 관리, PM 작업 등을 지원합니다.

## 핵심 규칙

1. 정보 조회와 분석은 즉시 수행합니다.
2. 서버 변경 작업은 반드시 실행 계획을 먼저 보여주고 사용자 승인을 받아야 합니다.
3. 항상 과거 장애 이력을 확인하고, 교훈이 있으면 계획에 반영합니다.
4. 서버별 주의사항(caution_notes)을 반드시 확인합니다.
5. business_impact가 critical인 서버는 특별히 주의합니다.
6. 답변은 한국어로 합니다.
"""


class SREAgentBrain:
    def __init__(self, graph: GraphStore) -> None:
        client_kwargs = {"api_key": settings.anthropic_api_key}
        if settings.anthropic_base_url:
            client_kwargs["base_url"] = settings.anthropic_base_url
        self._client = anthropic.AsyncAnthropic(**client_kwargs)
        self._graph = graph
        self._skills = load_all_skills()
        self._history: list[dict] = []

    def _build_tools(self) -> list[dict]:
        return [
            {
                "name": "query_server",
                "description": "서버 상세 정보 조회 (OS, 패키지, 서비스, 주의사항)",
                "input_schema": {
                    "type": "object",
                    "properties": {"hostname": {"type": "string"}},
                    "required": ["hostname"],
                },
            },
            {
                "name": "blast_radius",
                "description": "서버를 내릴 때 영향받는 전체 서비스 체인 분석",
                "input_schema": {
                    "type": "object",
                    "properties": {"hostname": {"type": "string"}},
                    "required": ["hostname"],
                },
            },
            {
                "name": "find_servers_by_package",
                "description": "특정 패키지가 설치된 서버 목록 검색",
                "input_schema": {
                    "type": "object",
                    "properties": {"package_name": {"type": "string"}},
                    "required": ["package_name"],
                },
            },
            {
                "name": "get_topology",
                "description": "서비스 간 통신 토폴로지 조회",
                "input_schema": {"type": "object", "properties": {}},
            },
            {
                "name": "find_incidents",
                "description": "과거 장애 이력 검색",
                "input_schema": {
                    "type": "object",
                    "properties": {"hostname": {"type": "string"}, "keyword": {"type": "string"}},
                },
            },
            {
                "name": "propose_plan",
                "description": "서버 변경 작업 실행 계획 생성 (사용자 승인 필요)",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "skill_id": {"type": "string"},
                        "target_servers": {"type": "array", "items": {"type": "string"}},
                        "reason": {"type": "string"},
                    },
                    "required": ["skill_id", "target_servers", "reason"],
                },
            },
        ]

    async def _execute_tool(self, name: str, inp: dict) -> str:
        if name == "query_server":
            hostname = inp["hostname"]
            server = self._graph.get_node(f"server:{hostname}")
            edges = self._graph.get_edges_to(f"server:{hostname}", "HOSTED_ON")
            services = [self._graph.get_node(e["source"]) for e in edges]
            result = {"server": server, "services": services}

        elif name == "blast_radius":
            result = self._graph.blast_radius(f"server:{inp['hostname']}")

        elif name == "find_servers_by_package":
            pkg_name = inp["package_name"]
            # 패키지 노드에서 INSTALLED_ON edge를 따라 서버 찾기
            pkg_nodes = self._graph.find_nodes("Package", name=pkg_name)
            servers = []
            for pkg in pkg_nodes:
                edges = self._graph.get_edges_from(pkg["_id"], "INSTALLED_ON")
                for e in edges:
                    srv = self._graph.get_node(e["target"])
                    if srv:
                        servers.append(srv)
            result = {"package": pkg_name, "servers": servers}

        elif name == "get_topology":
            result = self._graph.get_full_topology()

        elif name == "find_incidents":
            incidents = self._graph.find_nodes("Incident")
            keyword = inp.get("keyword", "")
            hostname = inp.get("hostname", "")
            if keyword:
                kw = keyword.lower()
                incidents = [i for i in incidents if kw in json.dumps(i, default=str).lower()]
            if hostname:
                # hostname에 AFFECTED edge가 있는 incident만
                filtered = []
                for inc in incidents:
                    edges = self._graph.get_edges_from(inc["_id"], "AFFECTED")
                    if any(f"server:{hostname}" == e["target"] for e in edges):
                        filtered.append(inc)
                incidents = filtered
            result = {"incidents": incidents}

        elif name == "propose_plan":
            caution_notes = []
            for h in inp["target_servers"]:
                srv = self._graph.get_node(f"server:{h}")
                if srv and srv.get("caution_notes"):
                    for note in srv["caution_notes"]:
                        caution_notes.append(f"[{h}] {note}")

            plan = ExecutionPlan(
                plan_id=f"PLAN-{uuid.uuid4().hex[:8]}",
                skill_id=inp["skill_id"],
                target_servers=inp["target_servers"],
                estimated_impact=f"{len(inp['target_servers'])}대 서버",
                caution_notes=caution_notes,
                approval_status=ApprovalStatus.PENDING,
            )
            result = plan.model_dump()
        else:
            result = {"error": f"Unknown tool: {name}"}

        return json.dumps(result, default=str, ensure_ascii=False)

    async def chat(self, user_message: str) -> str:
        self._history.append({"role": "user", "content": user_message})

        skills_summary = "\n".join(
            f"- {s.id}: {s.name} (trigger: {s.trigger}, risk: {s.risk})"
            for s in self._skills.values()
        )
        system = SYSTEM_PROMPT + f"\n\n## 사용 가능한 SKILL\n{skills_summary}"

        messages = list(self._history)

        for _ in range(10):  # max tool use rounds
            response = await self._client.messages.create(
                model=settings.anthropic_model,
                max_tokens=4096,
                system=system,
                tools=self._build_tools(),
                messages=messages,
            )

            if response.stop_reason == "end_turn":
                text = "".join(b.text for b in response.content if b.type == "text")
                self._history.append({"role": "assistant", "content": text})
                return text

            if response.stop_reason == "tool_use":
                messages.append({"role": "assistant", "content": response.content})
                tool_results = []
                for block in response.content:
                    if block.type == "tool_use":
                        logger.info("tool_call", extra={"tool": block.name, "input": block.input})
                        result = await self._execute_tool(block.name, block.input)
                        tool_results.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": block.id,
                                "content": result,
                            }
                        )
                messages.append({"role": "user", "content": tool_results})
                continue

            break

        return "응답을 생성하지 못했습니다."
