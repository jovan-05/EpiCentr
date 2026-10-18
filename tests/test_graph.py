"""Load + derive against a real FalkorDB (fixture `g` in conftest.py), then check the stories in the graph."""

import check_stories
import derive
from api import scoring


def q(g, query, **params):
    return derive.rows(g.ro_query(query, params))


def test_counts_match_json(g):
    meta, _, _ = check_stories.load()
    n = g.ro_query("MATCH (n) RETURN count(n)").result_set[0][0]
    e = g.ro_query("MATCH ()-[r]->() WHERE NOT type(r) IN ['UNDERSTANDS', 'COLLABORATES_WITH'] RETURN count(r)").result_set[0][0]
    assert n == sum(meta["counts"]["nodes"].values()) + 1
    assert e == sum(meta["counts"]["edges"].values())


def test_graph_scores_equal_python_scores(g):
    """Every UNDERSTANDS score computed from graph evidence equals the score check_stories computes from JSON."""
    world = check_stories.World(*check_stories.load())
    graph = {(r["who"], r["svc"]): r["score"] for r in
             q(g, "MATCH (p:Person)-[u:UNDERSTANDS]->(s:Service) RETURN p.handle AS who, s.name AS svc, u.score AS score")}
    python = {k: u["score"] for k, u in world.understands.items()}
    assert graph.keys() == python.keys()
    assert all(abs(graph[k] - python[k]) < 1e-6 for k in graph)


def test_who_knows_payments_api(g):
    rows = q(g, derive.Q4, service="payments-api", k=10)
    people = [r["person"] for r in rows]
    assert people[0] == "marco"
    assert people.index("sam") > people.index("marco")


def test_comprehension_map(g):
    zones = {r["service"]: r for r in q(g, derive.Q10, now=g.now, threshold=scoring.THRESHOLD)}
    assert zones["ledger"]["zone"] == "dark" and abs(zones["ledger"]["agent_share"] - 0.70) < 0.01
    assert zones["payments-api"]["zone"] == "fragile" and zones["payments-api"]["experts"] == ["marco"]
    assert zones["checkout-api"]["zone"] == "healthy" and zones["checkout-api"]["human_experts"] >= 4


def test_what_if_marco_leaves(g):
    hit = q(g, derive.Q14, now=g.now, threshold=scoring.THRESHOLD, gone=["marco"])
    assert [(r["service"], r["new_zone"]) for r in hit] == [("payments-api", "dark")]
    assert q(g, derive.Q14_LEARNER, service="payments-api", gone=["marco"])[0]["learner"] == "ivy"


def test_algorithms(g):
    assert q(g, derive.G1)[0]["service"] == "config-svc"
    assert q(g, derive.G2)[0]["person"] == "marco"
    shadow = {"gus", "hana", "mei", "kenji", "noor"}
    spanning = [r for r in q(g, derive.G3) if {"payments", "data", "risk"} <= set(r["official_teams"])]
    assert any(shadow <= set(r["people"]) for r in spanning)
