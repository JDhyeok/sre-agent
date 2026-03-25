/**
 * SRE Agent Dashboard — 토폴로지(그래프) / 승인 / 채팅
 */

/* global vis */

function escapeHtml(s) {
  const div = document.createElement("div");
  div.textContent = s;
  return div.innerHTML;
}

function wsUrl(path) {
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${window.location.host}${path}`;
}

async function fetchJson(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return r.json();
}

async function refreshHealth() {
  const el = document.getElementById("health");
  try {
    const h = await fetchJson("/health");
    el.innerHTML = `<span class="health-pill ok"><span class="dot"></span> nodes ${h.nodes} · edges ${h.edges}</span>`;
  } catch (e) {
    el.innerHTML = `<span class="health-pill"><span class="dot"></span> ${escapeHtml(String(e))}</span>`;
  }
}

// ─── Topology graph (vis-network) ───────────────────────────

let topologyNetwork = null;
let topologyLayoutMode = "hierarchical"; // 'hierarchical' | 'physics'
let topologyViewMode = "graph"; // 'graph' | 'table'
let lastTopology = { edges: [], count: 0 };

function shortNodeLabel(id) {
  const i = id.indexOf(":");
  if (i < 0) return id;
  return id.slice(i + 1);
}

function nodeStyle(id) {
  if (id.startsWith("service:")) {
    return { background: "#1a3352", border: "#3d8bfd", highlight: { background: "#243a5c", border: "#60a5fa" } };
  }
  if (id.startsWith("server:")) {
    return { background: "#164032", border: "#34d399", highlight: { background: "#1e4f3d", border: "#6ee7b7" } };
  }
  return { background: "#2a2f3d", border: "#64748b", highlight: { background: "#374151", border: "#94a3b8" } };
}

function buildVisData(edges) {
  const ids = new Set();
  for (const e of edges) {
    ids.add(e.source);
    ids.add(e.target);
  }
  const nodes = [...ids].map((id) => ({
    id,
    label: shortNodeLabel(id),
    title: id,
    color: nodeStyle(id),
    font: { color: "#e8edf4", size: 14, face: "system-ui, -apple-system, sans-serif" },
    margin: 10,
    shape: "box",
    shadow: true,
  }));

  const visEdges = edges.map((e, idx) => ({
    id: `e-${idx}`,
    from: e.source,
    to: e.target,
    label: [e.protocol || "TCP", e.port != null ? String(e.port) : ""].filter(Boolean).join(":"),
    font: { color: "#94a3b8", size: 11, strokeWidth: 0, align: "middle" },
    color: { color: "#5c7a9a", highlight: "#3d8bfd" },
    arrows: { to: { enabled: true, scaleFactor: 0.75 } },
    smooth: { type: "cubicBezier", forceDirection: "horizontal", roundness: 0.35 },
  }));

  return {
    nodes: new vis.DataSet(nodes),
    edges: new vis.DataSet(visEdges),
  };
}

function getTopologyOptions(layoutMode) {
  const common = {
    interaction: {
      hover: true,
      tooltipDelay: 150,
      navigationButtons: false,
      keyboard: true,
      zoomView: true,
      dragView: true,
    },
  };

  if (layoutMode === "hierarchical") {
    return {
      ...common,
      layout: {
        hierarchical: {
          enabled: true,
          direction: "UD",
          sortMethod: "directed",
          levelSeparation: 135,
          nodeSpacing: 125,
          treeSpacing: 200,
          shakeTowards: "roots",
          blockShifting: true,
          edgeMinimization: true,
        },
      },
      physics: false,
    };
  }

  return {
    ...common,
    layout: { hierarchical: false },
    physics: {
      enabled: true,
      barnesHut: {
        gravitationalConstant: -4200,
        centralGravity: 0.22,
        springLength: 200,
        springConstant: 0.055,
        damping: 0.55,
        avoidOverlap: 0.65,
      },
      stabilization: { iterations: 220, updateInterval: 25 },
    },
  };
}

function destroyTopologyNetwork() {
  if (topologyNetwork) {
    topologyNetwork.destroy();
    topologyNetwork = null;
  }
}

function renderTopologyGraph(edges) {
  const container = document.getElementById("topology-graph");
  const meta = document.getElementById("topology-meta");
  if (typeof vis === "undefined") {
    container.innerHTML =
      '<p class="empty">vis-network 번들을 불러오지 못했습니다. <code>/static/vendor/vis-network.min.js</code> 확인.</p>';
    meta.textContent = "";
    return;
  }

  destroyTopologyNetwork();
  container.innerHTML = "";

  if (!edges.length) {
    container.innerHTML =
      '<p class="empty">CALLS 엣지가 없습니다. 이벤트 수집 후 갱신됩니다.</p>';
    meta.textContent = "";
    return;
  }

  const data = buildVisData(edges);
  const options = getTopologyOptions(topologyLayoutMode);
  topologyNetwork = new vis.Network(container, data, options);

  const fitAnim = () => {
    topologyNetwork.fit({ animation: { duration: 320, easingFunction: "easeInOutQuad" } });
  };
  if (topologyLayoutMode === "hierarchical") {
    setTimeout(fitAnim, 80);
  } else {
    topologyNetwork.once("stabilizationIterationsDone", fitAnim);
  }

  topologyNetwork.on("doubleClick", (p) => {
    if (p.nodes && p.nodes.length === 1) {
      topologyNetwork.focus(p.nodes[0], {
        scale: 1.15,
        animation: true,
      });
    }
  });

  meta.textContent = `엣지 ${edges.length}개 · 드래그로 캔버스 이동 · 휠로 줌 · 노드에 마우스를 올리면 전체 ID · 더블클릭으로 포커스`;
}

function renderTopologyTable(edges) {
  const wrap = document.getElementById("topology-table");
  if (!edges.length) {
    wrap.innerHTML = '<p class="empty">CALLS 엣지가 없습니다.</p>';
    return;
  }
  const rows = edges
    .map(
      (e) =>
        `<tr><td>${escapeHtml(e.source)}</td><td>${escapeHtml(e.target)}</td>` +
        `<td>${escapeHtml(String(e.protocol ?? ""))}</td>` +
        `<td>${escapeHtml(String(e.port ?? ""))}</td>` +
        `<td>${escapeHtml(String(e.confidence ?? ""))}</td></tr>`,
    )
    .join("");
  wrap.innerHTML = `
    <div class="table-wrap">
      <table>
        <thead><tr><th>출발</th><th>도착</th><th>프로토콜</th><th>포트</th><th>신뢰도</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;
}

function syncTopologyPanels() {
  const graphEl = document.getElementById("topology-graph");
  const tableEl = document.getElementById("topology-table");
  const edges = lastTopology.edges || [];

  if (topologyViewMode === "graph") {
    tableEl.classList.add("hidden");
    graphEl.classList.remove("hidden");
    renderTopologyGraph(edges);
  } else {
    destroyTopologyNetwork();
    graphEl.classList.add("hidden");
    graphEl.innerHTML = "";
    tableEl.classList.remove("hidden");
    renderTopologyTable(edges);
  }

  const meta = document.getElementById("topology-meta");
  if (topologyViewMode === "table") {
    meta.textContent = `총 ${lastTopology.count ?? edges.length}개 엣지 (표 보기)`;
  }
}

function setTopoToolbarActive() {
  document.getElementById("topo-view-graph").classList.toggle("active", topologyViewMode === "graph");
  document.getElementById("topo-view-table").classList.toggle("active", topologyViewMode === "table");
  document.getElementById("topo-layout-hier").classList.toggle("active", topologyLayoutMode === "hierarchical");
  document.getElementById("topo-layout-physics").classList.toggle("active", topologyLayoutMode === "physics");
}

async function refreshTopology() {
  const errBanner = document.getElementById("topology-body");
  try {
    const t = await fetchJson("/api/topology");
    lastTopology = { edges: t.edges || [], count: t.count ?? (t.edges || []).length };
    errBanner.innerHTML = "";
    errBanner.classList.add("hidden");
    document.getElementById("topology-graph").classList.remove("hidden");
    syncTopologyPanels();
    setTopoToolbarActive();
  } catch (e) {
    destroyTopologyNetwork();
    document.getElementById("topology-graph").innerHTML = "";
    document.getElementById("topology-graph").classList.add("hidden");
    document.getElementById("topology-table").innerHTML = "";
    document.getElementById("topology-table").classList.add("hidden");
    document.getElementById("topology-meta").textContent = "";
    errBanner.classList.remove("hidden");
    errBanner.innerHTML = `<p class="empty">로드 실패: ${escapeHtml(String(e))}</p>`;
  }
}

function topologyFit() {
  if (topologyNetwork) {
    topologyNetwork.fit({ animation: { duration: 380, easingFunction: "easeInOutQuad" } });
  }
}

function onResizeTopology() {
  if (topologyNetwork && topologyViewMode === "graph") {
    topologyNetwork.redraw();
    topologyNetwork.fit();
  }
}

async function refreshIncidents() {
  const el = document.getElementById("incidents");
  try {
    const data = await fetchJson("/api/incidents");
    const list = data.incidents || [];
    if (list.length === 0) {
      el.innerHTML = '<p class="empty">등록된 인시던트가 없습니다.</p>';
      return;
    }
    const items = list.slice(0, 8).map((inc) => {
      const id = inc._id || inc.id || "";
      const sev = inc.severity ? ` · ${inc.severity}` : "";
      return `<li><strong>${escapeHtml(String(id))}</strong>${escapeHtml(sev)}</li>`;
    });
    el.innerHTML = `<ul class="incidents-list">${items.join("")}</ul>`;
  } catch (e) {
    el.innerHTML = `<p class="empty">${escapeHtml(String(e))}</p>`;
  }
}

function parseTargets(raw) {
  try {
    const t = JSON.parse(raw || "[]");
    return Array.isArray(t) ? t : [];
  } catch {
    return [];
  }
}

async function refreshPlans() {
  const el = document.getElementById("plans");
  try {
    const data = await fetchJson("/api/plans/pending");
    const plans = data.plans || [];
    if (plans.length === 0) {
      el.innerHTML = '<p class="empty">승인 대기 중인 실행 계획이 없습니다.</p>';
      return;
    }
    const cards = plans
      .map((p) => {
        const targets = parseTargets(p.targets);
        const tgtStr = targets.length ? targets.join(", ") : "(대상 없음)";
        return `
      <div class="plan-card" data-plan-id="${escapeHtml(p.plan_id)}">
        <header>
          <div>
            <span class="plan-id">${escapeHtml(p.plan_id)}</span>
            <div class="plan-meta">스킬: ${escapeHtml(p.skill_id || "")} · 요청: ${escapeHtml(p.requested_by || "")}</div>
          </div>
        </header>
        <div class="plan-meta">대상: ${escapeHtml(tgtStr)}</div>
        <div class="plan-actions">
          <button type="button" class="btn-success btn-small" data-action="approve">승인</button>
          <button type="button" class="btn-danger btn-small" data-action="reject">거부</button>
        </div>
      </div>`;
      })
      .join("");
    el.innerHTML = `<div class="plans-grid">${cards}</div>`;
    el.querySelectorAll(".plan-card").forEach((card) => {
      const id = card.getAttribute("data-plan-id");
      card.querySelector('[data-action="approve"]').addEventListener("click", () => {
        const actor = document.getElementById("actor").value.trim() || "operator";
        approvePlan(id, actor);
      });
      card.querySelector('[data-action="reject"]').addEventListener("click", () => {
        const actor = document.getElementById("actor").value.trim() || "operator";
        rejectPlan(id, actor);
      });
    });
  } catch (e) {
    el.innerHTML = `<p class="empty">로드 실패: ${escapeHtml(String(e))}</p>`;
  }
}

async function approvePlan(planId, actor) {
  try {
    const r = await fetch(`/api/plans/${encodeURIComponent(planId)}/approve`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ actor }),
    });
    if (!r.ok) {
      const err = await r.json().catch(() => ({}));
      throw new Error(err.detail || r.statusText);
    }
    appendChat("system", `계획 ${planId} 승인됨`);
    await refreshPlans();
  } catch (e) {
    appendChat("error", `승인 실패: ${e}`);
  }
}

async function rejectPlan(planId, actor) {
  try {
    const r = await fetch(`/api/plans/${encodeURIComponent(planId)}/reject`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ actor }),
    });
    if (!r.ok) {
      const err = await r.json().catch(() => ({}));
      throw new Error(err.detail || r.statusText);
    }
    appendChat("system", `계획 ${planId} 거부됨`);
    await refreshPlans();
  } catch (e) {
    appendChat("error", `거부 실패: ${e}`);
  }
}

let ws = null;

function appendChat(kind, text) {
  const box = document.getElementById("chat-messages");
  const role =
    kind === "user"
      ? "나"
      : kind === "assistant"
        ? "에이전트"
        : kind === "error"
          ? "오류"
          : "시스템";
  const cls = kind === "error" ? "msg error" : `msg ${kind}`;
  box.insertAdjacentHTML(
    "beforeend",
    `<div class="${cls}"><div class="msg-role">${escapeHtml(role)}</div><div class="msg-body">${escapeHtml(text)}</div></div>`,
  );
  box.scrollTop = box.scrollHeight;
}

function setChatStatus(text, className) {
  const s = document.getElementById("chat-status");
  s.textContent = text;
  s.className = "chat-status " + (className || "");
}

function connectWebSocket() {
  if (ws && ws.readyState === WebSocket.OPEN) return;
  setChatStatus("연결 중…", "");
  ws = new WebSocket(wsUrl("/ws/chat"));
  ws.onopen = () => setChatStatus("연결됨", "connected");
  ws.onclose = () => {
    setChatStatus("연결 끊김 — 아래 버튼으로 재연결", "error");
    ws = null;
  };
  ws.onerror = () => setChatStatus("WebSocket 오류", "error");
  ws.onmessage = (ev) => {
    try {
      const data = JSON.parse(ev.data);
      if (data.type === "message" && data.content) {
        appendChat("assistant", data.content);
      } else if (data.type === "error" && data.content) {
        appendChat("error", data.content);
      }
    } catch {
      appendChat("assistant", ev.data);
    }
  };
}

function sendChat() {
  const input = document.getElementById("chat-input");
  const text = input.value.trim();
  if (!text) return;
  if (!ws || ws.readyState !== WebSocket.OPEN) {
    appendChat("error", "WebSocket이 연결되지 않았습니다.");
    return;
  }
  appendChat("user", text);
  ws.send(JSON.stringify({ message: text }));
  input.value = "";
}

function refreshAll() {
  refreshHealth();
  refreshTopology();
  refreshIncidents();
  refreshPlans();
}

document.addEventListener("DOMContentLoaded", () => {
  document.getElementById("btn-refresh").addEventListener("click", refreshAll);
  document.getElementById("btn-refresh-chat").addEventListener("click", connectWebSocket);
  document.getElementById("btn-send").addEventListener("click", sendChat);
  const input = document.getElementById("chat-input");
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendChat();
    }
  });

  document.getElementById("topo-view-graph").addEventListener("click", () => {
    topologyViewMode = "graph";
    syncTopologyPanels();
    setTopoToolbarActive();
  });
  document.getElementById("topo-view-table").addEventListener("click", () => {
    topologyViewMode = "table";
    syncTopologyPanels();
    setTopoToolbarActive();
  });
  document.getElementById("topo-layout-hier").addEventListener("click", () => {
    topologyLayoutMode = "hierarchical";
    setTopoToolbarActive();
    if (topologyViewMode === "graph") {
      renderTopologyGraph(lastTopology.edges || []);
    }
  });
  document.getElementById("topo-layout-physics").addEventListener("click", () => {
    topologyLayoutMode = "physics";
    setTopoToolbarActive();
    if (topologyViewMode === "graph") {
      renderTopologyGraph(lastTopology.edges || []);
    }
  });
  document.getElementById("topo-fit").addEventListener("click", topologyFit);

  window.addEventListener("resize", () => {
    window.requestAnimationFrame(onResizeTopology);
  });

  refreshAll();
  connectWebSocket();
});
