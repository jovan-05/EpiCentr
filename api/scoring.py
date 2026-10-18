"""Comprehension score: the single source of truth for weights and thresholds.

Used by scripts/check_stories.py (on the generated JSON), scripts/derive.py (on evidence
rows pulled from FalkorDB with the Q3 queries) and the api tools (THRESHOLD).
Every number here is meant to be explained to a judge in one sentence.
"""

DAY_MS = 86_400_000

# Weight per piece of evidence that a person understands a service (04, Q3 table).
WEIGHTS = {
    "authored": 3.0,   # wrote the PR themselves
    "deep": 2.0,       # review with secs >= 300 or comments >= 2
    "light": 0.5,      # a review that is neither deep nor a stamp (not in 04's table; our choice)
    "prompted": 1.5,   # prompted an agent PR AND left comments on it
    "stamp": 0.2,      # review with secs < 120 and 0 comments
    "incident": 4.0,   # resolved an incident rooted in the service
    "chat": 0.5,       # sent a message that mentions the service
}

# A person counts as an expert on a service at or above this score (Q10 $threshold).
THRESHOLD = 5.0


def recency(age_ms):
    """1.0 if under 90 days old, 0.5 if under a year, 0.15 if older."""
    if age_ms < 90 * DAY_MS:
        return 1.0
    if age_ms < 365 * DAY_MS:
        return 0.5
    return 0.15


def review_kind(secs, comments):
    """Same CASE as Q3b: deep, stamp, or light."""
    if secs >= 300 or comments >= 2:
        return "deep"
    if secs < 120 and comments == 0:
        return "stamp"
    return "light"


def score(evidence, now_ms):
    """Aggregate evidence rows into UNDERSTANDS properties.

    evidence: iterable of (who, svc, kind, at_ms). kind is one of WEIGHTS, or
    "prompted_silent" (prompted an agent PR without commenting; worth 0, but counted).
    Returns {(who, svc): {score, authored, deep_reviews, light_reviews, stamps, prompted, last_seen}}.
    """
    out = {}
    for who, svc, kind, at in evidence:
        u = out.setdefault((who, svc), {"score": 0.0, "authored": 0, "deep_reviews": 0, "light_reviews": 0,
                                        "stamps": 0, "prompted": 0, "last_seen": 0})
        u["score"] += WEIGHTS.get(kind, 0.0) * recency(now_ms - at)
        counter = {"authored": "authored", "deep": "deep_reviews", "light": "light_reviews", "stamp": "stamps",
                   "prompted": "prompted", "prompted_silent": "prompted"}.get(kind)
        if counter:
            u[counter] += 1
        u["last_seen"] = max(u["last_seen"], at)
    for u in out.values():
        u["score"] = round(u["score"], 3)
    return out


def zone(n_experts):
    """Q10 zone rule."""
    return "dark" if n_experts == 0 else "fragile" if n_experts == 1 else "healthy"


def shallow_touches(u):
    """How often someone touched a service without engaging: rubber stamps plus agent PRs they prompted.
    The knowledge-spreading suggestion picks the learner with the most shallow touches
    (they've seen the code but don't understand it yet)."""
    return u["stamps"] + u["prompted"]
