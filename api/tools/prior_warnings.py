"""prior_warnings (Q7): did anyone see this coming?"""

from api.db import Trace, graph
from api.tools.check_decision import clean_terms

# Chat messages that match the terms AND mention the service, with author and channel.
# Graph: full-text search narrowed by a MENTIONS hop, so a warning about another service never shows up.
# $terms uses RediSearch syntax: | is OR, * is a prefix, %term% is fuzzy (e.g. "timeout|circuit*|%risk%").
MESSAGES = """
CALL db.idx.fulltext.queryNodes('Message', $terms) YIELD node, score
MATCH (node)-[:MENTIONS]->(s:Service {name: $service})
MATCH (author:Person)-[:SENT]->(node)-[:IN_THREAD]->(th:Thread)
RETURN node.id AS id, node.text AS text, author.handle AS by, th.channel AS channel,
       node.sent_at AS at, score
ORDER BY score DESC
LIMIT 5
"""

# Tickets about the service that match the terms and are not done: the warning someone filed and nobody fixed.
TICKETS = """
CALL db.idx.fulltext.queryNodes('Ticket', $terms) YIELD node, score
MATCH (node)-[:ABOUT]->(s:Service {name: $service})
WHERE node.status <> 'done'
OPTIONAL MATCH (who:Person)-[:ASSIGNED]->(node)
RETURN node.key AS id, node.title AS text, who.handle AS by, node.status AS status,
       node.created_at AS at, score
ORDER BY score DESC
LIMIT 5
"""


def prior_warnings(service: str, terms: str, g=None) -> dict:
    """Messages and still-open tickets about `service` matching `terms`. Rows have kind 'message' or 'ticket'."""
    t = Trace(g or graph())
    params = {"service": service, "terms": clean_terms(terms)}   # a '-' in a term would mean NOT
    msgs = [{"kind": "message", **r} for r in t.read(MESSAGES, params)]
    tickets = [{"kind": "ticket", **r} for r in t.read(TICKETS, params)]
    t.key("Service", service)
    for r in msgs:
        t.key("Message", r["id"])
        t.key("Person", r["by"])
    for r in tickets:
        t.key("Ticket", r["id"])
        t.key("Person", r["by"])
    return t.result(msgs + tickets)
