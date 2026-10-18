"""Learning loop (Block G): incident -> drafted lesson -> human approval -> rule the guard enforces."""

import json

import pytest
from fastapi.testclient import TestClient

from api import learn, main
from api.db import rows_of
from api.tools import check_decision, open_incident, resolve_incident
from test_agent import fake_llm

PROPOSAL = "remove the timeout in the fraud client, it's adding latency"   # Story 8, said to Claude Code

# What the plan expects the LLM to draft (06, Story 8), plus junk the validation must clean up.
DRAFT = {"summary": "Always keep a timeout and fallback on fraud-svc calls from payments-api.",
         "chosen_option": "always keep a timeout + fallback on fraud-svc calls",
         "rejected_option": "remove or disable the timeout on fraud-svc calls",
         "reason": "INC-042: PR-4471 removed the timeout, fraud-svc slowed down, payments-api hung and checkout went down",
         "keywords": "timeout|fraud*|Disable",
         "affects": ["fraud-svc", "made-up-svc"]}


def q(g, cypher, **p):
    return rows_of(g.ro_query(cypher, p))


def test_full_learning_loop(wg, monkeypatch):
    open_incident("payments-api", "marco", "payments-api down", downstream=["checkout-api"], g=wg)
    before = check_decision("payments-api", PROPOSAL, log=False, g=wg)
    assert before["verdict"] == "ok"                                     # nothing forbids it yet

    with pytest.raises(learn.LessonError, match="resolve it"):
        learn.draft_lesson("INC-042", g=wg)                              # only resolved incidents teach
    resolve_incident("INC-042", "marco", "PR-4471 removed the fraud-svc timeout", g=wg)

    sent = fake_llm(monkeypatch, [{"content": "Here you go:\n" + json.dumps(DRAFT)}])
    draft = learn.draft_lesson("INC-042", g=wg)
    assert "PR-4471" in sent[0]["messages"][1]["content"]                # the LLM saw the suspect change
    assert draft["decision_id"] == "LRN-042"
    assert draft["lesson"]["affects"] == ["payments-api", "fraud-svc"]   # root added, invented service dropped
    assert draft["lesson"]["keywords"] == "timeout fraud disable"
    assert not q(wg, "MATCH (d:Decision {id: 'LRN-042'}) RETURN d.id AS id")   # drafting writes nothing

    res = learn.approve_lesson("INC-042", draft["lesson"], "marco", g=wg)
    assert res["rows"][0]["decision"] == "LRN-042" and "Incident:INC-042" in res["keys"]
    d = q(wg, """MATCH (d:Decision {id: 'LRN-042'})-[:CAUSED_BY]->(i:Incident), (d)-[rj:REJECTED]->(o:Option)
                 MATCH (p:Person)-[:DECIDED]->(d)
                 RETURN d.status AS status, d.origin AS origin, i.id AS inc, o.text AS rejected, p.handle AS by""")
    assert d == [{"status": "active", "origin": "learned", "inc": "INC-042",
                  "rejected": "remove or disable the timeout on fraud-svc calls", "by": "marco"}]

    after = check_decision("payments-api", PROPOSAL, caller="claude-code", g=wg)
    assert after["verdict"] == "block", after["reason"]
    assert "LRN-042" in after["reason"] and "INC-042" in after["reason"] and "@marco" in after["reason"]
    assert check_decision("fraud-svc", PROPOSAL, log=False, g=wg)["verdict"] == "block"     # affects both services
    assert check_decision("payments-api", "add a timeout and fallback to the fraud client", log=False,
                          g=wg)["verdict"] != "block"                                       # the chosen option is fine
    assert check_decision("payments-api", "bump the logging library", log=False, g=wg)["verdict"] == "ok"
    # How Claude Code actually described it over MCP (live run): "fraud-svc" used to become the full-text term
    # 'fraud-svc*', where '-' means NOT, which excluded this very rule -> "ok".
    claude = ("Remove the TIMEOUT_S timeout from the fraud-svc client in services/payments_api/fraud_client.py "
              "(http.post called without timeout)")
    assert check_decision("payments-api", claude, log=False, g=wg)["verdict"] == "block"
    # Same topic, not the rejected action (it keeps the timeout): not a block.
    assert check_decision("payments-api", "increase the fraud client timeout from 300 ms to 500 ms", log=False,
                          g=wg)["verdict"] != "block"

    # approving again (retry / double click) doesn't duplicate anything
    learn.approve_lesson("INC-042", draft["lesson"], "marco", g=wg)
    counts = q(wg, """MATCH (d:Decision {id: 'LRN-042'}) OPTIONAL MATCH (d)-[r]->(o:Option)
                      RETURN count(r) AS options""")
    assert counts == [{"options": 2}]


def test_approve_rejects_bad_input(wg):
    open_incident("ledger", "dana", "ledger slow", g=wg)
    inc = q(wg, "MATCH (i:Incident {status: 'open'})-[:IMPACTS]->(:Service {name: 'ledger'}) RETURN i.id AS id")[0]["id"]
    with pytest.raises(learn.LessonError, match="only resolved"):
        learn.approve_lesson(inc, DRAFT, "dana", g=wg)
    resolve_incident(inc, "dana", g=wg)
    with pytest.raises(learn.LessonError, match="unknown approver"):
        learn.approve_lesson(inc, DRAFT, "nobody", g=wg)
    with pytest.raises(learn.LessonError, match="missing"):
        learn.approve_lesson(inc, {**DRAFT, "rejected_option": ""}, "dana", g=wg)
    lrn = learn.decision_id_for(inc)
    assert not q(wg, "MATCH (d:Decision {id: $id}) RETURN d.id AS id", id=lrn)        # nothing half-written


def test_learning_loop_over_http(wg, monkeypatch):
    monkeypatch.setenv("FALKORDB_GRAPH", wg.name)
    with TestClient(main.app) as c:
        c.post("/reset")
        fake_llm(monkeypatch, [{"content": json.dumps({"summary": "x", "claims": [], "actions": [
            {"type": "open_incident", "assignee": "marco", "title": "payments-api down"}]})},
            {"content": json.dumps(DRAFT)}])
        sim = c.post("/incident/simulate", json={"service": "payments-api", "mode": "playbook"}).json()
        inc = sim["actions"][0]["id"]
        assert c.post(f"/incident/{inc}/lesson/draft").status_code == 409                # still open
        c.post(f"/incident/{inc}/resolve", json={"resolver": "marco", "root_cause": "PR-4471"})
        draft = c.post(f"/incident/{inc}/lesson/draft").json()
        edited = {**draft["lesson"], "summary": "Never ship a fraud-svc call without a timeout."}   # human edits the card
        ok = c.post("/lesson/approve", json={"incident_id": inc, "approver": "marco", "lesson": edited})
        assert ok.status_code == 200 and ok.json()["rows"][0]["summary"].startswith("Never ship")
        guard = c.post("/guard", json={"service": "payments-api", "proposal": PROPOSAL, "caller": "claude-code"}).json()
        assert guard["verdict"] == "block" and inc in guard["reason"]

        # the map now shows the gold rule wired to its (resolved) incident, and the guard call that hit it
        m = c.get("/map").json()
        nodes = {n["id"]: n for n in m["nodes"]}
        links = {(l["source"], l["target"], l["type"]) for l in m["links"]}
        assert nodes["Decision:LRN-042"]["origin"] == "learned" and nodes[f"Incident:{inc}"]["status"] == "resolved"
        assert ("Decision:LRN-042", f"Incident:{inc}", "CAUSED_BY") in links
        assert ("Decision:LRN-042", "Service:payments-api", "AFFECTS") in links
        gc = [n for n in m["nodes"] if n["type"] == "GuardCheck"]
        assert gc and gc[0]["verdict"] == "block" and gc[0]["caller"] == "claude-code"
        assert (gc[0]["id"], "Decision:LRN-042", "MATCHED") in links
        assert all(l["source"] in nodes and l["target"] in nodes for l in m["links"])
        assert len(m["nodes"]) <= 120

        assert c.post("/risk", json={"service": "ledger", "kind": "dark_zone", "score": 0.7}).json()["rows"][0]["id"] \
            == "RISK-dark_zone-ledger"
        s = c.get("/service/ledger").json()["stamp_chains"]["summary"]
        assert s["stamped_prs"] == 16 and s["under_90s"] == 16
