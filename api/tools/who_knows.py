"""who_knows (Q4): who can actually fix it, with evidence."""

from api.db import Trace, graph

# Active people ranked by comprehension score on the service, with their PRs there (newest first) as evidence.
# Graph: UNDERSTANDS is materialised, so this is one hop plus a 2-hop evidence lookup.
# Anchored on the Service index first (GRAPH.EXPLAIN showed 04's form does a Person label scan).
CYPHER = """
MATCH (s:Service {name: $service})
WITH s
MATCH (s)<-[k:UNDERSTANDS]-(p:Person) WHERE p.status = 'active'
OPTIONAL MATCH (p)-[:AUTHORED|REVIEWED]->(pr:PR)-[:TOUCHES]->(s)
WITH p, k, s, pr ORDER BY pr.merged_at DESC
RETURN p.handle AS person, p.role AS role, k.score AS score,
       k.authored AS authored, k.deep_reviews AS deep_reviews, k.light_reviews AS light_reviews,
       k.stamps AS stamps, k.last_seen AS last_seen,
       collect(pr.id) AS recent_prs
ORDER BY score DESC
LIMIT $k
"""


def who_knows(service: str, k: int = 5, g=None) -> dict:
    """Top `k` active people who understand `service`, best first, each with up to 3 recent PRs."""
    t = Trace(g or graph())
    rows = t.read(CYPHER, {"service": service, "k": k})
    t.key("Service", service)
    for r in rows:
        r["recent_prs"] = r["recent_prs"][:3]   # trim in Python; collect() slicing isn't supported
        t.key("Person", r["person"])
        for pr in r["recent_prs"]:
            t.key("PR", pr)
    return t.result(rows)
