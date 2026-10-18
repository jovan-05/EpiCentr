"""file_risk (A5): the comprehension scan acts by filing a Risk on a service."""

from api.db import Trace, clock, graph

# Create or update the risk for this service + kind (MERGE: re-running the scan doesn't duplicate it).
CYPHER = """
MATCH (s:Service {name: $service})
MERGE (r:Risk {id: $id})
SET r.kind = $kind, r.score = $score, r.created_at = $now
MERGE (r)-[:ON]->(s)
RETURN r.id AS id, r.kind AS kind, r.score AS score
"""


def file_risk(service: str, kind: str, score: float, g=None) -> dict:
    """kind: 'dark_zone' | 'bus_factor' | 'orphaned'. One Risk per (service, kind): RISK-<kind>-<service>."""
    assert kind in ("dark_zone", "bus_factor", "orphaned"), kind
    g = g or graph()
    now, _ = clock(g)
    t = Trace(g)
    rid = f"RISK-{kind}-{service}"
    rows = t.write(CYPHER, {"service": service, "id": rid, "kind": kind, "score": score, "now": now})
    t.key("Service", service)
    return t.result(rows)
