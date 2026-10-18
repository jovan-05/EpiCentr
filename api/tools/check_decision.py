"""check_decision (Q11): the decision guard. Is this change something the team already rejected?"""

import re

from api.db import Trace, graph
from api.tools.log_guard import log_guard

# Full-text match of the proposal against REJECTED options of decisions that affect the service, with the reason,
# the incident that caused the decision, the ADR, and who to ask (active deciders only).
# Graph: the rule, its reason, its origin incident and its people are all one hop from the Decision.
CYPHER = """
CALL db.idx.fulltext.queryNodes('Option', $terms) YIELD node AS o, score
MATCH (d:Decision)-[rj:REJECTED]->(o)
MATCH (d)-[:AFFECTS]->(s:Service {name: $service})
OPTIONAL MATCH (d)-[:CHOSE]->(c:Option)
OPTIONAL MATCH (d)-[:DOCUMENTED_IN]->(doc:Doc)
OPTIONAL MATCH (d)-[:CAUSED_BY]->(inc:Incident)
OPTIONAL MATCH (d)<-[:DECIDED]-(who:Person)
OPTIONAL MATCH (newer:Decision)-[:SUPERSEDES]->(d)
RETURN d.id AS decision, d.summary AS summary, d.status AS status,
       o.id AS option, o.text AS rejected_option, o.keywords AS keywords, rj.reason AS reason,
       c.text AS chosen_option, c.keywords AS chosen_keywords,
       doc.id AS doc, doc.title AS doc_title, inc.id AS incident, inc.title AS incident_title,
       collect(DISTINCT CASE WHEN who.status = 'active' THEN who.handle END) AS ask,
       newer.id AS superseded_by, score
ORDER BY score DESC LIMIT 3
"""

STOP = {"this", "that", "with", "from", "into", "have", "make", "just", "please", "some", "when", "then", "than",
        "the", "and", "for", "our", "its", "it's", "should", "would", "could", "because", "code", "file"}


def terms_from_text(text: str) -> str:
    """Turn a plain-English proposal into full-text terms: 'remove the idempotency check' -> 'remove*|idempotency*|check*'.
    Words only (letters): in the full-text syntax '-' means NOT, so 'fraud-svc*' would EXCLUDE every option that
    mentions fraud-svc. Coding agents write names like that ("the fraud-svc client"), so hyphens split words."""
    words = []
    for w in re.findall(r"[a-z]{4,}", text.lower()):
        if w not in STOP and w not in words:
            words.append(w)
    return "|".join(f"{w}*" for w in words[:16])


def clean_terms(terms: str) -> str:
    """Terms a caller passes in: same rule, a '-' inside a term would turn it into a NOT, so it splits the term."""
    return "|".join(t for t in re.split(r"[|\s\-]+", terms) if t.strip("*%"))


def _stems(terms: str):
    return [t.strip("*%").lower()[:6] for t in terms.split("|") if t.strip("*%")]


def overlap(terms: str, option_text: str, keywords: str) -> int:
    """How many distinct proposal terms (6-letter prefix) appear in the rejected option. One shared word is weak."""
    words = re.findall(r"[a-z]+", f"{option_text} {keywords or ''}".lower())
    return sum(any(w.startswith(s) for w in words) for s in set(_stems(terms)))


def distinctive(terms: str, option_text: str, chosen_text: str) -> int:
    """Proposal terms found in the rejected option's TEXT but not in the chosen option's: what actually separates
    the two, usually the action ('Remove or disable the timeout' vs 'Keep the timeout'). A change that only shares
    the topic ('increase the fraud client timeout') doesn't do what was rejected, so it can't be a block."""
    rejected = re.findall(r"[a-z]+", (option_text or "").lower())
    chosen = re.findall(r"[a-z]+", (chosen_text or "").lower())
    return sum(any(w.startswith(s) for w in rejected) and not any(w.startswith(s) for w in chosen)
               for s in set(_stems(terms)))


def check_decision(service: str, proposal: str, terms: str = None, caller: str = "keystone", log: bool = True,
                   g=None) -> dict:
    """Check a proposed change to `service` against rejected options.
    verdict: 'block' (>= 2 terms shared with a rejected option of an active, unsuperseded decision,
                      closer to the rejected option than to the option the team chose, and sharing at least one
                      word only the rejected option has, usually its action: "remove", "disable"),
             'warn'  (>= 2 shared terms but ambiguous: as close to the chosen option, only the topic in common,
                      or the decision was superseded),
             or 'ok' (no match, or only 1 shared term).
    Every call is logged as a GuardCheck node unless log=False."""
    g = g or graph()
    # Callers often pass the code directory name (services/payments_api); graph names are kebab-case.
    service = service.strip().lower().replace("_", "-")
    terms = clean_terms(terms) if terms else terms_from_text(proposal)
    t = Trace(g)
    rows = t.read(CYPHER, {"service": service, "terms": terms})
    for r in rows:
        r["ask"] = [h for h in r["ask"] if h]
        r["overlap"] = overlap(terms, r["rejected_option"], r["keywords"])
        r["chosen_overlap"] = overlap(terms, r["chosen_option"] or "", r["chosen_keywords"])
        r["distinctive"] = distinctive(terms, r["rejected_option"], r["chosen_option"])
    rows.sort(key=lambda r: (-r["overlap"], -r["score"]))

    verdict, reason, top = "ok", "No rejected option matches this change.", rows[0] if rows else None
    if top and top["overlap"] >= 2 and top["overlap"] > top["chosen_overlap"] and top["distinctive"] >= 1 \
            and top["status"] == "active" and not top["superseded_by"]:
        verdict = "block"
    elif top and top["overlap"] >= 2:   # one shared word (e.g. "capture") is too weak to interrupt anyone
        verdict = "warn"
    if verdict != "ok":
        reason = (f"{'Blocked' if verdict == 'block' else 'Warning'} by {top['decision']}: "
                  f"'{top['rejected_option']}' was rejected ({top['reason']})"
                  + (f"; {top['incident']} {top['incident_title']}" if top["incident"] else "")
                  + (f"; superseded by {top['superseded_by']}" if top["superseded_by"] else "")
                  + (f". Ask {', '.join('@' + h for h in top['ask'])}." if top["ask"] else "."))
        t.key("Decision", top["decision"])
        t.key("Option", top["option"])
        t.key("Incident", top["incident"])
        t.key("Doc", top["doc"])
        for h in top["ask"]:
            t.key("Person", h)
    t.key("Service", service)

    guard_id = None
    if log:
        guard_id = log_guard(service, proposal, verdict, caller, top["decision"] if verdict != "ok" else None,
                             g=g)["rows"][0]["id"]
    return t.result(rows, verdict=verdict, reason=reason, terms=terms, guard_check=guard_id)
