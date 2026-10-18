"""resolve_incident (Q15 step 1): close the incident, credit the resolver, bring the service back up."""

from api.db import Trace, clock, graph

# Resolve the incident at the demo clock's "now", link the resolver, and mark its root service up again.
# Graph: the resolved incident becomes evidence (Q3c, weight 4) the next time understanding is derived.
CYPHER = """
MATCH (i:Incident {id: $id}), (p:Person {handle: $resolver})
SET i.status = 'resolved', i.resolved_at = $now, i.root_cause = $root_cause
MERGE (i)-[:RESOLVED_BY]->(p)
WITH i
MATCH (i)-[:IMPACTS {role: 'root'}]->(s:Service)
SET s.status = 'up'
RETURN i.id AS id, s.name AS service, i.status AS status
"""


def resolve_incident(id: str, resolver: str, root_cause: str = "", g=None) -> dict:
    g = g or graph()
    now, _ = clock(g)
    t = Trace(g)
    rows = t.write(CYPHER, {"id": id, "resolver": resolver, "root_cause": root_cause, "now": now})
    if not rows:
        raise ValueError(f"unknown incident {id!r} or person {resolver!r}")
    t.key("Incident", id)
    t.key("Person", resolver)
    t.key("Service", rows[0]["service"])
    return t.result(rows)
