"""Write tools (the agent's actions), on their own graph copy (fixture `wg`)."""

import pytest

from api.db import rows_of
from api.tools import check_decision, cite, file_risk, log_guard, open_incident


def q(g, cypher, **params):
    return rows_of(g.ro_query(cypher, params))


def test_open_incident_and_cite(wg):
    res = open_incident("payments-api", "marco", "payments-api down", brief="fraud-svc hang after PR-4471",
                        reason="top who_knows; on-call priya departed", downstream=["checkout-api", "checkout-web"], g=wg)
    assert res["rows"][0]["id"] == "INC-042" and res["rows"][0]["opened_at"] == wg.now   # demo clock, video id
    impacts = q(wg, "MATCH (:Incident {id: 'INC-042'})-[r:IMPACTS]->(s:Service) RETURN s.name AS s, r.role AS role")
    assert sorted((r["s"], r["role"]) for r in impacts) == [
        ("checkout-api", "downstream"), ("checkout-web", "downstream"), ("payments-api", "root")]
    assert q(wg, "MATCH (:Incident {id:'INC-042'})-[:ASSIGNED_TO]->(p) RETURN p.handle AS h")[0]["h"] == "marco"

    # no downstream: must still create the incident (04's single-statement A1 would silently return nothing)
    assert open_incident("ledger", "dana", "ledger slow", g=wg)["rows"][0]["id"] == "INC-043"

    out = cite("INC-042", [{"claim": "Marco understands payments-api best", "keys": ["Person:marco", "PR:PR-4452"]},
                           {"claim": "PR-4471 removed the timeout", "keys": ["PR:PR-4471"]}], g=wg)
    assert out["rows"][0]["cited"] == 3
    cites = q(wg, "MATCH (:Incident {id:'INC-042'})-[c:CITES]->(n) RETURN c.claim AS claim, c.path_keys AS path")
    assert len(cites) == 3 and ["Person:marco", "PR:PR-4452"] in [c["path"] for c in cites]
    with pytest.raises(ValueError):
        cite("INC-042", [{"claim": "x", "keys": ["Bogus:1"]}], g=wg)


def test_open_incident_unknown_person(wg):
    with pytest.raises(ValueError):
        open_incident("payments-api", "nobody", "x", g=wg)


def test_file_risk_is_idempotent(wg):
    file_risk("ledger", "dark_zone", 0.7, g=wg)
    file_risk("ledger", "dark_zone", 0.9, g=wg)
    rows = q(wg, "MATCH (r:Risk)-[:ON]->(:Service {name:'ledger'}) RETURN r.id AS id, r.score AS score")
    assert rows == [{"id": "RISK-dark_zone-ledger", "score": 0.9}]


def test_guard_calls_are_logged(wg):
    res = check_decision("ledger", "remove the idempotency check", caller="claude-code", g=wg)
    gc = q(wg, "MATCH (g:GuardCheck {id: $id})-[:MATCHED]->(d:Decision) RETURN g.verdict AS v, g.caller AS c, d.id AS d",
           id=res["guard_check"])
    assert gc == [{"v": "block", "c": "claude-code", "d": "ADR-009"}]
    ok = log_guard("ledger", "bump deps", "ok", "test", g=wg)["rows"][0]["id"]
    assert q(wg, "MATCH (g:GuardCheck {id: $id}) OPTIONAL MATCH (g)-[m:MATCHED]->() RETURN count(m) AS n", id=ok)[0]["n"] == 0
