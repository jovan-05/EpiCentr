"""Generate the Northwind Labs dataset (Block B).

Reads hand-written YAML from data/northwind/seed/ and writes:
  data/northwind/nodes/<Label>.json   one list of property dicts per node label
  data/northwind/edges/<TYPE>.json    one list per relationship type:
                                      {src_label, src, dst_label, dst, ...props}
  data/northwind/live/ADR-031.json    Story 4 payload, ingested live on camera (not in the base graph)
  data/northwind/meta.json            as-of time, key property per label, counts

Everything is deterministic (random seed 42). All times are epoch milliseconds (UTC),
relative to AS_OF = 2026-10-17 09:00 UTC: "now" for the demo. PR-4471 merged 3 hours before it.

Story histories that depend on exact counts (payments-api: Stories 1 and 8; ledger: Story 6)
are scripted below. Everything else is weighted random noise.

Run:  .venv\\Scripts\\python.exe scripts\\generate_data.py
"""

import datetime as dt
import json
import random
import shutil
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SEED_DIR = ROOT / "data" / "northwind" / "seed"
OUT_DIR = ROOT / "data" / "northwind"

SEED = 42
AS_OF = dt.datetime(2026, 10, 17, 9, 0, tzinfo=dt.timezone.utc)
HOUR = 3_600_000
DAY = 24 * HOUR

# Stable key property per label (never store id(n); CLAUDE.md).
KEYS = {
    "Person": "handle", "CodingAgent": "name", "Team": "slug", "Service": "name", "Datastore": "name",
    "PR": "id", "Ticket": "key", "Doc": "id", "Decision": "id", "Option": "id",
    "Thread": "id", "Message": "id", "Incident": "id",
}

# Services whose PR history is scripted (no random PRs or chat mentions may touch them).
SCRIPTED = ("payments-api", "ledger")

# Story 5: these teams only collaborate with the rest of the company through marco
# (his payments-api work), so he is the top-betweenness bridge by construction, not by luck.
# Random reviews never leak into or out of them.
ISOLATED_TEAMS = ("checkout", "mobile")

# Random PRs per service (the rest of the ~450).
NOISE_PRS = {
    "payouts-worker": 18, "checkout-web": 24, "checkout-api": 32, "cart": 14,
    "fraud-svc": 24, "rules-engine": 14, "kyc-svc": 12,
    "api-gateway": 22, "auth-svc": 22, "config-svc": 14, "event-bus": 16,
    "warehouse-sync": 18, "reporting-api": 12, "ml-features": 24,
    "notifications": 16, "email-svc": 10, "referrals": 8,
    "mobile-bff": 18, "admin-console": 10, "support-api": 14,
}

NOUNS = {
    "payouts-worker": ["payout batching", "bank file export", "retry schedule", "payout status sync"],
    "checkout-web": ["payment form", "address step", "order summary", "SSR hydration", "error banner"],
    "checkout-api": ["order validation", "cart handoff", "tax lookup", "payment intent call", "session expiry", "promo codes"],
    "cart": ["cart merge", "item limits", "redis TTL", "price refresh"],
    "fraud-svc": ["score caching", "model rollout", "feature fetch", "decision logging", "velocity checks"],
    "rules-engine": ["rule parser", "rule versioning", "analyst UI hooks", "rule metrics"],
    "kyc-svc": ["document upload", "liveness check", "retention job", "vendor callbacks"],
    "api-gateway": ["rate limiter", "route table", "TLS config", "request logging", "header filtering"],
    "auth-svc": ["token refresh", "session revoke", "key rotation", "MFA flow", "login throttling"],
    "config-svc": ["flag cache", "flag audit log", "config schema", "SDK client"],
    "event-bus": ["consumer lag alerts", "topic config", "schema registry", "replay tooling"],
    "warehouse-sync": ["event mapping", "late event handling", "backfill job", "stream checkpoints"],
    "reporting-api": ["month-end report", "replica routing", "CSV export", "report cache"],
    "ml-features": ["feature snapshots", "point-in-time joins", "feature freshness", "training export", "velocity features"],
    "notifications": ["push templates", "quiet hours", "dedupe of pushes", "delivery receipts"],
    "email-svc": ["bounce handling", "template rendering", "unsubscribe links"],
    "referrals": ["reward cap", "referral codes", "abuse checks"],
    "mobile-bff": ["home screen payload", "checkout screen", "auth refresh", "image sizes"],
    "admin-console": ["refund screen", "customer search", "SSO login", "audit view"],
    "support-api": ["refund flow", "dispute lookup", "account notes", "ledger lookup"],
}
VERBS = ["Fix", "Add", "Improve", "Speed up", "Clean up", "Handle errors in", "Add metrics for", "Simplify", "Rework"]

# Chat noise templates. {svc} adds a MENTIONS edge to the thread's service.
MSG_WITH_SVC = [
    "Deploying {svc} to staging now.", "Anyone else seeing flaky tests in {svc}?", "{svc} p99 looks better after the last deploy.",
    "Can someone review my {svc} PR today?", "Heads up: {svc} config change going out at 3pm.",
    "Opened a ticket for the {svc} alert noise.", "Is {svc} still on the old base image?", "{svc} dashboards are updated.",
    "Rolled back {svc}, investigating.", "Who owns the {svc} runbook these days?",
]
MSG_PLAIN = [
    "Thanks!", "Looking now.", "LGTM.", "Can we pair on this tomorrow?", "Merged.", "Done, closing the ticket.",
    "I'll pick this up after standup.", "Good catch.", "Agreed.", "Let's discuss in planning.", "On it.",
    "Added a test for that case.", "Ship it.", "Updated the doc.", "Bumping this.",
]
TICKET_TEMPLATES = [
    "Alert noise on {svc}", "Upgrade dependencies in {svc}", "Add dashboards for {svc}", "Flaky integration test in {svc}",
    "Document {svc} runbook", "Reduce {svc} cold start time", "{svc}: handle partial failures", "Clean up feature flags in {svc}",
    "{svc} error budget review", "Load test {svc} before peak season",
]
TICKET_PREFIX = {"payments": "PAY", "checkout": "CHK", "risk": "RISK", "platform": "PLAT", "data": "DATA",
                 "growth": "GRW", "mobile": "MOB", "support-tools": "SUP"}


def ms(value):
    """ISO date / datetime string or YAML date -> epoch ms (UTC)."""
    if isinstance(value, dt.datetime):
        d = value
    elif isinstance(value, dt.date):
        d = dt.datetime(value.year, value.month, value.day)
    else:
        d = dt.datetime.fromisoformat(str(value))
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return int(d.timestamp() * 1000)


AS_OF_MS = ms(AS_OF)


def iso_week(t_ms):
    y, w, _ = dt.datetime.fromtimestamp(t_ms / 1000, dt.timezone.utc).isocalendar()
    return f"{y}-W{w:02d}"


class Graph:
    """Collects nodes and edges before they are written as JSON."""

    def __init__(self):
        self.nodes = {}   # label -> {key: props}
        self.edges = {}   # type -> [row]

    def node(self, label, **props):
        key = props[KEYS[label]]
        assert key not in self.nodes.setdefault(label, {}), f"duplicate {label} {key}"
        self.nodes[label][key] = {k: v for k, v in props.items() if v is not None}

    def edge(self, rtype, src_label, src, dst_label, dst, **props):
        assert src in self.nodes.get(src_label, {}), f"{rtype}: missing {src_label} {src}"
        assert dst in self.nodes.get(dst_label, {}), f"{rtype}: missing {dst_label} {dst}"
        row = {"src_label": src_label, "src": src, "dst_label": dst_label, "dst": dst}
        row.update({k: v for k, v in props.items() if v is not None})
        self.edges.setdefault(rtype, []).append(row)


def load_seed():
    return {name: yaml.safe_load((SEED_DIR / f"{name}.yaml").read_text(encoding="utf-8"))
            for name in ("org", "services", "decisions", "story")}


# ------------------------------------------------------------------ org

def build_org(g, org):
    people = {}
    for p in org["people"]:
        joined, left = ms(p["joined"]), (ms(p["left"]) if p.get("left") else None)
        people[p["handle"]] = {**p, "joined_ms": joined, "left_ms": left}
        g.node("Person", handle=p["handle"], name=p["name"], role=p["role"],
               status="departed" if left else "active", joined_at=joined, left_at=left)
    for t in org["teams"]:
        g.node("Team", slug=t["slug"], name=t["name"], group=t["group"])
    for a in org["agents"]:
        g.node("CodingAgent", name=a["name"], vendor=a["vendor"])

    for p in people.values():
        if p["team"]:
            g.edge("MEMBER_OF", "Person", p["handle"], "Team", p["team"], valid_from=p["joined_ms"], valid_to=p["left_ms"])

    # REPORTS_TO: members -> team lead, leads -> CTO; payments switches lead to dana when priya leaves.
    changes = {c["team"]: c for c in org.get("lead_changes", [])}
    for t in org["teams"]:
        members = [p for p in people.values() if p["team"] == t["slug"]]
        lead = next(p for p in members if p.get("lead"))
        change = changes.get(t["slug"])
        change_ms = ms(change["at"]) if change else None
        g.edge("REPORTS_TO", "Person", lead["handle"], "Person", "helen",
               valid_from=lead["joined_ms"], valid_to=lead["left_ms"])
        for m in members:
            if m is lead:
                continue
            frm = max(m["joined_ms"], lead["joined_ms"])
            to = min(x for x in (m["left_ms"], change_ms, lead["left_ms"]) if x) if (m["left_ms"] or change_ms or lead["left_ms"]) else None
            if to is None or to > frm:
                g.edge("REPORTS_TO", "Person", m["handle"], "Person", lead["handle"], valid_from=frm, valid_to=to)
            if change and m["handle"] != change["new_lead"] and (m["left_ms"] is None or m["left_ms"] > change_ms):
                g.edge("REPORTS_TO", "Person", m["handle"], "Person", change["new_lead"], valid_from=max(frm, change_ms),
                       valid_to=m["left_ms"])
        if change:
            g.edge("REPORTS_TO", "Person", change["new_lead"], "Person", "helen", valid_from=change_ms)
    return people


def build_services(g, svc_seed):
    for d in svc_seed["datastores"]:
        g.node("Datastore", name=d["name"], engine=d["engine"])
    services = {s["name"]: s for s in svc_seed["services"]}
    for s in services.values():
        g.node("Service", name=s["name"], tier=s["tier"], status="up", description=s["description"])
    for s in services.values():
        g.edge("OWNS", "Team", s["team"], "Service", s["name"], valid_from=ms("2020-01-01"),
               valid_to=ms(s["owner_until"]) if s.get("owner_until") else None)
        deps = list(s["deps"])
        if s["name"] != "config-svc":   # Story 5: everything reads flags from config-svc
            deps.append({"to": "config-svc", "mode": "sync"})
        for d in deps:
            g.edge("DEPENDS_ON", "Service", s["name"], "Service", d["to"], mode=d["mode"])
        for ds in s["uses"]:
            g.edge("USES", "Service", s["name"], "Datastore", ds)
    return services


# ------------------------------------------------------------------ PRs

class PRs:
    """Builds PR records; ids are assigned at the end in merge order so PR-4471 is the newest."""

    def __init__(self, rng):
        self.rng = rng
        self.items = []

    def review(self, who, kind, merged_at, fast=False):
        r = self.rng
        if kind == "deep":
            secs, comments = r.randint(300, 1500), r.randint(2, 6)
        elif kind == "light":
            secs, comments = r.randint(130, 290), 1
        else:  # stamp
            secs, comments = (r.randint(25, 85) if fast else r.randint(20, 110)), 0
        return {"who": who, "secs": secs, "comments": comments, "at": merged_at - r.randint(10, 600) * 60_000}

    def add(self, service, merged_at, title, lines, author=None, agent=None, prompted_by=None,
            prompted_comments=0, reviews=(), body=None, fixed_id=None):
        assert (author is None) != (agent is None)
        self.items.append({
            "service": service, "merged_at": merged_at, "title": title, "lines": lines,
            "author": author, "agent": agent, "prompted_by": prompted_by, "prompted_comments": prompted_comments,
            "authored_at": merged_at - self.rng.randint(1, 48) * HOUR, "reviews": list(reviews),
            "body": body, "fixed_id": fixed_id,
        })


def script_payments_api(prs, story):
    """Stories 1 and 8. payments-api history, hand-placed so that:
    - in the last 15 PRs, marco authored 4 and deep-reviewed 9 (SDK migration, Jul-Oct 2026);
    - sam has the MOST reviews, nearly all rubber stamps, and his own PRs are > 1 year old;
    - ivy light-reviewed 8 of marco's PRs (she is the natural learner if marco leaves);
    - priya (departed) wrote most of the older code;
    - PR-4471 (codegen-bot, prompted by ivy, stamped by sam in 38 s) is the newest PR in the company."""
    S, r = "payments-api", prs.rng
    rev = prs.review

    # sam's own PRs, all older than a year
    for i, day in enumerate(["2024-05-10", "2024-11-20", "2025-03-04"]):
        t = ms(day) + 15 * HOUR
        prs.add(S, t, ["Add capture retries", "Tidy refund path", "Log decline reasons"][i], r.randint(60, 220),
                author="sam", reviews=[rev("priya", "deep", t)])

    # priya's older work: 20 PRs from 2025-04-01 to 2026-07-15
    start, end = ms("2025-04-01T14:00"), ms("2026-07-15T14:00")
    titles = ["Capture flow cleanup", "Refund idempotency", "3DS challenge handling", "Partial captures",
              "Currency rounding", "Settlement report hook", "Card token refresh", "Payment state machine",
              "Decline code mapping", "Webhook signatures"]
    for i in range(20):
        t = start + (end - start) * i // 19
        reviews = []
        if i in (0, 1, 2, 3, 4, 5, 6, 7, 8, 17, 18, 19):
            reviews.append(rev("sam", "stamp", t))
        if i in (10, 13, 16):
            reviews.append(rev("dana", "deep", t))
        if i in (9, 12, 18):
            reviews.append(rev("kenji", "light", t))
        if i in (11, 15):
            reviews.append(rev("noor", "light", t))
        if i == 14:
            reviews.append(rev("sam", "light", t))
        if i in (2, 6):   # omar reviewed a little; sam must still have the most reviews
            reviews.append(rev("omar", "deep", t))
        prs.add(S, t, titles[i % len(titles)], r.randint(40, 380), author="priya", reviews=reviews)

    # the last 15 (excluding nothing): marco's SDK migration era
    sdk = ["Migrate auth calls to payments SDK v3", "Migrate capture to payments SDK v3",
           "Migrate refunds to payments SDK v3", "Remove legacy payments client"]
    for day, title in zip(["2026-08-12", "2026-09-02", "2026-09-23", "2026-10-07"], sdk):
        t = ms(day) + 16 * HOUR
        prs.add(S, t, title, r.randint(150, 420), author="marco", reviews=[rev("ivy", "light", t)])

    reviewed_by_marco = [
        ("2026-07-25", dict(author="priya"), "Prepare SDK v3 adapter", False),
        ("2026-08-05", dict(author="priya"), "Handover notes in code comments", True),
        ("2026-08-20", dict(author="priya"), "Move fraud call behind interface", True),
        ("2026-08-27", dict(agent="codegen-bot", prompted_by="ivy"), "Generate SDK v3 request models", False),
        ("2026-09-09", dict(author="noor"), "Fix currency for JPY captures", True),
        ("2026-09-16", dict(agent="codegen-bot", prompted_by="ivy"), "Generate SDK v3 response mappers", False),
        ("2026-09-29", dict(author="kenji"), "Add SDK v3 metrics", True),
        ("2026-10-01", dict(agent="codegen-bot", prompted_by="ivy"), "Port webhook handlers to SDK v3", False),
        ("2026-10-10", dict(author="tariq"), "Expose capture status for mobile", False),
    ]
    for day, who, title, ivy_reviews in reviewed_by_marco:
        t = ms(day) + 11 * HOUR
        reviews = [rev("marco", "deep", t)] + ([rev("ivy", "light", t)] if ivy_reviews else [])
        prs.add(S, t, title, r.randint(80, 400), reviews=reviews, **who)

    t = ms("2026-10-13T10:00")
    prs.add(S, t, "Bump payments SDK patch version", 12, agent="codegen-bot", prompted_by="noor",
            reviews=[rev("sam", "stamp", t)])

    # Story 8: PR-4471
    p = story["pr_4471"]
    t = AS_OF_MS - p["merged_hours_before_as_of"] * HOUR
    prs.add(p["service"], t, p["title"], p["lines"], agent=p["agent"], prompted_by=p["prompted_by"],
            reviews=[{"who": p["reviewer"], "secs": p["review_secs"], "comments": p["review_comments"], "at": t - 2 * 60_000}],
            body=p["body"], fixed_id=p["id"])
    prs.items[-1]["authored_at"] = t - 2 * HOUR


def script_ledger(prs):
    """Story 6. ledger history, hand-placed so that:
    - omar (left July 2026) wrote it; dana deep-reviewed his early PRs (> 1 year ago);
    - in the last 90 days ~70% of changed lines came from codegen-bot, prompted by ivy (joined June);
    - those agent PRs were rubber-stamped, mostly by sam in under 90 s with 0 comments;
    - no active human ends up at or above the expert threshold -> ledger is DARK."""
    S, r = "ledger", prs.rng
    rev = prs.review

    start, end = ms("2024-08-01T13:00"), ms("2026-07-05T13:00")
    titles = ["Double-entry posting", "Balance snapshot job", "Payout journal", "Reconciliation report",
              "Idempotency key table", "Account freeze flag", "FX journal entries", "Ledger audit log"]
    for i in range(25):
        t = start + (end - start) * i // 24
        reviewer = ("dana", "deep") if i in (1, 4, 7, 10, 12, 14) else ("priya", "deep")
        prs.add(S, t, titles[i % len(titles)], r.randint(80, 420), author="omar", reviews=[rev(*reviewer, t)])

    # agent PRs in the last 90 days
    start, end = ms("2026-07-22T10:00"), ms("2026-10-15T10:00")
    agent_lines = 0
    for i in range(16):
        t = start + (end - start) * i // 15
        lines = r.randint(140, 230)
        agent_lines += lines
        stamper = "noor" if i in (3, 8, 12) else "sam"
        prs.add(S, t, f"Refactor reconciliation step {i + 1}", lines, agent="codegen-bot", prompted_by="ivy",
                prompted_comments=2 if i == 5 else 0, reviews=[rev(stamper, "stamp", t, fast=True)])

    # human PRs in the last 90 days: ~30% of lines, each by a different person
    human_total = round(agent_lines * 0.3 / 0.7)
    authors = [("kenji", "2026-08-03", "Fix rounding in payout journal"), ("noor", "2026-08-25", "Index on journal date"),
               ("fatima", "2026-09-12", "Ledger config flags cleanup"), ("hugo", "2026-09-30", "Expose balance for risk checks")]
    for k, (who, day, title) in enumerate(authors):
        t = ms(day) + 15 * HOUR
        lines = human_total // 4 + (human_total % 4 if k == 0 else 0)
        prs.add(S, t, title, lines, author=who, reviews=[rev("ivy", "light", t)])


def noise_prs(prs, people, services, org):
    """Random PRs for every other service: team members author and review, ~40% are agent-written
    (~35% overall once the scripted human-heavy histories are included),
    10% of reviews leak in from another team (never into or out of ISOLATED_TEAMS, so marco stays the bridge).
    The shadow team (Story 5) co-reviews ml-features and warehouse-sync."""
    r = prs.rng
    shadow = org["shadow_team"]
    agents = ["codegen-bot", "refactor-bot", "test-bot"]

    def employed(h, t):
        p = people[h]
        return p["joined_ms"] <= t and (p["left_ms"] is None or t < p["left_ms"])

    by_team = {}
    for h in sorted(people):
        if people[h]["team"]:
            by_team.setdefault(people[h]["team"], []).append(h)
    leak_pool = [h for h in sorted(people) if people[h]["team"] not in (None, "sre") + ISOLATED_TEAMS]

    for svc in sorted(NOISE_PRS):
        team = services[svc]["team"]
        for _ in range(NOISE_PRS[svc]):
            t = AS_OF_MS - 4 * HOUR - int(730 * DAY * (r.random() ** 1.6))
            team_now = [h for h in by_team[team] if employed(h, t)]
            if svc in ("ml-features", "warehouse-sync"):
                authors_pool = [h for h in team_now + [s for s in shadow if s not in team_now] if employed(h, t)]
            else:
                authors_pool = team_now
            noun = r.choice(NOUNS[svc])
            if r.random() < 0.40:
                agent = r.choice(agents)
                human = r.choice(authors_pool)
                title = {"test-bot": f"Add tests for {noun}", "refactor-bot": f"Refactor {noun}"}.get(agent, f"{r.choice(VERBS)} {noun}")
                kw = dict(agent=agent, prompted_by=human, prompted_comments=0 if r.random() < 0.6 else r.randint(1, 3))
                lines = r.randint(40, 600)
                kinds = (["deep"] * 30 + ["light"] * 25 + ["stamp"] * 45)
            else:
                human = r.choice(authors_pool)
                title = f"{r.choice(VERBS)} {noun}"
                kw = dict(author=human)
                lines = r.randint(15, 450)
                kinds = (["deep"] * 40 + ["light"] * 35 + ["stamp"] * 25)

            reviewers = []
            if svc in ("ml-features", "warehouse-sync") and r.random() < 0.8:
                pool = [h for h in shadow if h != human and employed(h, t)]
                reviewers = r.sample(pool, k=min(3, len(pool)))
            else:
                for _ in range(1 if r.random() < 0.7 else 2):
                    leak = team not in ISOLATED_TEAMS and r.random() < 0.10
                    if leak:
                        pool = [h for h in leak_pool if h != human and h not in reviewers and employed(h, t)]
                    else:
                        pool = [h for h in team_now if h != human and h not in reviewers]
                    if pool:
                        reviewers.append(r.choice(pool))
            reviews = [prs.review(h, r.choice(kinds), t) for h in reviewers]
            prs.add(svc, t, title, lines, reviews=reviews, **kw)


def emit_prs(g, prs):
    items = sorted(prs.items, key=lambda x: x["merged_at"])
    assert items[-1]["fixed_id"] == "PR-4471", "PR-4471 must be the newest PR"
    for i, p in enumerate(items):
        p["id"] = p["fixed_id"] or f"PR-{4471 - (len(items) - 1 - i)}"
        g.node("PR", id=p["id"], title=p["title"], merged_at=p["merged_at"], lines=p["lines"],
               ai_generated=p["agent"] is not None, body=p["body"])
        g.edge("TOUCHES", "PR", p["id"], "Service", p["service"])
        if p["agent"]:
            g.edge("AUTHORED", "CodingAgent", p["agent"], "PR", p["id"], at=p["authored_at"])
            g.edge("PROMPTED", "Person", p["prompted_by"], "PR", p["id"], at=p["authored_at"], comments=p["prompted_comments"])
        else:
            g.edge("AUTHORED", "Person", p["author"], "PR", p["id"], at=p["authored_at"])
        for rv in p["reviews"]:
            g.edge("REVIEWED", "Person", rv["who"], "PR", p["id"], at=rv["at"], secs=rv["secs"], comments=rv["comments"])


# ------------------------------------------------------------------ decisions, docs, incidents, chat, tickets

def add_decision(g, d, decided_at):
    g.node("Decision", id=d["id"], title=d["title"], summary=d["summary"], status=d["status"], decided_at=decided_at,
           origin="seed")
    g.node("Doc", id=f"DOC-{d['id']}", title=d["title"], kind="ADR", updated_at=decided_at, body=d["doc_body"].strip())
    g.edge("DOCUMENTED_IN", "Decision", d["id"], "Doc", f"DOC-{d['id']}")
    g.node("Option", id=f"{d['id']}-chosen", text=d["chosen"]["text"], keywords=d["chosen"]["keywords"])
    g.edge("CHOSE", "Decision", d["id"], "Option", f"{d['id']}-chosen")
    for i, o in enumerate(d["rejected"], 1):
        oid = f"{d['id']}-rejected-{i}"
        g.node("Option", id=oid, text=o["text"], keywords=o["keywords"])
        g.edge("REJECTED", "Decision", d["id"], "Option", oid, reason=o["reason"])
    for s in d["affects"]:
        g.edge("AFFECTS", "Decision", d["id"], "Service", s)
    for h in d["deciders"]:
        g.edge("DECIDED", "Person", h, "Decision", d["id"])


def build_decisions(g, dec):
    for d in dec["decisions"]:
        add_decision(g, d, ms(d["decided_at"]) + 14 * HOUR)
    for d in dec["decisions"]:   # second pass: links to other decisions, incidents, messages
        if d.get("supersedes"):
            g.edge("SUPERSEDES", "Decision", d["id"], "Decision", d["supersedes"], at=ms(d["decided_at"]) + 14 * HOUR)
        if d.get("caused_by"):
            g.edge("CAUSED_BY", "Decision", d["id"], "Incident", d["caused_by"])
        if d.get("proposed_in"):
            g.edge("PROPOSED_IN", "Decision", d["id"], "Message", d["proposed_in"])
    for doc in dec["docs"]:
        g.node("Doc", id=doc["id"], title=doc["title"], kind=doc["kind"], updated_at=ms(doc["updated_at"]), body=doc["body"].strip())


def build_incidents(g, story):
    for i in story["incidents"]:
        t = ms(i["at"])
        g.node("Incident", id=i["id"], title=i["title"], severity=i["severity"], opened_at=t, resolved_at=t + 3 * HOUR,
               status="resolved", brief=i["brief"])
        g.edge("IMPACTS", "Incident", i["id"], "Service", i["service"], role="root")
        for d in i.get("downstream", []):
            g.edge("IMPACTS", "Incident", i["id"], "Service", d, role="downstream")
        g.edge("ASSIGNED_TO", "Incident", i["id"], "Person", i["resolver"], reason="on-call")
        g.edge("RESOLVED_BY", "Incident", i["id"], "Person", i["resolver"])


def build_story_threads(g, story):
    for th in story["threads"]:
        g.node("Thread", id=th["id"], channel=th["channel"], topic=th["topic"])
        for m in th["messages"]:
            g.node("Message", id=m["id"], text=m["text"], sent_at=ms(m["at"]))
            g.edge("SENT", "Person", m["by"], "Message", m["id"])
            g.edge("IN_THREAD", "Message", m["id"], "Thread", th["id"])
            for s in m["mentions"]:
                g.edge("MENTIONS", "Message", m["id"], "Service", s, confidence=1.0)


def noise_threads(g, rng, people, services, n_threads=36, per_thread=16):
    """Chat noise in team channels. Never mentions the scripted services."""
    def employed(h, t):
        p = people[h]
        return p["joined_ms"] <= t and (p["left_ms"] is None or t < p["left_ms"])

    svc_names = [s for s in sorted(services) if s not in SCRIPTED]
    teams = sorted({services[s]["team"] for s in svc_names}) + ["sre"]
    n = 0
    for k in range(n_threads):
        team = teams[k % len(teams)]
        svc = rng.choice([s for s in svc_names if services[s]["team"] == team] if team != "sre" else svc_names)
        tid = f"TH-{k + 1:03d}"
        t = AS_OF_MS - int(540 * DAY * rng.random()) - DAY
        g.node("Thread", id=tid, channel=f"#{team}", topic=f"{svc} {rng.choice(['deploys', 'alerts', 'reviews', 'questions', 'planning'])}")
        members = [h for h in sorted(people) if people[h]["team"] == team]
        for _ in range(per_thread):
            t += rng.randint(2, 180) * 60_000
            senders = [h for h in members if employed(h, t)]
            if not senders:
                continue
            n += 1
            mid = f"MSG-{n:05d}"
            with_svc = rng.random() < 0.45
            text = (rng.choice(MSG_WITH_SVC).format(svc=svc) if with_svc else rng.choice(MSG_PLAIN))
            g.node("Message", id=mid, text=text, sent_at=t)
            g.edge("SENT", "Person", rng.choice(senders), "Message", mid)
            g.edge("IN_THREAD", "Message", mid, "Thread", tid)
            if with_svc:
                g.edge("MENTIONS", "Message", mid, "Service", svc, confidence=1.0)


def build_tickets(g, rng, people, services, story, n_noise=149):
    for tk in story["tickets"]:
        g.node("Ticket", key=tk["key"], title=tk["title"], status=tk["status"], priority=tk["priority"], created_at=ms(tk["created_at"]))
        for s in tk["about"]:
            g.edge("ABOUT", "Ticket", tk["key"], "Service", s)
        g.edge("ASSIGNED", "Person", tk["assignee"], "Ticket", tk["key"])
    taken = {tk["key"] for tk in story["tickets"]}
    counters = {p: 20 for p in TICKET_PREFIX.values()}
    svc_names = sorted(services)
    for _ in range(n_noise):
        svc = rng.choice(svc_names)
        team = services[svc]["team"]
        prefix = TICKET_PREFIX[team]
        key = f"{prefix}-{counters[prefix]}"
        while key in taken:
            counters[prefix] += 1
            key = f"{prefix}-{counters[prefix]}"
        counters[prefix] += 1
        taken.add(key)
        t = AS_OF_MS - int(540 * DAY * rng.random())
        status = rng.choices(["done", "in_progress", "open"], [55, 15, 30])[0]
        g.node("Ticket", key=key, title=rng.choice(TICKET_TEMPLATES).format(svc=svc), status=status,
               priority=rng.choice(["low", "medium", "medium", "high"]), created_at=t)
        g.edge("ABOUT", "Ticket", key, "Service", svc)
        staff = [h for h in sorted(people) if people[h]["team"] == team
                 and people[h]["joined_ms"] <= t and (people[h]["left_ms"] is None or people[h]["left_ms"] > t)]
        if staff:
            g.edge("ASSIGNED", "Person", rng.choice(staff), "Ticket", key)


def build_on_call(g, people, services):
    """Weekly rotation per team, 6 weeks back to 2 weeks ahead. Story 1: the payments sheet was
    never updated after priya left, and she is on call for the AS_OF week."""
    as_of_week = dt.datetime.fromtimestamp(AS_OF_MS / 1000, dt.timezone.utc).isocalendar()[1]
    for team in sorted({s["team"] for s in services.values()}):
        if team == "payments":
            rota = ["dana", "priya", "sam", "noor", "kenji"]          # stale sheet
            offset = rota.index("priya") - as_of_week                  # priya lands on the AS_OF week
        else:
            rota = [h for h in sorted(people) if people[h]["team"] == team and people[h]["left_ms"] is None]
            offset = 0
        for w in range(as_of_week - 6, as_of_week + 3):
            who = rota[(w + offset) % len(rota)]
            for s in sorted(n for n, x in services.items() if x["team"] == team):
                g.edge("ON_CALL_FOR", "Person", who, "Service", s, week=f"2026-W{w:02d}")


def write_live(dec):
    out = OUT_DIR / "live"
    out.mkdir(parents=True, exist_ok=True)
    for d in dec["live"]:
        payload = {
            "decision": {"id": d["id"], "title": d["title"], "summary": d["summary"], "status": d["status"]},
            "doc": {"id": f"DOC-{d['id']}", "title": d["title"], "kind": "ADR", "body": d["doc_body"].strip()},
            "options": {"chosen": d["chosen"], "rejected": d["rejected"]},
            "affects": d["affects"], "deciders": d["deciders"], "supersedes": d.get("supersedes"),
            "note": "Ingested live on camera (Story 4) with the A3 statements; decided_at = time of ingest.",
        }
        (out / f"{d['id']}.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")


def write(g):
    for sub in ("nodes", "edges"):
        shutil.rmtree(OUT_DIR / sub, ignore_errors=True)
        (OUT_DIR / sub).mkdir(parents=True)
    for label, rows in sorted(g.nodes.items()):
        data = [rows[k] for k in sorted(rows)]
        (OUT_DIR / "nodes" / f"{label}.json").write_text(json.dumps(data, indent=1), encoding="utf-8")
    for rtype, rows in sorted(g.edges.items()):
        (OUT_DIR / "edges" / f"{rtype}.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
    meta = {
        "company": "Northwind Labs (synthetic)", "seed": SEED,
        "as_of_ms": AS_OF_MS, "as_of_iso": AS_OF.isoformat(), "as_of_week": iso_week(AS_OF_MS),
        "keys": KEYS,
        "counts": {"nodes": {k: len(v) for k, v in sorted(g.nodes.items())},
                   "edges": {k: len(v) for k, v in sorted(g.edges.items())}},
    }
    (OUT_DIR / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return meta


def main():
    rng = random.Random(SEED)
    seed = load_seed()
    g = Graph()
    people = build_org(g, seed["org"])
    services = build_services(g, seed["services"])
    build_incidents(g, seed["story"])
    build_story_threads(g, seed["story"])
    build_decisions(g, seed["decisions"])

    prs = PRs(rng)
    script_payments_api(prs, seed["story"])
    script_ledger(prs)
    noise_prs(prs, people, services, seed["org"])
    emit_prs(g, prs)

    noise_threads(g, rng, people, services)
    build_tickets(g, rng, people, services, seed["story"])
    build_on_call(g, people, services)
    write_live(seed["decisions"])
    meta = write(g)

    print(f"Wrote data/northwind (as of {meta['as_of_iso']}, week {meta['as_of_week']})")
    for kind in ("nodes", "edges"):
        print(f"  {kind}: " + ", ".join(f"{k}={v}" for k, v in meta["counts"][kind].items()))


if __name__ == "__main__":
    main()
