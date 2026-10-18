"""current_owners (Q2): what the org chart says, and whether it's still true."""

from api.db import Trace, clock, graph

# The owning team, its current members, and who is on call this week. Graph: org chart and schedule
# are just edges with validity, so "is this still true" is a property check on the same hop.
CYPHER = """
MATCH (s:Service {name: $service})<-[o:OWNS]-(t:Team)
WHERE o.valid_to IS NULL
OPTIONAL MATCH (t)<-[m:MEMBER_OF]-(p:Person)
WHERE m.valid_to IS NULL
OPTIONAL MATCH (oc:Person)-[:ON_CALL_FOR {week: $week}]->(s)
RETURN t.slug AS team,
       collect(DISTINCT {handle: p.handle, status: p.status}) AS members,
       oc.handle AS on_call, oc.status AS on_call_status
"""


def current_owners(service: str, week: str = None, g=None) -> dict:
    """Owner team, members and this week's on-call for `service`.
    Each row adds `on_call_departed` (the schedule points at someone who left) and `active_members`.
    No rows means the service has no current owner (orphaned)."""
    g = g or graph()
    week = week or clock(g)[1]
    t = Trace(g)
    rows = t.read(CYPHER, {"service": service, "week": week})
    t.key("Service", service)
    for r in rows:
        r["members"] = [m for m in r["members"] if m["handle"] is not None]
        r["active_members"] = sum(m["status"] == "active" for m in r["members"])
        r["on_call_departed"] = r["on_call_status"] == "departed"
        t.key("Team", r["team"])
        t.key("Person", r["on_call"])
        for m in r["members"]:             # members too, so the UI can light up who the team actually is
            t.key("Person", m["handle"])
    return t.result(rows, orphaned=not rows)
