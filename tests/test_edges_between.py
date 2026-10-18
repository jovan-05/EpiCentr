"""edges_between: the 3D view's evidence lines must be real edges in FalkorDB, with the right type and direction.

Regression: the UI used to guess lines from the order of a tool's keys (PR -> PR lines, PRs linked to the wrong
person). These tests feed the tools' real keys and check every returned line against the graph.
"""

import pytest
from fastapi.testclient import TestClient

from api import main
from api.db import KEYS
from api.tools import decision_trace, edges_between, suspect_changes, who_knows

EDGE = "MATCH (a:{la} {{{ka}: $va}})-[r:{t}]->(b:{lb} {{{kb}: $vb}}) RETURN count(r)"


def _exists(g, source, typ, target):
    """Is there an edge source -[typ]-> target? (labels/types come from KEYS and the tool's own rows, not user input)"""
    la, va = source.split(":", 1)
    lb, vb = target.split(":", 1)
    assert la in KEYS and lb in KEYS and typ.isupper()
    q = EDGE.format(la=la, ka=KEYS[la], t=typ, lb=lb, kb=KEYS[lb])
    return g.ro_query(q, {"va": va, "vb": vb}).result_set[0][0] > 0


def _evidence(keys):
    return [k for k in keys if k.split(":")[0] in ("PR", "Message", "Doc", "Ticket", "Decision")]


def test_contract_and_who_knows_lines_are_real(g):
    keys = who_knows("payments-api", g=g)["keys"]
    focus = _evidence(keys)
    r = edges_between(keys, focus, g=g)
    assert set(r) >= {"rows", "keys", "cypher", "params", "ms"}
    assert r["rows"], "who_knows evidence must connect to something"
    for row in r["rows"]:
        assert row["source"] in focus or row["target"] in focus          # every line touches new evidence
        assert not (row["source"].startswith("PR:") and row["target"].startswith("PR:"))   # no PR -> PR
        for t in row["types"]:
            assert _exists(g, row["source"], t, row["target"]), row
    # every evidence node gets at least one real line (none float alone)
    linked = {k for row in r["rows"] for k in (row["source"], row["target"])}
    assert set(focus) <= linked
    assert set(r["keys"]) == set(focus)


def test_who_knows_story_lines(g):
    keys = who_knows("payments-api", g=g)["keys"]
    rows = edges_between(keys, _evidence(keys), g=g)["rows"]
    pairs = {(x["source"], x["target"]): x["types"] for x in rows}
    pr = next(k for k in keys if k.startswith("PR:"))                    # marco's most recent PR
    assert any(s == "Person:marco" and t == pr for s, t in pairs)       # linked to its person, Person -> PR
    assert (pr, "Service:payments-api") in pairs                         # and to the service it touched


def test_suspect_change_lines(g):
    keys = suspect_changes("payments-api", g=g)["keys"]
    rows = edges_between(keys, ["PR:PR-4471"], g=g)["rows"]
    got = {(x["source"], x["target"]): x["types"] for x in rows}
    assert got[("CodingAgent:codegen-bot", "PR:PR-4471")] == ["AUTHORED"]
    assert got[("Person:ivy", "PR:PR-4471")] == ["PROMPTED"]
    assert got[("Person:sam", "PR:PR-4471")] == ["REVIEWED"]
    assert got[("PR:PR-4471", "Service:payments-api")] == ["TOUCHES"]


def test_decision_trace_lines(g):
    keys = decision_trace("payments-api", g=g)["keys"]
    rows = edges_between(keys, ["Decision:ADR-017", "Doc:DOC-ADR-017", "Message:MSG-PA-001"], g=g)["rows"]
    got = {(x["source"], x["target"]): x["types"] for x in rows}
    assert got[("Person:priya", "Message:MSG-PA-001")] == ["SENT"]
    assert got[("Decision:ADR-017", "Message:MSG-PA-001")] == ["PROPOSED_IN"]
    assert got[("Decision:ADR-017", "Doc:DOC-ADR-017")] == ["DOCUMENTED_IN"]
    assert ("Message:MSG-PA-001", "Person:dana") not in got and ("Person:dana", "Message:MSG-PA-001") not in got


def test_empty_and_unknown(g):
    assert edges_between(["Service:payments-api"], [], g=g)["rows"] == []
    r = edges_between(["PR:does-not-exist", "Service:payments-api", "junk"], g=g)
    assert r["rows"] == [] and r["keys"] == []


@pytest.fixture()
def client(wg, monkeypatch):
    monkeypatch.setenv("FALKORDB_GRAPH", wg.name)
    with TestClient(main.app) as c:
        yield c


def test_http(client):
    r = client.post("/graph/edges", json={"keys": ["PR:PR-4471", "Person:sam", "Service:payments-api"],
                                         "focus": ["PR:PR-4471"]})
    assert r.status_code == 200
    got = {(x["source"], x["target"]): x["types"] for x in r.json()["rows"]}
    assert got == {("Person:sam", "PR:PR-4471"): ["REVIEWED"], ("PR:PR-4471", "Service:payments-api"): ["TOUCHES"]}
    assert client.post("/graph/edges", json={}).status_code == 422
