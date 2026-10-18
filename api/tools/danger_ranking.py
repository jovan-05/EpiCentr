"""danger_ranking (Q14, "who's most dangerous to lose?"): single points of failure, compared with G2 betweenness."""

from api import scoring
from api.db import Trace, graph

# For every active person: the services where they are the ONLY expert (they leave -> dark) or one of exactly two
# (they leave -> fragile). One query instead of 04's per-person loop: removing one person from a service with
# n experts gives n-1, so only services with 1 or 2 experts can change zone. Graph: derived UNDERSTANDS edges.
SPOF = """
MATCH (s:Service)<-[u:UNDERSTANDS]-(p:Person {status: 'active'})
WHERE u.score >= $threshold
WITH s, collect(p.handle) AS experts
WHERE size(experts) <= 2
UNWIND experts AS h
RETURN h AS person,
       collect(CASE WHEN size(experts) = 1 THEN {service: s.name, tier: s.tier} END) AS dark,
       collect(CASE WHEN size(experts) = 2 THEN {service: s.name, tier: s.tier} END) AS fragile
"""

# G2: betweenness on the collaboration graph: people who sit on the paths between groups (fixed seed: reproducible).
BRIDGES = """
CALL algo.betweenness({nodeLabels: ['Person'], relationshipTypes: ['COLLABORATES_WITH'], samplingSize: 64, samplingSeed: 42})
YIELD node, score
RETURN node.handle AS person, node.status AS status, score
"""

PEOPLE = "MATCH (p:Person {status: 'active'}) OPTIONAL MATCH (p)-[m:MEMBER_OF]->(t:Team) WHERE m.valid_to IS NULL RETURN p.handle AS person, p.role AS role, t.slug AS team"

TIER_WEIGHT = {1: 3, 2: 2, 3: 1}


def danger_ranking(limit: int = 10, threshold: float = scoring.THRESHOLD, g=None) -> dict:
    """Active people ranked by what their leaving would do: risk = sum over services they'd turn dark
    (2 x tier weight) + services they'd turn fragile (tier weight), tier weights 1:3, 2:2, 3:1.
    Each row also has betweenness (G2) and its rank, to compare "dangerous to lose" with "bridge"."""
    t = Trace(g or graph())
    spof = {r["person"]: r for r in t.read(SPOF, {"threshold": threshold})}
    bridges = sorted(t.read(BRIDGES), key=lambda r: -r["score"])
    brank = {r["person"]: (i + 1, r["score"]) for i, r in enumerate(bridges)}
    rows = []
    for p in t.read(PEOPLE):
        s = spof.get(p["person"], {"dark": [], "fragile": []})
        dark = sorted(s["dark"], key=lambda x: x["tier"])
        fragile = sorted(s["fragile"], key=lambda x: x["tier"])
        risk = sum(2 * TIER_WEIGHT.get(x["tier"], 1) for x in dark) + sum(TIER_WEIGHT.get(x["tier"], 1) for x in fragile)
        b_rank, b = brank.get(p["person"], (None, 0.0))
        rows.append({**p, "dark": dark, "fragile": fragile, "risk": risk, "betweenness": b, "bridge_rank": b_rank})
    rows.sort(key=lambda r: (-r["risk"], -len(r["dark"]), -r["betweenness"]))
    rows = [r for r in rows if r["risk"] > 0][:limit]
    for r in rows:
        t.key("Person", r["person"])
        for x in r["dark"] + r["fragile"]:
            t.key("Service", x["service"])
    return t.result(rows)
