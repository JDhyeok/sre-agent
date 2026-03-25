"""
FastAPI 메인 앱 — 단일 프로세스, SQLite, Background Correlator.

실행: python -m uvicorn src.api.main:app --host 0.0.0.0 --port 8000 --reload
또는: make api
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path

import structlog
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from src.agent.approval import (
    approve_plan,
    get_pending_plans,
    get_plan,
    reject_plan,
)
from src.agent.brain import SREAgentBrain
from src.config import settings
from src.correlator.otlp_adapter import process_otlp_logs
from src.correlator.receiver import enqueue_event, run_correlator_loop
from src.database import close_all, init_all_databases
from src.graph.queries import GraphQueries
from src.graph.store import GraphStore
from src.logging_config import setup_logging
from src.skills.loader import load_all_skills

logger = structlog.get_logger(__name__)

STATIC_DIR = Path(__file__).resolve().parent.parent.parent / "static"

graph: GraphStore | None = None
queries: GraphQueries | None = None
agent: SREAgentBrain | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global graph, queries, agent

    setup_logging()
    init_all_databases()
    graph = GraphStore()
    queries = GraphQueries(graph)
    agent = SREAgentBrain(graph)

    skills = load_all_skills()
    index_path = STATIC_DIR / "index.html"
    logger.info(
        "app_started",
        skills=len(skills),
        nodes=graph.nx.number_of_nodes(),
        dashboard_static=str(STATIC_DIR),
        dashboard_index_exists=index_path.is_file(),
    )
    if not index_path.is_file():
        logger.error(
            "dashboard_missing",
            expected_index=str(index_path),
            hint="Run from sre-agent repo root; static/index.html must exist.",
        )

    correlator_task = asyncio.create_task(run_correlator_loop(graph))

    yield

    correlator_task.cancel()
    close_all()


app = FastAPI(
    title="SRE Agent API",
    description="SRE 운영 자동화 에이전트 — 폐쇄망 대응, SQLite + NetworkX",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def dashboard():
    """대시보드 (토폴로지 · 승인 · 채팅)."""
    index = STATIC_DIR / "index.html"
    if not index.is_file():
        raise HTTPException(status_code=404, detail="Dashboard index not found")
    return FileResponse(index, media_type="text/html")


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ─── 이벤트 수신 (OTel Collector → 여기) ──────────────────


class IngestPayload(BaseModel):
    event_type: str = "network_connection"
    payload: dict


@app.post("/ingest/events")
async def ingest_event(data: IngestPayload):
    """OTel Collector Agent가 Tetragon 이벤트를 여기로 POST한다."""
    event_id = enqueue_event(data.event_type, data.payload)
    return {"status": "queued", "event_id": event_id}


@app.post("/ingest/events/batch")
async def ingest_events_batch(events: list[IngestPayload]):
    """여러 이벤트를 한 번에 받는다."""
    ids = [enqueue_event(e.event_type, e.payload) for e in events]
    return {"status": "queued", "count": len(ids)}


@app.post("/v1/logs")
async def ingest_otlp_logs(request: Request):
    """OTel Collector의 otlphttp exporter가 OTLP 포맷으로 보내는 엔드포인트."""
    import gzip as _gzip

    content_type = request.headers.get("content-type", "")
    if "protobuf" in content_type:
        return JSONResponse(
            status_code=415,
            content={"error": "Protobuf not supported. Set encoding: json in OTel config."},
        )

    body = await request.body()
    content_encoding = request.headers.get("content-encoding", "")
    if content_encoding == "gzip":
        body = _gzip.decompress(body)

    data = json.loads(body)
    count = process_otlp_logs(data)
    logger.info("otlp_logs_ingested", count=count)
    return JSONResponse(content={"partialSuccess": {}})


# ─── 온톨로지 조회 API ─────────────────────────────────────


@app.get("/api/servers")
async def list_servers():
    servers = graph.find_nodes("Server")
    return {"servers": servers, "count": len(servers)}


@app.get("/api/servers/{hostname}")
async def get_server(hostname: str):
    result = queries.get_server_full(hostname)
    if not result:
        raise HTTPException(status_code=404, detail=f"Server '{hostname}' not found")
    return result


@app.get("/api/servers/{hostname}/blast-radius")
async def get_blast_radius(hostname: str):
    return graph.blast_radius(f"server:{hostname}")


@app.get("/api/servers/{hostname}/dependencies")
async def get_dependencies(hostname: str, max_depth: int = 5):
    return queries.get_dependency_chain(hostname, max_depth)


@app.get("/api/topology")
async def get_topology():
    return graph.get_full_topology()


@app.get("/api/spof")
async def detect_spof(min_fan_in: int = 3):
    return {"spof": graph.find_spof(min_fan_in)}


@app.get("/api/packages/{package_name}/servers")
async def get_servers_by_package(package_name: str):
    servers = queries.find_servers_with_package(package_name)
    return {"package": package_name, "servers": servers, "count": len(servers)}


@app.get("/api/incidents")
async def search_incidents(keyword: str | None = None, hostname: str | None = None):
    return {"incidents": queries.search_incidents(keyword, hostname)}


@app.get("/api/certs/expiring")
async def get_expiring_certs(days: int = 30):
    return {"certs": queries.get_expiring_certs(days)}


@app.get("/api/skills")
async def list_skills():
    skills = load_all_skills()
    return {
        sid: {"name": s.name, "trigger": s.trigger, "risk": s.risk} for sid, s in skills.items()
    }


# ─── 승인 워크플로우 API ──────────────────────────────────


@app.get("/api/plans/pending")
async def list_pending_plans():
    plans = get_pending_plans()
    return {"plans": plans, "count": len(plans)}


@app.get("/api/plans/{plan_id}")
async def get_plan_detail(plan_id: str):
    plan = get_plan(plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail=f"Plan '{plan_id}' not found")
    return plan


class ApprovalAction(BaseModel):
    actor: str


@app.post("/api/plans/{plan_id}/approve")
async def approve(plan_id: str, action: ApprovalAction):
    success = approve_plan(plan_id, action.actor)
    if not success:
        raise HTTPException(
            status_code=400,
            detail=f"Plan '{plan_id}' is not in pending state",
        )
    return {"status": "approved", "plan_id": plan_id}


@app.post("/api/plans/{plan_id}/reject")
async def reject(plan_id: str, action: ApprovalAction):
    success = reject_plan(plan_id, action.actor)
    if not success:
        raise HTTPException(
            status_code=400,
            detail=f"Plan '{plan_id}' is not in pending state",
        )
    return {"status": "rejected", "plan_id": plan_id}


# ─── WebSocket Chat ───────────────────────────────────────


@app.websocket("/ws/chat")
async def websocket_chat(ws: WebSocket):
    await ws.accept()
    chat_agent = SREAgentBrain(graph)

    try:
        while True:
            data = await ws.receive_json()
            user_message = data.get("message", "")
            if not user_message:
                continue

            try:
                response = await chat_agent.chat(user_message)
                await ws.send_json({"type": "message", "content": response})
            except Exception as e:
                logger.error("chat_error", error=str(e))
                await ws.send_json({"type": "error", "content": str(e)})

    except WebSocketDisconnect:
        pass


# ─── Health ───────────────────────────────────────────────


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "nodes": graph.nx.number_of_nodes() if graph else 0,
        "edges": graph.nx.number_of_edges() if graph else 0,
    }
