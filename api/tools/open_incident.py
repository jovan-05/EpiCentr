"""open_incident (A1): the agent acts. Creates the incident, its impacts and the assignee in the graph."""

import re

from api.db import Trace, clock, graph

# Existing incident ids, to pick the next number.
IDS = "MATCH (i:Incident) RETURN i.id AS id"

# Create the incident at the demo clock's "now", impacting the failing service and assigned to a person.
CREATE = """
MATCH (s:Service {name: $service}), (p:Person {handle: $assignee})
CREATE (i:Incident {id: $id, title: $title, severity: $severity, opened_at: $now, status: 'open', brief: $brief})
CREATE (i)-[:IMPACTS {role: 'root'}]->(s)
CREATE (i)-[:ASSIGNED_TO {reason: $reason}]->(p)
RETURN i.id AS id, i.opened_at AS opened_at
"""

# Downstream impacts. A separate statement: UNWIND of an empty list would return no rows from A1 as one query.
DOWNSTREAM = """
MATCH (i:Incident {id: $id})
UNWIND $downstream AS name
MATCH (x:Service {name: name})
CREATE (i)-[:IMPACTS {role: 'downstream'}]->(x)
"""

FIRST_NEW_ID = 42   # the video script calls the demo incident INC-042


def next_id(t):
    nums = [int(m.group(1)) for r in t.read(IDS) if (m := re.match(r"INC-(\d+)$", r["id"]))]
    return f"INC-{max([FIRST_NEW_ID - 1, *nums]) + 1:03d}"


def open_incident(service: str, assignee: str, title: str, brief: str = "", severity: str = "sev1",
                  reason: str = "", downstream: list = None, id: str = None, g=None) -> dict:
    """Open an incident on `service`, assigned to `assignee`, impacting `downstream` services too."""
    g = g or graph()
    now, _ = clock(g)
    t = Trace(g)
    inc = id or next_id(t)
    rows = t.write(CREATE, {"service": service, "assignee": assignee, "id": inc, "title": title, "severity": severity,
                            "brief": brief, "reason": reason, "now": now})
    if not rows:
        raise ValueError(f"unknown service {service!r} or person {assignee!r}")
    if downstream:
        t.write(DOWNSTREAM, {"id": inc, "downstream": downstream})
    t.key("Incident", inc)
    t.key("Service", service)
    t.key("Person", assignee)
    for s in downstream or []:
        t.key("Service", s)
    return t.result(rows)
