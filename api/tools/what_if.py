"""what_if (Q14): what happens to understanding if these people leave."""

from api import scoring
from api.db import Trace, graph

# Services whose expert count drops to 0 or 1 if $gone leave. Graph: re-runs the Q10 zone logic with
# people removed, without touching the data.
LEAVE = """
MATCH (s:Service)
OPTIONAL MATCH (p:Person {status: 'active'})-[u:UNDERSTANDS]->(s)
WHERE u.score >= $threshold
WITH s, collect(p.handle) AS before
WITH s, before, [h IN before WHERE NOT h IN $gone] AS after
WHERE size(after) < size(before) AND size(after) <= 1
RETURN s.name AS service, s.tier AS tier, before, after,
       CASE WHEN size(after) = 0 THEN 'dark' ELSE 'fragile' END AS new_zone
ORDER BY tier, size(after)
"""

# Best learners for one affected service: people who stay, already have some evidence on it, plus how much
# they work with the leavers. Graph: UNDERSTANDS and COLLABORATES_WITH combined in one score.
# Aggregate first, then add (FalkorDB rejects mixing sum() and a non-grouped value in one expression).
LEARNERS = """
MATCH (s:Service {name: $service})
WITH s
MATCH (l:Person {status: 'active'})-[u:UNDERSTANDS]->(s)
WHERE NOT l.handle IN $gone
OPTIONAL MATCH (l)-[c:COLLABORATES_WITH]->(g:Person) WHERE g.handle IN $gone
WITH l, u.score AS score, sum(c.shared) AS shared
WITH l, score, coalesce(shared, 0) AS shared, score + 0.5 * coalesce(shared, 0) AS readiness
RETURN l.handle AS learner, score, shared, readiness
ORDER BY readiness DESC LIMIT 2
"""


# Before/after expert counts for EVERY service, so the UI can show the company-wide diff
# ("dark zones 2 → 3"). Same zone rule as Q10: 0 experts = dark, 1 = fragile, 2+ = healthy.
ZONES = """
MATCH (s:Service)
OPTIONAL MATCH (p:Person {status: 'active'})-[u:UNDERSTANDS]->(s)
WHERE u.score >= $threshold
WITH s, collect(p.handle) AS before
RETURN s.name AS service, s.tier AS tier, size(before) AS n_before,
       size([h IN before WHERE NOT h IN $gone]) AS n_after
"""


def zone(n: int) -> str:
    return "dark" if n == 0 else "fragile" if n == 1 else "healthy"


def what_if(gone: list, threshold: float = scoring.THRESHOLD, g=None) -> dict:
    """Services that get worse if the people in `gone` leave, each with up to 2 suggested learners.
    `summary` = zone counts before/after over all services, plus how many tier-1 services get worse."""
    t = Trace(g or graph())
    rows = t.read(LEAVE, {"gone": gone, "threshold": threshold})
    for h in gone:
        t.key("Person", h)
    for r in rows:
        t.key("Service", r["service"])
        r["learners"] = t.read(LEARNERS, {"service": r["service"], "gone": gone})
        for lr in r["learners"]:
            t.key("Person", lr["learner"])
    all_zones = t.read(ZONES, {"gone": gone, "threshold": threshold})
    summary = {"before": {z: 0 for z in ("dark", "fragile", "healthy")}, "after": {z: 0 for z in ("dark", "fragile", "healthy")},
               "services": len(all_zones), "tier1_worse": sum(r["tier"] == 1 for r in rows)}
    for z in all_zones:
        summary["before"][zone(z["n_before"])] += 1
        summary["after"][zone(z["n_after"])] += 1
    return t.result(rows, summary=summary)
