"""FastAPI endpoints against a throwaway graph (fixture `wg`), with the scripted fake LLM from test_agent."""

import json

import pytest
from fastapi.testclient import TestClient

from api import main
from api.db import graph
from test_agent import ANSWER, fake_llm


@pytest.fixture()
def client(wg, monkeypatch):
    monkeypatch.setenv("FALKORDB_GRAPH", wg.name)
    with TestClient(main.app) as c:
        yield c


def test_reseed_on_boot(monkeypatch):
    monkeypatch.setenv("FALKORDB_GRAPH", "kt_boot")
    g = graph("kt_boot")
    try:
        g.delete()
    except Exception:  # noqa: BLE001
        pass
    with TestClient(main.app) as c:          # startup sees an empty graph and loads + derives it
        h = c.get("/health").json()
    assert h["ok"] and h["graph"] == "kt_boot" and h["nodes"] == 1416
    assert g.ro_query("MATCH ()-[u:UNDERSTANDS]->() RETURN count(u)").result_set[0][0] == 197
    g.delete()


def test_health(client):
    h = client.get("/health").json()
    assert h["ok"] and h["clock"]["week"] == "2026-W42"


def test_map(client):
    m = client.get("/map").json()
    nodes = {n["id"]: n for n in m["nodes"]}
    assert 80 <= m["counts"]["nodes"] <= 120                                     # 05: at most ~120 nodes on screen
    assert nodes["Service:ledger"]["zone"] == "dark" and nodes["Service:ledger"]["agent_share"] > 0.65
    assert nodes["Person:priya"]["status"] == "departed"
    assert max((n for n in m["nodes"] if n["type"] == "Service"), key=lambda n: n["pagerank"])["id"] == "Service:config-svc"
    types = {l["type"] for l in m["links"]}
    assert {"DEPENDS_ON", "OWNS", "MEMBER_OF", "UNDERSTANDS", "AGENT_WROTE"} <= types
    assert all(l["source"] in nodes and l["target"] in nodes for l in m["links"])  # no dangling links


def test_map_drops_links_to_unknown_nodes(client, wg):
    # An old guard check on a service name that doesn't exist must not produce a dangling link (it blanks the 3D view).
    wg.query("CREATE (:GuardCheck {id: 'GC-bad', verdict: 'ok', caller: 'mcp', service: 'payments_api', proposal: 'x', at: 1})")
    m = client.get("/map").json()
    ids = {n["id"] for n in m["nodes"]}
    assert "GuardCheck:GC-bad" in ids
    assert all(l["source"] in ids and l["target"] in ids for l in m["links"])


def test_service_detail_and_tools(client):
    d = client.get("/service/ledger").json()
    assert d["knowledge_spread"]["rows"][0]["learner"] == "ivy" and d["stamp_chains"]["rows"]
    r = client.post("/tools/who_knows", json={"service": "payments-api"}).json()
    assert r["rows"][0]["person"] == "marco"
    assert client.post("/tools/open_incident", json={}).status_code == 404       # write tools aren't exposed here


def test_whatif_and_guard(client):
    w = client.post("/whatif", json={"gone": ["marco"]}).json()
    assert w["rows"][0]["service"] == "payments-api" and w["rows"][0]["learners"][0]["learner"] == "ivy"
    assert w["summary"]["after"]["dark"] == w["summary"]["before"]["dark"] + 1
    d = client.post("/whatif/down", json={"down": ["payments-api"]}).json()
    assert d["summary"]["impacted"] == 6 and d["summary"]["call"][0]["person"] == "marco"
    assert client.post("/whatif/down", json={"down": []}).status_code == 422
    rk = client.get("/whatif/ranking").json()
    assert rk["rows"][0]["person"] == "marco"
    gd = client.post("/guard", json={"service": "ledger", "caller": "test",
                                     "proposal": "remove the idempotency check in ledger to cut write latency"}).json()
    assert gd["verdict"] == "block" and gd["guard_check"].startswith("GC-")


def test_simulate_stream_then_resolve(client, wg, monkeypatch):
    fake_llm(monkeypatch, [{"content": json.dumps(ANSWER)}])                    # playbook mode: one LLM call
    with client.stream("GET", "/incident/simulate/stream", params={"service": "payments-api"}) as resp:
        events = [json.loads(line[6:]) for line in resp.iter_lines() if line.startswith("data: ")]
    kinds = [e["type"] for e in events]
    assert kinds[0] == "down" and kinds.count("step") == 6 and kinds[-1] == "result"
    assert kinds.index("thinking") > kinds.index("step")                         # tools first, then the LLM writes
    assert next(e for e in events if e["type"] == "action")["action"]["type"] == "open_incident"
    first = next(e for e in events if e["type"] == "step")
    assert first["tool"] == "suspect_changes" and "PR:PR-4471" in first["keys"] and "MATCH" in first["cypher"]
    result = events[-1]["result"]
    inc = result["actions"][0]["id"]
    assert inc == "INC-042" and result["actions"][0]["assignee"] == "marco"
    m = {n["id"]: n for n in client.get("/map").json()["nodes"]}
    assert m["Service:payments-api"]["status"] == "down" and "Incident:INC-042" in m

    res = client.post(f"/incident/{inc}/resolve", json={"resolver": "marco", "root_cause": "PR-4471"}).json()
    assert res["rows"][0]["status"] == "resolved"
    u = res["understanding"]
    assert u["after"] == pytest.approx(u["before"] + 4.0)                         # resolving counts (weight 4, recent)
    m = {n["id"]: n for n in client.get("/map").json()["nodes"]}
    assert m["Service:payments-api"]["status"] == "up" and "Incident:INC-042" not in m

    assert client.post("/incident/INC-999/resolve", json={"resolver": "marco"}).status_code == 404
    assert client.post("/lesson/approve", json={}).status_code == 422          # body required (Block G)


def test_reset(client, wg):
    wg.query("MATCH (s:Service {name:'ledger'}) SET s.status = 'down'")
    out = client.post("/reset").json()
    assert out["nodes"] == 1416
    assert wg.ro_query("MATCH (s:Service {name:'ledger'}) RETURN s.status").result_set[0][0] == "up"
