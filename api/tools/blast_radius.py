"""blast_radius (Q1): what breaks if this service goes down."""

from api.db import Trace, graph

# Everything upstream within 4 DEPENDS_ON hops, its shortest hop count, one shortest path, and its current owner team.
# Graph: a variable-length traversal in one query; SQL needs a recursive CTE per hop.
# (Hop bound is a literal: Cypher can't take *1..$n. max_hops filters in Python.)
CYPHER = """
MATCH p = (down:Service {name: $service})<-[:DEPENDS_ON*1..4]-(up:Service)
WITH up, p ORDER BY length(p)
WITH up, min(length(p)) AS hops, collect(p)[0] AS sample
OPTIONAL MATCH (up)<-[o:OWNS]-(t:Team) WHERE o.valid_to IS NULL
RETURN up.name AS service, up.tier AS tier, hops, t.slug AS owner_team,
       [n IN nodes(sample) | n.name] AS path
ORDER BY tier ASC, hops ASC
"""


def blast_radius(service: str, max_hops: int = 4, g=None) -> dict:
    """Services that fail when `service` fails, nearest and most critical first.
    Each row: service, tier, hops, owner_team, path (from the failing service outwards)."""
    t = Trace(g or graph())
    rows = [r for r in t.read(CYPHER, {"service": service}) if r["hops"] <= max_hops]
    t.key("Service", service)
    for r in rows:
        for name in r["path"]:
            t.key("Service", name)
        t.key("Team", r["owner_team"])
    return t.result(rows)
