"""Keystone API (FastAPI). Every endpoint is a thin wrapper over graph tools; FalkorDB does the work.

Run:  .venv\\Scripts\\uvicorn.exe api.main:app --reload --port 8000      (docs at http://localhost:8000/docs)

On startup, if the graph is empty it is loaded and derived from data/northwind (reseed on boot), so a
fresh deploy or a wiped volume heals itself.
"""

import json
import logging
import queue
import threading
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from api import config, learn, llm, scoring, seed
from api.agent import run_incident
from api.db import clock, graph
from api.map import build_map
from api.tools import (READ, blast_radius, check_decision, current_owners, danger_ranking, decision_trace, edges_between,
                       file_risk,
                       knowledge_spread, resolve_incident, services_down, set_service_status, stamp_chains, what_if,
                       who_knows)

log = logging.getLogger("keystone.api")


@asynccontextmanager
async def lifespan(app):
    g = graph()
    if seed.is_empty(g):
        log.warning("graph %s is empty: reseeding from data/northwind", g.name)
        log.warning("reseeded: %s", seed.reseed(g))
    yield


app = FastAPI(title="Keystone", description="Company brain on FalkorDB", version="0.1", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])  # no auth (CLAUDE.md)


def _bad_request(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except ValueError as ex:
        raise HTTPException(404, str(ex)) from ex


# ---------------------------------------------------------------- read

@app.get("/health")
def health():
    g = graph()
    now, week = clock(g)
    nodes = g.ro_query("MATCH (n) RETURN count(n)").result_set[0][0]
    return {"ok": True, "graph": g.name, "nodes": nodes, "clock": {"now": now, "week": week},
            "llm": {"key_set": bool(config.OPENROUTER_API_KEY), "models": config.LLM_MODELS}}


@app.get("/map")
def get_map(threshold: float = scoring.THRESHOLD):
    """Nodes + links for the 3D view: services (zone, agent_share, pagerank, status), teams, people, agents, open incidents."""
    return build_map(threshold=threshold)


@app.get("/service/{name}")
def service_detail(name: str):
    """Everything the side panel shows when a service is clicked."""
    g = graph()
    return {"service": name, "owners": current_owners(name, g=g), "who_knows": who_knows(name, g=g),
            "stamp_chains": stamp_chains(name, g=g), "knowledge_spread": knowledge_spread(name, g=g),
            "decisions": decision_trace(name, g=g), "blast_radius": blast_radius(name, g=g)}


@app.post("/tools/{name}")
def run_tool(name: str, args: dict = None):
    """Run any READ tool by name with JSON args (the Cypher panel / debugging)."""
    if name not in READ:
        raise HTTPException(404, f"unknown read tool {name!r}; have {sorted(READ)}")
    try:
        return READ[name](**(args or {}))
    except TypeError as ex:
        raise HTTPException(422, str(ex)) from ex


class EdgesIn(BaseModel):
    keys: list[str] = Field(..., max_length=400, examples=[["Person:marco", "PR:PR-4452", "Service:payments-api"]])
    focus: Optional[list[str]] = Field(None, max_length=400, examples=[["PR:PR-4452"]])


@app.post("/graph/edges")
def post_graph_edges(body: EdgesIn):
    """Real edges between evidence nodes (`focus`) and the other `keys`: the 3D view draws only lines that exist."""
    return edges_between(body.keys, body.focus)


class RiskIn(BaseModel):
    service: str = Field(..., examples=["ledger"])
    kind: str = Field("dark_zone", pattern="^(dark_zone|bus_factor|orphaned)$")
    score: float = 1.0


@app.post("/risk")
def post_risk(body: RiskIn):
    """A5: the comprehension scan acts by filing a Risk on a service (the map's "File risk" button)."""
    return _bad_request(file_risk, body.service, body.kind, body.score)


class WhatIf(BaseModel):
    gone: list[str] = Field(..., examples=[["marco"]])
    threshold: float = scoring.THRESHOLD


@app.post("/whatif")
def post_whatif(body: WhatIf):
    """Which services go dark/fragile if these people leave, and who should learn what (+ before/after zone counts)."""
    return what_if(body.gone, threshold=body.threshold)


class Down(BaseModel):
    down: list[str] = Field(..., min_length=1, examples=[["payments-api"]])


@app.post("/whatif/down")
def post_whatif_down(body: Down):
    """What breaks if these services go down: merged blast radius ranked by PageRank, and who to call. Nothing is written."""
    return services_down(body.down)


@app.get("/whatif/ranking")
def get_whatif_ranking(limit: int = 10):
    """Who's most dangerous to lose: single points of failure, with G2 betweenness alongside."""
    return danger_ranking(limit=limit)


class Guard(BaseModel):
    service: str = Field(..., examples=["ledger"])
    proposal: str = Field(..., examples=["remove the idempotency check in ledger to cut write latency"])
    terms: Optional[str] = None
    caller: str = "api"


@app.post("/guard")
def post_guard(body: Guard):
    """The decision guard (Q11). Called by the Claude Code hook and the MCP server. Logs a GuardCheck."""
    return check_decision(body.service, body.proposal, terms=body.terms, caller=body.caller)


# ---------------------------------------------------------------- incidents

class Simulate(BaseModel):
    service: str = Field("payments-api", examples=["payments-api"])
    mode: str = Field("playbook", pattern="^(agent|playbook)$")
    act: bool = True


def _simulate(service, mode, act, on_event=None):
    g = graph()
    set_service_status(service, "down", g=g)
    if on_event:
        on_event({"type": "down", "service": service})
    return run_incident(service, mode=mode, act=act, g=g, on_event=on_event)


@app.post("/incident/simulate")
def post_simulate(body: Simulate):
    """Take a service down and let the agent investigate and act. Returns the full result (40-90 s with free models)."""
    try:
        return _bad_request(_simulate, body.service, body.mode, body.act)
    except llm.LLMError as ex:
        raise HTTPException(503, f"LLM unavailable: {ex}") from ex


@app.get("/incident/simulate/stream")
def stream_simulate(service: str = "payments-api", mode: str = "playbook", act: bool = True):
    """Same as POST /incident/simulate, as Server-Sent Events: down, thinking, step (with keys + cypher), fallback,
    grounded, action, then result (or error). The UI animates each step as it arrives."""
    if mode not in ("agent", "playbook"):
        raise HTTPException(422, "mode must be agent or playbook")
    events = queue.Queue()

    def work():
        try:
            events.put({"type": "result", "result": _simulate(service, mode, act, on_event=events.put)})
        except Exception as ex:  # noqa: BLE001  (report any failure to the browser)
            events.put({"type": "error", "message": f"{type(ex).__name__}: {ex}"})
        finally:
            events.put(None)

    threading.Thread(target=work, daemon=True).start()

    def sse():
        while (ev := events.get()) is not None:
            yield f"event: {ev['type']}\ndata: {json.dumps(ev, default=str)}\n\n"

    return StreamingResponse(sse(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


class Resolve(BaseModel):
    resolver: str = Field(..., examples=["marco"])
    root_cause: str = Field("", examples=["PR-4471 removed the timeout on the fraud-svc call"])


@app.post("/incident/{incident_id}/resolve")
def post_resolve(incident_id: str, body: Resolve):
    """Q15 step 1: resolve, service back up, then re-derive so the resolver's understanding grows on the map."""
    g = graph()
    res = _bad_request(resolve_incident, incident_id, body.resolver, body.root_cause, g=g)
    service = res["rows"][0]["service"]
    before = _score(g, body.resolver, service)
    seed.derive(g)
    after = _score(g, body.resolver, service)
    return {**res, "understanding": {"person": body.resolver, "service": service, "before": before, "after": after}}


def _score(g, handle, service):
    rows = g.ro_query("MATCH (:Person {handle: $h})-[u:UNDERSTANDS]->(:Service {name: $s}) RETURN u.score",
                      {"h": handle, "s": service}).result_set
    return rows[0][0] if rows else 0.0


@app.post("/incident/{incident_id}/lesson/draft")
def post_lesson_draft(incident_id: str):
    """Q15 step 2: the LLM drafts a lesson from a RESOLVED incident. Nothing is written; the UI shows it as a card."""
    try:
        return learn.draft_lesson(incident_id)
    except learn.LessonError as ex:
        raise HTTPException(409, str(ex)) from ex
    except llm.LLMError as ex:
        raise HTTPException(503, f"LLM unavailable: {ex}") from ex


class Lesson(BaseModel):
    summary: str
    chosen_option: str
    rejected_option: str
    reason: str
    keywords: str = ""
    affects: list[str] = []


class Approve(BaseModel):
    incident_id: str = Field(..., examples=["INC-042"])
    approver: str = Field(..., examples=["marco"])
    lesson: Lesson


@app.post("/lesson/approve")
def post_lesson_approve(body: Approve):
    """Q15 step 3: a human approved (and maybe edited) the lesson; write it as a rule the guard enforces."""
    try:
        return learn.approve_lesson(body.incident_id, body.lesson.model_dump(), body.approver)
    except learn.LessonError as ex:
        raise HTTPException(409, str(ex)) from ex


# ---------------------------------------------------------------- admin

@app.post("/reset")
def post_reset():
    """Rebuild the demo graph from data/northwind (between video takes)."""
    return seed.reseed(graph())
