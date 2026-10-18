"""decision_trace (Q6): why it's built this way."""

from api.db import Trace, graph

# Every decision affecting the service with its ADR doc, the chat message it was proposed in, who decided,
# what was rejected and why, and whether a newer decision replaced it. Graph: six joins from one node.
CYPHER = """
MATCH (s:Service {name: $service})<-[:AFFECTS]-(d:Decision)
OPTIONAL MATCH (d)-[:DOCUMENTED_IN]->(doc:Doc)
OPTIONAL MATCH (d)-[:PROPOSED_IN]->(m:Message)<-[:SENT]-(author:Person)
OPTIONAL MATCH (m)-[:IN_THREAD]->(th:Thread)
OPTIONAL MATCH (d)<-[:DECIDED]-(dec:Person)
OPTIONAL MATCH (d)-[rj:REJECTED]->(o:Option)
OPTIONAL MATCH (newer:Decision)-[:SUPERSEDES]->(d)
RETURN d.id AS decision, d.summary AS summary, d.status AS status, d.decided_at AS decided_at,
       collect(DISTINCT doc.id) AS docs,
       collect(DISTINCT {id: m.id, text: m.text, by: author.handle, channel: th.channel, at: m.sent_at}) AS origin,
       collect(DISTINCT dec.handle) AS deciders,
       collect(DISTINCT {text: o.text, reason: rj.reason}) AS rejected,
       newer.id AS superseded_by
ORDER BY decided_at DESC
"""


def decision_trace(service: str, g=None) -> dict:
    """Decisions that shaped `service`, newest first, with origin message, deciders and rejected options."""
    t = Trace(g or graph())
    rows = t.read(CYPHER, {"service": service})
    t.key("Service", service)
    for r in rows:
        r["origin"] = [m for m in r["origin"] if m["id"] is not None]
        r["rejected"] = [o for o in r["rejected"] if o["text"] is not None]
        t.key("Decision", r["decision"])
        for d in r["docs"]:
            t.key("Doc", d)
        for m in r["origin"]:
            t.key("Message", m["id"])
            t.key("Person", m["by"])
        for h in r["deciders"]:
            t.key("Person", h)
        t.key("Decision", r["superseded_by"])
    return t.result(rows)
