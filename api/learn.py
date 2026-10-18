"""Learning loop (Q15): a resolved incident becomes a rule every coding agent must respect.

episodic memory (Incident) -> human-approved lesson -> procedural memory (Decision + rejected Option) that
check_decision (the guard) matches from then on.

1. draft_lesson(incident_id): the LLM reads the incident and the changes that preceded it and proposes
   {summary, chosen_option, rejected_option, reason, keywords, affects}. NOTHING is written (human in the loop).
2. approve_lesson(incident_id, lesson, approver): writes it with the Q15 statements (each a MERGE, so a retry is
   safe), then re-derives understanding. The rule id comes from the incident (INC-042 -> LRN-042).
"""

import re

from api import llm, seed
from api.db import Trace, clock, graph
from api.agent import parse_json
from api.tools import decision_trace, suspect_changes

HOUR = 3_600_000

# The incident to learn from: its story, root service and resolver. Graph: one node and two hops.
INCIDENT = """
MATCH (i:Incident {id: $id})-[:IMPACTS {role: 'root'}]->(s:Service)
OPTIONAL MATCH (i)-[:RESOLVED_BY]->(p:Person)
RETURN i.id AS id, i.title AS title, i.brief AS brief, i.root_cause AS root_cause, i.status AS status,
       i.opened_at AS opened_at, s.name AS service, p.handle AS resolver
"""

SERVICES = "MATCH (s:Service) RETURN s.name AS name"

# Q15 3a: the lesson becomes an active Decision, marked as learned.
WRITE_DECISION = """
MERGE (d:Decision {id: $decision_id})
SET d.title = $title, d.summary = $summary, d.status = 'active', d.decided_at = $now, d.origin = 'learned'
"""

# Q15 3b: it exists because of this incident (the guard cites it).
WRITE_CAUSED_BY = """
MATCH (d:Decision {id: $decision_id}), (i:Incident {id: $incident_id})
MERGE (d)-[:CAUSED_BY]->(i)
"""

# Q15 3c: what to do, and what never to do again (the rejected option is what the guard full-text matches).
WRITE_OPTIONS = """
MATCH (d:Decision {id: $decision_id})
MERGE (c:Option {id: $decision_id + '-chosen'}) SET c.text = $chosen, c.keywords = $chosen_keywords
MERGE (r:Option {id: $decision_id + '-rejected'}) SET r.text = $rejected, r.keywords = $keywords
MERGE (d)-[:CHOSE]->(c)
MERGE (d)-[rj:REJECTED]->(r) SET rj.reason = $reason
"""

# Q15 3d: which services the rule protects.
WRITE_AFFECTS = """
MATCH (d:Decision {id: $decision_id})
UNWIND $affects AS svc
MATCH (s:Service {name: svc})
MERGE (d)-[:AFFECTS]->(s)
"""

# Q15 3e: the human who approved it is a decider ("ask @marco").
WRITE_DECIDED = """
MATCH (d:Decision {id: $decision_id}), (p:Person {handle: $approver})
MERGE (p)-[:DECIDED]->(d)
"""

DRAFT_PROMPT = """You turn a resolved incident into ONE rule that every AI coding agent must follow from now on.
Use only the facts below. Answer with ONE JSON object and nothing else:
{"summary": "the rule in one sentence",
 "chosen_option": "what to always do, short",
 "rejected_option": "the exact change that must never be made again, phrased as that change (e.g. 'remove or disable X')",
 "reason": "why, citing the incident id",
 "keywords": "5-10 lowercase words a future proposal to make that change would contain",
 "affects": ["the service the bad change was made in", "the service that change called, if any"]}
"affects" lists only those 1-2 services (where a coding agent could repeat the change), NOT every downstream service."""


class LessonError(ValueError):
    pass


def decision_id_for(incident_id):
    m = re.match(r"INC-(\d+)$", incident_id)
    if not m:
        raise LessonError(f"bad incident id {incident_id!r}")
    return f"LRN-{m.group(1)}"


def _incident(t, incident_id):
    rows = t.read(INCIDENT, {"id": incident_id})
    if not rows:
        raise LessonError(f"unknown incident {incident_id!r}")
    return rows[0]


def _clean_keywords(text):
    """'timeout|fraud*, Circuit' -> 'timeout fraud circuit' (plain words for the full-text index)."""
    words = re.findall(r"[a-z][a-z\-]+", (text or "").lower().replace("|", " "))
    return " ".join(dict.fromkeys(words))


def normalise(lesson, root_service, known_services):
    """Validate a drafted or human-edited lesson. Only real services; the incident's own service always included."""
    for k in ("summary", "chosen_option", "rejected_option", "reason"):
        if not isinstance(lesson.get(k), str) or not lesson[k].strip():
            raise LessonError(f"lesson field {k!r} is missing")
    affects = [s for s in (lesson.get("affects") or []) if s in known_services]
    if root_service not in affects:
        affects.insert(0, root_service)
    kw = lesson.get("keywords")
    kw = " ".join(kw) if isinstance(kw, list) else kw
    return {"summary": lesson["summary"].strip(), "chosen_option": lesson["chosen_option"].strip(),
            "rejected_option": lesson["rejected_option"].strip(), "reason": lesson["reason"].strip(),
            "keywords": _clean_keywords(kw) or _clean_keywords(lesson["rejected_option"]), "affects": affects}


def draft_lesson(incident_id, g=None):
    """Ask the LLM for a lesson. Returns {incident, decision_id, lesson, evidence_keys}; writes nothing."""
    g = g or graph()
    t = Trace(g)
    inc = _incident(t, incident_id)
    if inc["status"] != "resolved":
        raise LessonError(f"{incident_id} is {inc['status']}; resolve it before drafting a lesson")
    changes = suspect_changes(inc["service"], since=inc["opened_at"] - 72 * HOUR, g=g)
    decisions = decision_trace(inc["service"], g=g)
    facts = {
        "incident": {k: inc[k] for k in ("id", "title", "brief", "root_cause", "service", "resolver")},
        "changes_before_the_incident": [{k: r[k] for k in ("pr", "title", "body", "agent_author", "prompted_by",
                                                           "rubber_stamped", "hours_ago")} for r in changes["rows"]],
        "existing_decisions": [{k: r[k] for k in ("decision", "summary", "status")} for r in decisions["rows"]],
    }
    msg = llm.chat([{"role": "system", "content": DRAFT_PROMPT},
                    {"role": "user", "content": str(facts)}])
    raw = parse_json(msg.get("content"))
    if raw is None:
        raise llm.LLMError("model did not return a lesson JSON")
    known = {r["name"] for r in t.read(SERVICES)}
    lesson = normalise(raw, inc["service"], known)
    return {"incident": incident_id, "decision_id": decision_id_for(incident_id), "lesson": lesson,
            "model": msg.get("model"), "evidence_keys": changes["keys"] + [f"Incident:{incident_id}"]}


def approve_lesson(incident_id, lesson, approver, g=None):
    """Write the approved (possibly edited) lesson as a rule, then re-derive. Safe to call twice (MERGE)."""
    g = g or graph()
    now, _ = clock(g)
    t = Trace(g)
    inc = _incident(t, incident_id)
    if inc["status"] != "resolved":
        raise LessonError(f"{incident_id} is {inc['status']}; only resolved incidents become rules")
    if not t.read("MATCH (p:Person {handle: $h}) RETURN p.handle AS h", {"h": approver}):
        raise LessonError(f"unknown approver {approver!r}")
    known = {r["name"] for r in t.read(SERVICES)}
    lesson = normalise(lesson, inc["service"], known)
    did = decision_id_for(incident_id)
    p = {"decision_id": did, "incident_id": incident_id, "approver": approver, "now": now,
         "title": f"{did}: {lesson['summary']}", "summary": lesson["summary"],
         "chosen": lesson["chosen_option"], "chosen_keywords": _clean_keywords(lesson["chosen_option"]),
         "rejected": lesson["rejected_option"], "keywords": lesson["keywords"], "reason": lesson["reason"],
         "affects": lesson["affects"]}
    for stmt in (WRITE_DECISION, WRITE_CAUSED_BY, WRITE_OPTIONS, WRITE_AFFECTS, WRITE_DECIDED):
        t.write(stmt, p)
    seed.derive(g)   # Q15 step 4: re-derive understanding (the resolved incident counts for its resolver)
    for k in [("Decision", did), ("Option", f"{did}-rejected"), ("Option", f"{did}-chosen"),
              ("Incident", incident_id), ("Person", approver)] + [("Service", s) for s in lesson["affects"]]:
        t.key(*k)
    return t.result([{"decision": did, "status": "active", **lesson, "approved_by": approver}])
