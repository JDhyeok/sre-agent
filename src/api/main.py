"""
FastAPI 메인 앱 — 단일 프로세스, SQLite, Background Correlator.

실행: python -m uvicorn src.api.main:app --host 0.0.0.0 --port 8000 --reload
또는: make api
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from src.agent.brain import SREAgentBrain
from src.config import settings
from src.correlator.receiver import enqueue_event, run_correlator_loop
from src.database import init_all_databases
from src.graph.store import GraphStore
from src.skills.loader import load_all_skills

logger = logging.getLogger(__name__)

graph: GraphStore | None = None
agent: SREAgentBrain | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global graph, agent

    # Startup
    init_all_databases()
    graph = GraphStore()
    agent = SREAgentBrain(graph)

    skills = load_all_skills()
    logger.info("app_started", extra={"skills": len(skills), "nodes": graph.nx.number_of_nodes()})

    # Background: Correlator 루프 시작
    correlator_task = asyncio.create_task(run_correlator_loop(graph))

    yield

    # Shutdown
    correlator_task.cancel()


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


# ─── 온톨로지 조회 API ─────────────────────────────────────


@app.get("/api/servers")
async def list_servers():
    servers = graph.find_nodes("Server")
    return {"servers": servers, "count": len(servers)}


@app.get("/api/servers/{hostname}")
async def get_server(hostname: str):
    node_id = f"server:{hostname}"
    server = graph.get_node(node_id)
    if not server:
        return {"error": "Not found"}, 404

    edges = graph.get_edges_to(node_id, "HOSTED_ON")
    services = [graph.get_node(e["source"]) for e in edges]

    return {"server": server, "services": services}


@app.get("/api/servers/{hostname}/blast-radius")
async def get_blast_radius(hostname: str):
    return graph.blast_radius(f"server:{hostname}")


@app.get("/api/topology")
async def get_topology():
    return graph.get_full_topology()


@app.get("/api/spof")
async def detect_spof(min_fan_in: int = 3):
    return {"spof": graph.find_spof(min_fan_in)}


@app.get("/api/skills")
async def list_skills():
    skills = load_all_skills()
    return {
        sid: {"name": s.name, "trigger": s.trigger, "risk": s.risk} for sid, s in skills.items()
    }


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
                logger.error("chat_error", extra={"error": str(e)})
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
