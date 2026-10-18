"""suspect_changes (Q13): what changed right before it broke."""

from api import scoring
from api.db import Trace, clock, graph

HOUR = 3_600_000

# The newest PRs on the service since $since: who wrote them (agent or human), who prompted the agent, and how
# they were reviewed. Graph: links the incident to authorship and review behaviour in one traversal.
CYPHER = """
MATCH (s:Service {name: $service})
WITH s
MATCH (pr:PR)-[:TOUCHES]->(s)
WHERE pr.merged_at >= $since
OPTIONAL MATCH (ag:CodingAgent)-[:AUTHORED]->(pr)
OPTIONAL MATCH (h:Person)-[:AUTHORED]->(pr)
OPTIONAL MATCH (pp:Person)-[:PROMPTED]->(pr)
OPTIONAL MATCH (rv:Person)-[r:REVIEWED]->(pr)
RETURN pr.id AS pr, pr.title AS title, pr.body AS body, pr.merged_at AS merged_at, pr.lines AS lines,
       ag.name AS agent_author, h.handle AS human_author, pp.handle AS prompted_by,
       collect({by: rv.handle, secs: r.secs, comments: r.comments}) AS reviews
ORDER BY merged_at DESC LIMIT 5
"""


def suspect_changes(service: str, since: int = None, hours: int = 72, g=None) -> dict:
    """PRs merged on `service` since `since` (epoch ms; default: the last `hours` hours of the demo clock).
    Each row adds hours_ago and rubber_stamped (every review was a stamp: < 120 s, 0 comments)."""
    g = g or graph()
    now, _ = clock(g)
    since = since if since is not None else now - hours * HOUR
    t = Trace(g)
    rows = t.read(CYPHER, {"service": service, "since": since})
    t.key("Service", service)
    for r in rows:
        r["reviews"] = [rv for rv in r["reviews"] if rv["by"] is not None]
        r["hours_ago"] = round((now - r["merged_at"]) / HOUR, 1)
        r["rubber_stamped"] = bool(r["reviews"]) and all(
            scoring.review_kind(rv["secs"], rv["comments"]) == "stamp" for rv in r["reviews"])
        t.key("PR", r["pr"])
        t.key("CodingAgent", r["agent_author"])
        t.key("Person", r["human_author"])
        t.key("Person", r["prompted_by"])
        for rv in r["reviews"]:
            t.key("Person", rv["by"])
    return t.result(rows)
