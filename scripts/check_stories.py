"""Assert that every planted story (06, Stories 1-8) is present in data/northwind.

Works on the generated JSON only (no database needed), so it runs right after
generate_data.py. It recomputes the comprehension score with api/scoring.py,
PageRank over DEPENDS_ON and betweenness over COLLABORATES_WITH in plain Python,
so we know the stories will come out of the graph queries in Block C.

Run:  .venv\\Scripts\\python.exe scripts\\check_stories.py
"""

import datetime as dt
import json
import sys
from collections import defaultdict, deque
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from api import scoring  # noqa: E402

DATA = Path(__file__).resolve().parent.parent / "data" / "northwind"
HOUR = 3_600_000


def ms(day):
    """'2026-08-29' -> epoch ms at 00:00 UTC."""
    return int(dt.datetime.fromisoformat(day).replace(tzinfo=dt.timezone.utc).timestamp() * 1000)


def on_day(t, day):
    return ms(day) <= t < ms(day) + 24 * HOUR


def in_month(t, month):
    """in_month(t, '2026-02')"""
    y, m = map(int, month.split("-"))
    nxt = f"{y + (m == 12)}-{m % 12 + 1:02d}-01"
    return ms(f"{month}-01") <= t < ms(nxt)


def load():
    meta = json.loads((DATA / "meta.json").read_text(encoding="utf-8"))
    nodes = {p.stem: {r[meta["keys"][p.stem]]: r for r in json.loads(p.read_text(encoding="utf-8"))}
             for p in (DATA / "nodes").glob("*.json")}
    edges = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in (DATA / "edges").glob("*.json")}
    return meta, nodes, edges


class World:
    """Indexes over the JSON, plus the derived things Block C will compute in the graph."""

    def __init__(self, meta, nodes, edges):
        self.meta, self.n, self.e = meta, nodes, edges
        self.now = meta["as_of_ms"]
        self.touches = {r["src"]: r["dst"] for r in edges["TOUCHES"]}
        self.prs_of = defaultdict(list)
        for pr, svc in self.touches.items():
            self.prs_of[svc].append(pr)
        self.author = {r["dst"]: (r["src_label"], r["src"], r["at"]) for r in edges["AUTHORED"]}
        self.prompted = {r["dst"]: r for r in edges["PROMPTED"]}
        self.reviews = defaultdict(list)
        for r in edges["REVIEWED"]:
            self.reviews[r["dst"]].append(r)
        self.understands = scoring.score(self.evidence(), self.now)
        self.collab = self.collaboration()

    def active(self, h):
        return self.n["Person"][h]["status"] == "active"

    def evidence(self):
        """The same evidence rows Q3a/Q3b/Q3c (+ prompted, chat) will return from the graph."""
        ev = []
        for pr, (lbl, who, at) in self.author.items():
            if lbl == "Person":
                ev.append((who, self.touches[pr], "authored", at))
        for pr, revs in self.reviews.items():
            for r in revs:
                ev.append((r["src"], self.touches[pr], scoring.review_kind(r["secs"], r["comments"]), r["at"]))
        for pr, p in self.prompted.items():
            ev.append((p["src"], self.touches[pr], "prompted" if p["comments"] > 0 else "prompted_silent", p["at"]))
        resolved = {i for i, x in self.n["Incident"].items() if x["status"] == "resolved"}
        roots = {r["src"]: r["dst"] for r in self.e["IMPACTS"] if r["role"] == "root"}
        for r in self.e["ASSIGNED_TO"]:
            if r["src"] in resolved:
                ev.append((r["dst"], roots[r["src"]], "incident", self.n["Incident"][r["src"]]["opened_at"]))
        sender = {r["dst"]: r["src"] for r in self.e["SENT"]}
        for r in self.e["MENTIONS"]:
            ev.append((sender[r["src"]], r["dst"], "chat", self.n["Message"][r["src"]]["sent_at"]))
        return ev

    def collaboration(self):
        """COLLABORATES_WITH derive: people who authored/reviewed the same PR."""
        people_on = defaultdict(set)
        for pr, (lbl, who, _) in self.author.items():
            if lbl == "Person":
                people_on[pr].add(who)
        for pr, revs in self.reviews.items():
            for r in revs:
                people_on[pr].add(r["src"])
        shared = defaultdict(int)
        for ps in people_on.values():
            ps = sorted(ps)
            for i, a in enumerate(ps):
                for b in ps[i + 1:]:
                    shared[(a, b)] += 1
                    shared[(b, a)] += 1
        return shared

    def scores(self, svc, active_only=True):
        rows = [(who, u) for (who, s), u in self.understands.items()
                if s == svc and (self.active(who) or not active_only)]
        return sorted(rows, key=lambda x: -x[1]["score"])

    def experts(self, svc, gone=()):
        return [w for w, u in self.scores(svc) if u["score"] >= scoring.THRESHOLD and w not in gone]

    def zones(self):
        return {s: scoring.zone(len(self.experts(s))) for s in self.n["Service"]}

    def pagerank(self, d=0.85, iters=100):
        nodes = sorted(self.n["Service"])
        out = defaultdict(list)
        for r in self.e["DEPENDS_ON"]:
            out[r["src"]].append(r["dst"])
        pr = {v: 1 / len(nodes) for v in nodes}
        for _ in range(iters):
            dangling = sum(pr[v] for v in nodes if not out[v])
            nxt = {v: (1 - d) / len(nodes) + d * dangling / len(nodes) for v in nodes}
            for v in nodes:
                for w in out[v]:
                    nxt[w] += d * pr[v] / len(out[v])
            pr = nxt
        return sorted(pr.items(), key=lambda x: -x[1])

    def betweenness(self):
        """Brandes, unweighted, undirected, over Person nodes and COLLABORATES_WITH."""
        nodes = sorted(self.n["Person"])
        adj = defaultdict(set)
        for (a, b) in self.collab:
            adj[a].add(b)
        bc = dict.fromkeys(nodes, 0.0)
        for s in nodes:
            stack, pred = [], defaultdict(list)
            sigma, dist = defaultdict(int), {s: 0}
            sigma[s] = 1
            q = deque([s])
            while q:
                v = q.popleft()
                stack.append(v)
                for w in sorted(adj[v]):
                    if w not in dist:
                        dist[w] = dist[v] + 1
                        q.append(w)
                    if dist[w] == dist[v] + 1:
                        sigma[w] += sigma[v]
                        pred[w].append(v)
            delta = defaultdict(float)
            while stack:
                w = stack.pop()
                for v in pred[w]:
                    delta[v] += sigma[v] / sigma[w] * (1 + delta[w])
                if w != s:
                    bc[w] += delta[w]
        return sorted(((k, v / 2) for k, v in bc.items()), key=lambda x: -x[1])


# ------------------------------------------------------------------ checks

def edges_to(w, rtype, dst):
    return [r for r in w.e[rtype] if r["dst"] == dst]


def edges_from(w, rtype, src):
    return [r for r in w.e[rtype] if r["src"] == src]


def story1(w):
    p = w.n["Person"]["priya"]
    yield "priya departed on 2026-08-29", p["status"] == "departed" and on_day(p["left_at"], "2026-08-29"), p.get("left_at")
    week = w.meta["as_of_week"]
    oc = [r["src"] for r in w.e["ON_CALL_FOR"] if r["dst"] == "payments-api" and r["week"] == week]
    yield f"priya is on call for payments-api in {week} (sheet not updated)", oc == ["priya"], oc
    teams = [r["dst"] for r in edges_from(w, "MEMBER_OF", "marco")]
    yield "marco is on checkout, not payments", teams == ["checkout"], teams

    last = sorted(w.prs_of["payments-api"], key=lambda pr: -w.n["PR"][pr]["merged_at"])[:15]
    authored = sum(1 for pr in last if w.author[pr][1] == "marco")
    reviewed = sum(1 for pr in last if any(r["src"] == "marco" for r in w.reviews[pr]))
    yield "marco authored 4 and reviewed 9 of the last 15 payments-api PRs", (authored, reviewed) == (4, 9), (authored, reviewed)

    ranking = [(h, u["score"]) for h, u in w.scores("payments-api")]
    yield "who_knows('payments-api')[0] == marco", ranking[0][0] == "marco", ranking[:4]

    counts = defaultdict(int)
    for pr in w.prs_of["payments-api"]:
        for r in w.reviews[pr]:
            counts[r["src"]] += 1
    top_reviewer = max(counts, key=counts.get)
    sam = w.understands[("sam", "payments-api")]
    yield "sam has the MOST reviews on payments-api", top_reviewer == "sam", dict(sorted(counts.items(), key=lambda x: -x[1])[:4])
    yield "sam's reviews are nearly all rubber stamps", sam["stamps"] >= 0.8 * counts["sam"], f"{sam['stamps']}/{counts['sam']} stamps"
    sam_prs = [w.n["PR"][pr]["merged_at"] for pr in w.prs_of["payments-api"] if w.author[pr][1] == "sam"]
    yield "sam's own payments-api commits are > 1 year old", sam_prs and max(sam_prs) < w.now - 365 * 24 * HOUR, len(sam_prs)
    rank = [h for h, _ in ranking]
    yield "sam ranks below marco", rank.index("sam") > rank.index("marco"), f"marco {ranking[0][1]}, sam {sam['score']}"


def story2(w):
    deps = {(r["src"], r["dst"]): r["mode"] for r in w.e["DEPENDS_ON"]}
    yield "checkout-api -[sync]-> payments-api -[sync]-> fraud-svc", \
        deps.get(("checkout-api", "payments-api")) == "sync" and deps.get(("payments-api", "fraud-svc")) == "sync", \
        (deps.get(("checkout-api", "payments-api")), deps.get(("payments-api", "fraud-svc")))
    d = w.n["Decision"]["ADR-017"]
    deciders = sorted(r["src"] for r in edges_to(w, "DECIDED", "ADR-017"))
    yield "ADR-017 decided Feb 2026 by priya and dana", in_month(d["decided_at"], "2026-02") and deciders == ["dana", "priya"], deciders
    yield "ADR-017 AFFECTS payments-api", any(r["dst"] == "payments-api" for r in edges_from(w, "AFFECTS", "ADR-017")), None
    prop = edges_from(w, "PROPOSED_IN", "ADR-017")
    msg = prop[0]["dst"] if prop else None
    sender = next((r["src"] for r in w.e["SENT"] if r["dst"] == msg), None)
    thread = next((r["dst"] for r in w.e["IN_THREAD"] if r["src"] == msg), None)
    m = w.n["Message"].get(msg, {})
    yield "proposed by priya in #payments-arch on 2026-01-22", sender == "priya" and w.n["Thread"][thread]["channel"] == "#payments-arch" \
        and on_day(m.get("sent_at", 0), "2026-01-22"), (msg, sender, thread)


def story3(w):
    lena = [w.n["Message"][r["dst"]] for r in w.e["SENT"] if r["src"] == "lena"]
    warn = next((m for m in lena if m["text"].startswith("If fraud-svc slows down")), None)
    yield "lena's warning exists, 2026-03-11", warn is not None and on_day(warn["sent_at"], "2026-03-11"), warn and warn["text"]
    thread = next(r["dst"] for r in w.e["IN_THREAD"] if r["src"] == warn["id"])
    mentions = sorted(r["dst"] for r in w.e["MENTIONS"] if r["src"] == warn["id"])
    yield "in #risk-eng, mentions payments-api", w.n["Thread"][thread]["channel"] == "#risk-eng" and "payments-api" in mentions, mentions
    yield "full-text terms 'timeout|circuit*' hit it", "timeout" in warn["text"] and "circuit" in warn["text"], None
    t = w.n["Ticket"]["RISK-88"]
    about = sorted(r["dst"] for r in w.e["ABOUT"] if r["src"] == "RISK-88")
    yield "RISK-88 is still open and ABOUT payments-api", t["status"] == "open" and "payments-api" in about, (t["status"], about)


def story4(w):
    live = json.loads((DATA / "live" / "ADR-031.json").read_text(encoding="utf-8"))
    yield "ADR-031 is NOT in the base graph (ingested live)", "ADR-031" not in w.n["Decision"], None
    yield "live ADR-031 supersedes ADR-017 and affects payments-api", live["supersedes"] == "ADR-017" and "payments-api" in live["affects"], live["decision"]["summary"]
    yield "ADR-017 is active before the live ingest", w.n["Decision"]["ADR-017"]["status"] == "active", None


def story5(w):
    pr = w.pagerank()
    top = pr[0][0]
    yield "config-svc has the top PageRank and is tier 3", top == "config-svc" and w.n["Service"]["config-svc"]["tier"] == 3, \
        [(s, round(v, 3)) for s, v in pr[:3]]
    bc = w.betweenness()
    yield "marco has the highest betweenness", bc[0][0] == "marco", [(h, round(v, 1)) for h, v in bc[:4]]

    shadow = yaml.safe_load((DATA / "seed" / "org.yaml").read_text(encoding="utf-8"))["shadow_team"]
    teams = {h: next(r["dst"] for r in w.e["MEMBER_OF"] if r["src"] == h) for h in shadow}
    pairs = [(a, b) for i, a in enumerate(shadow) for b in shadow[i + 1:]]
    weak = [(a, b, w.collab[(a, b)]) for a, b in pairs if w.collab[(a, b)] < 3]
    yield "shadow team: 5 people from data, risk, payments co-review constantly", \
        sorted(set(teams.values())) == ["data", "payments", "risk"] and not weak, \
        f"min shared={min(w.collab[p] for p in pairs)}, weak pairs={weak}"

    owns = [r for r in w.e["OWNS"] if r["dst"] == "referrals"]
    current = [r for r in owns if r.get("valid_to") is None]
    yield "referrals has no current owner (growth OWNS ended)", owns and not current, [(r["src"], r.get("valid_to")) for r in owns]


def story6(w):
    yield "three coding agents", sorted(w.n["CodingAgent"]) == ["codegen-bot", "refactor-bot", "test-bot"], None
    cutoff = w.now - 90 * 24 * HOUR
    recent = [pr for pr in w.prs_of["ledger"] if w.n["PR"][pr]["merged_at"] > cutoff]
    lines = sum(w.n["PR"][pr]["lines"] for pr in recent)
    bot = [pr for pr in recent if w.author[pr][1] == "codegen-bot"]
    share = sum(w.n["PR"][pr]["lines"] for pr in bot) / lines
    yield "ledger: ~70% of last-90-day lines by codegen-bot", 0.65 <= share <= 0.75, f"{share:.1%}"
    prompters = {w.prompted[pr]["src"] for pr in bot}
    ivy_joined = w.n["Person"]["ivy"]["joined_at"]
    yield "prompted by ivy, who joined in June 2026", prompters == {"ivy"} and in_month(ivy_joined, "2026-06"), prompters
    sam_fast = [pr for pr in bot if any(r["src"] == "sam" and r["secs"] < 90 and r["comments"] == 0 for r in w.reviews[pr])]
    yield "most approved by sam in < 90 s with 0 comments", len(sam_fast) > len(bot) / 2, f"{len(sam_fast)}/{len(bot)}"
    omar = w.n["Person"]["omar"]
    yield "omar (original author) left in July 2026", omar["status"] == "departed" and in_month(omar["left_at"], "2026-07"), None

    zones = w.zones()
    led = [(h, u["score"]) for h, u in w.scores("ledger")[:4]]
    yield "ledger is DARK (no active human >= threshold)", zones["ledger"] == "dark", led
    yield "payments-api is FRAGILE (only marco)", w.experts("payments-api") == ["marco"], w.experts("payments-api")
    yield "checkout-api is HEALTHY (>= 4 experts)", len(w.experts("checkout-api")) >= 4, w.experts("checkout-api")
    dark_t1 = sorted(s for s, z in zones.items() if z == "dark" and w.n["Service"][s]["tier"] == 1)
    yield "ledger is the only dark tier-1 service", dark_t1 == ["ledger"], dark_t1

    led_scores = w.scores("ledger")
    mentor = led_scores[0][0]
    learner = max((x for x in led_scores if x[0] != mentor), key=lambda x: scoring.shallow_touches(x[1]))[0]
    yield "knowledge-spreading suggestion pairs dana (mentor) with ivy (learner)", (mentor, learner) == ("dana", "ivy"), \
        [(h, u["score"], scoring.shallow_touches(u)) for h, u in led_scores[:4]]


def story7(w):
    d = w.n["Decision"]["ADR-009"]
    yield "ADR-009 decided Nov 2025, AFFECTS ledger", in_month(d["decided_at"], "2025-11") and \
        any(r["dst"] == "ledger" for r in edges_from(w, "AFFECTS", "ADR-009")), None
    chosen = [w.n["Option"][r["dst"]]["text"] for r in edges_from(w, "CHOSE", "ADR-009")]
    rejected = {w.n["Option"][r["dst"]]["text"]: r["reason"] for r in edges_from(w, "REJECTED", "ADR-009")}
    yield "chosen: idempotency key per payout request", chosen == ["Idempotency key per payout request"], chosen
    yield "rejected: remove dedupe check (INC-007) and client-only dedupe", \
        rejected.get("Remove dedupe check for speed", "").startswith("caused INC-007: 312 duplicate payouts") and \
        rejected.get("Dedupe in the client only") == "retries from mobile bypass it", rejected
    cb = [r["dst"] for r in edges_from(w, "CAUSED_BY", "ADR-009")]
    resolver = [r["dst"] for r in edges_from(w, "RESOLVED_BY", "INC-007")]
    yield "CAUSED_BY INC-007, resolved by dana (active)", cb == ["INC-007"] and resolver == ["dana"] and w.active("dana"), (cb, resolver)
    opt = next(w.n["Option"][r["dst"]] for r in edges_from(w, "REJECTED", "ADR-009"))
    text = (opt["text"] + " " + opt["keywords"]).lower()
    yield "guard terms 'idempoten*|dedupe*' match the rejected option", "dedupe" in text and "idempoten" in text, opt["keywords"]


def story8(w):
    p = w.n["PR"]["PR-4471"]
    yield "PR-4471 'simplify fraud check client' merged 3 h before AS_OF", p["title"] == "simplify fraud check client" and \
        w.now - p["merged_at"] == 3 * HOUR, p["merged_at"]
    yield "authored by codegen-bot, prompted by ivy", w.author["PR-4471"][:2] == ("CodingAgent", "codegen-bot") and \
        w.prompted["PR-4471"]["src"] == "ivy", None
    rv = w.reviews["PR-4471"]
    yield "approved by sam in 38 s with 0 comments", [(r["src"], r["secs"], r["comments"]) for r in rv] == [("sam", 38, 0)], rv
    yield "touches payments-api and removed the 300 ms timeout", w.touches["PR-4471"] == "payments-api" and "300 ms timeout" in p["body"], p["body"]
    newest = max(w.n["PR"].values(), key=lambda x: x["merged_at"])["id"]
    yield "PR-4471 is the newest PR (top of suspect_changes)", newest == "PR-4471", newest

    after = w.experts("payments-api", gone=["marco"])
    yield "what-if marco leaves: payments-api goes dark", after == [], after
    # Q14 learner: active, not leaving, has SOME evidence on the service; readiness = score + 0.5 * shared with marco
    cands = [(h, round(u["score"] + 0.5 * w.collab[(h, "marco")], 2)) for h, u in w.scores("payments-api") if h != "marco"]
    cands.sort(key=lambda x: -x[1])
    yield "what-if learner is ivy", cands[0][0] == "ivy", cands[:3]


def sizes(w):
    c = {k: len(v) for k, v in w.n.items()}
    agent = sum(1 for lbl, _, _ in w.author.values() if lbl == "CodingAgent") / c["PR"]
    yield "~60 people, 3 agents, 9 teams, ~22 services", (55 <= c["Person"] <= 65, c["CodingAgent"], c["Team"]) == (True, 3, 9), \
        (c["Person"], c["CodingAgent"], c["Team"], c["Service"])
    yield "~450 PRs, ~35% agent-authored", 400 <= c["PR"] <= 500 and 0.30 <= agent <= 0.40, f"{c['PR']} PRs, {agent:.0%} agent"
    yield "~150 tickets, ~600 messages in ~40 threads", 140 <= c["Ticket"] <= 160 and 550 <= c["Message"] <= 650 and 35 <= c["Thread"] <= 45, \
        (c["Ticket"], c["Message"], c["Thread"])
    yield "~30 docs, ~25 decisions, ~60 options", 25 <= c["Doc"] <= 35 and 20 <= c["Decision"] <= 30 and 45 <= c["Option"] <= 70, \
        (c["Doc"], c["Decision"], c["Option"])


STORIES = [("Story 1: the owner left", story1), ("Story 2: decision behind the blast radius", story2),
           ("Story 3: someone warned us", story3), ("Story 4: knowledge stays current", story4),
           ("Story 5: org health", story5), ("Story 6: the dark zone", story6),
           ("Story 7: the decision guard", story7), ("Story 8: the thread that connects everything", story8),
           ("Dataset size", sizes)]


def main():
    w = World(*load())
    failed = total = 0
    for title, fn in STORIES:
        print(f"\n{title}")
        try:
            for label, ok, detail in fn(w):
                total += 1
                failed += not ok
                print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  -> {detail}" if detail is not None else ""))
        except Exception as ex:
            total += 1
            failed += 1
            print(f"  FAIL  crashed: {ex.__class__.__name__}: {ex}")
    print(f"\n{total - failed}/{total} story facts hold (threshold {scoring.THRESHOLD}, as of {w.meta['as_of_iso']}).")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
