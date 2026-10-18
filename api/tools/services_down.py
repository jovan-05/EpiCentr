"""services_down (Q14, second half): what breaks if these services go down, weighted by PageRank (G1)."""

from api.db import Trace, graph
from api.tools.blast_radius import CYPHER as BLAST

# How much the company depends on each service: PageRank over DEPENDS_ON (G1). Graph: an algorithm over the
# whole dependency graph, so a tier-3 config service everything calls still ranks as critical.
PAGERANK = "CALL algo.pageRank('Service', 'DEPENDS_ON') YIELD node, score RETURN node.name AS name, node.tier AS tier, score"

# Who to call for each down service: the active person with the most evidence on it (Q4 anchor-first form).
CALL = """
MATCH (s:Service {name: $service})
WITH s
MATCH (s)<-[u:UNDERSTANDS]-(p:Person)
WHERE p.status = 'active'
RETURN p.handle AS person, u.score AS score
ORDER BY score DESC LIMIT 1
"""


def services_down(down: list, g=None) -> dict:
    """Every service that fails when the services in `down` fail (Q1 per service, merged), ranked by PageRank.
    Each row: service, tier, hops (nearest down service), via (which down service), owner_team, pagerank, share
    (its share of total PageRank). `summary`: impacted count, tier-1 count, share of the company's dependency
    weight that is down or impacted, and who to call for each down service."""
    t = Trace(g or graph())
    pr = {r["name"]: r for r in t.read(PAGERANK)}
    total = sum(r["score"] for r in pr.values()) or 1.0
    hit = {}
    for d in down:
        t.key("Service", d)
        for r in t.read(BLAST, {"service": d}):
            if r["service"] in down:
                continue
            old = hit.get(r["service"])
            if old is None or r["hops"] < old["hops"]:
                hit[r["service"]] = {**r, "via": d}
    rows = []
    for name, r in hit.items():
        score = pr.get(name, {}).get("score", 0.0)
        rows.append({"service": name, "tier": r["tier"], "hops": r["hops"], "via": r["via"], "owner_team": r["owner_team"],
                     "path": r["path"], "pagerank": score, "share": score / total})
        t.key("Service", name)
    rows.sort(key=lambda r: (-r["pagerank"], r["tier"]))
    callers = []
    for d in down:
        c = t.read(CALL, {"service": d})
        callers.append({"service": d, "person": c[0]["person"] if c else None, "score": c[0]["score"] if c else None})
        t.key("Person", c[0]["person"] if c else None)
    down_share = sum(pr.get(d, {}).get("score", 0.0) for d in down) / total
    summary = {"down": list(down), "impacted": len(rows), "tier1": sum(r["tier"] == 1 for r in rows),
               "share": round(down_share + sum(r["share"] for r in rows), 4), "call": callers}
    return t.result(rows, summary=summary)
