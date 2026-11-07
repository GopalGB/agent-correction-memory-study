#!/usr/bin/env python3
"""Aggregate analysis of correction memories vs. user prompts.

Usage:
  python3 analyze.py --db claude-mem.db --memdir MEMORY_DIR --out results/aggregates.json \
      --project "<project name>" [--user-aliases "Name1,Name2"] [--start-date YYYY-MM-DD] [--end-date YYYY-MM-DD] \
      [--private-dir DIR] [--labels labels.json] [--labels_lenient l.json] [--labels2 l2.json] [--catlabels c.json]

Label files are JSON lists of 0/1 (or {"fingerprint": "<sha>", "labels": [...]}). The fingerprint of each
hand-check sample is written to aggregates under "sample_fingerprints"; if a label file carries one it must match.
Without label files all other results are still produced.

Only aggregates are written to --out. Row-level intermediates (which contain
prompt-derived text) go to --private-dir and must never be published.
"""
import argparse, collections, datetime as dt, glob, hashlib, json, math, os, platform, random, re, sqlite3, sys
import numpy as np
from scipy import stats

P = argparse.ArgumentParser()
P.add_argument("--db", required=True)
P.add_argument("--memdir", required=True)
P.add_argument("--out", required=True)
P.add_argument("--private-dir", default=None)
P.add_argument("--labels", default=None, help="JSON list of 0/1 hand labels for the sample")
P.add_argument("--labels2", default=None, help="JSON list of 0/1 hand labels for the v2 sample")
P.add_argument("--labels_lenient", default=None, help="JSON list of 0/1 lenient hand labels for the v1 sample")
P.add_argument("--catlabels", default=None, help="JSON list of 0/1 hand labels (assigned category acceptable) for the taxonomy sample")
P.add_argument("--project", required=True, help="session project name whose sessions load the memory directory (the memory scope)")
P.add_argument("--user-aliases", default="", help="comma-separated names/aliases by which memory text refers to the human user; "
               "no default. Used only for a text-mention proxy (NOT verified authorship). Empty = skip the proxy.")
P.add_argument("--start-date", default=None, help="first study date (UTC, YYYY-MM-DD); default = earliest observation date")
P.add_argument("--end-date", default=None, help="last study date (UTC, YYYY-MM-DD); default = latest observation date")
A = P.parse_args()
SEED = 20261107
random.seed(SEED)
D = dt.date.fromisoformat
R = {}  # results

def sha_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for ch in iter(lambda: f.read(1 << 20), b""): h.update(ch)
    return h.hexdigest()
def lab_load(path, fp):
    v = json.load(open(path))
    if isinstance(v, dict):
        if v.get("fingerprint") not in (None, fp): raise SystemExit(f"label file {path}: fingerprint mismatch (sample changed)")
        v = v["labels"]
    return v
def utc_date(e): return dt.datetime.fromtimestamp(e / 1000 if e > 1e11 else e, dt.timezone.utc).date()

# ------------------------------------------------------------------ memory files
def fm(text):
    m = re.match(r"---\n(.*?)\n---\n?(.*)", text, re.S)
    head, body = (m.group(1), m.group(2)) if m else ("", text)
    g = lambda k: (re.search(rf"^{k}:\s*(.*)$", head, re.M) or [None, ""])[1].strip().strip("\"'")
    return g("name"), g("description"), body

index_line = {}
idx = os.path.join(A.memdir, "MEMORY.md")
idx_text = open(idx).read() if os.path.exists(idx) else ""
for l in idx_text.splitlines():
    m = re.match(r"- \[(.*?)\]\((?:.*/)?(.*?\.md)\)\s*-?\s*(.*)", l)
    if m: index_line[m.group(2)] = m.group(1) + " " + m.group(3)

files = sorted(glob.glob(os.path.join(A.memdir, "*.md")))
R["memory_files_total"] = len(files)
R["memory_files_by_prefix"] = dict(collections.Counter(
    (os.path.basename(f).split("_")[0] if "_" in os.path.basename(f) else "other") for f in files))
R["memory_index_chars"] = len(idx_text)
R["memory_index_lines"] = len(idx_text.splitlines())

ISO = re.compile(r"\d{4}-\d{2}-\d{2}")
MEM = []
for f in sorted(glob.glob(os.path.join(A.memdir, "feedback_*.md"))):
    b = os.path.basename(f); t = open(f, errors="ignore").read()
    name, desc, body = fm(t)
    src, d = None, None
    m = ISO.search(b)
    if m: d, src = m.group(0), "filename"
    if not d:
        m = ISO.search(body)
        if m: d, src = m.group(0), "body"
    if not d:
        m = re.search(r"^modified:\s*(\d{4}-\d{2}-\d{2})", t, re.M)
        if m: d, src = m.group(1), "frontmatter"
    if not d:
        d, src = dt.datetime.fromtimestamp(os.path.getmtime(f), dt.timezone.utc).date().isoformat(), "mtime"
    MEM.append(dict(file=b, name=name or b, desc=desc, body=body, date=d, date_src=src,
                    chars=len(t), idx=index_line.get(b, "")))
R["feedback_files"] = len(MEM)
R["feedback_date_source"] = dict(collections.Counter(m["date_src"] for m in MEM))
R["feedback_chars_total"] = sum(m["chars"] for m in MEM)
R["feedback_date_range"] = [min(m["date"] for m in MEM), max(m["date"] for m in MEM)]

def week_of(d):  # Monday of the week
    d = D(d) if isinstance(d, str) else d
    return d - dt.timedelta(days=d.weekday())

# ------------------------------------------------------------------ RQ1 growth + taxonomy
wk = collections.Counter(week_of(m["date"]).isoformat() for m in MEM)
R["rq1_weekly_new"] = dict(sorted(wk.items()))
monthly = collections.Counter(m["date"][:7] for m in MEM)
R["rq1_monthly_new"] = dict(sorted(monthly.items()))
first, last = week_of(min(m["date"] for m in MEM)), week_of(max(m["date"] for m in MEM))
nweeks = (last - first).days // 7 + 1
R["rq1_weeks_span"] = nweeks
R["rq1_weeks_with_new"] = len(wk)
R["rq1_mean_new_per_week"] = len(MEM) / nweeks
R["rq1_max_week"] = max(wk.items(), key=lambda x: x[1])

# Priority-ordered keyword classifier. Score = 3 * hits in name+description + 1 * hits in body.
# Ties break by list order. Zero score -> "other".
CATS = collections.OrderedDict([
    ("safety_outbound_gating", ["outbound", "email", "send", "sent ", "draft", "linkedin", "halt", "credential",
        ".env", "secret", "delete", "destructive", "approv", "permission", "bounce", "invite", "never reset",
        "leak", "dm ", "post "]),
    ("verification_honesty", ["verif", "evidence", "claim", "fake", "false", "hallucin", "fabricat", "proof",
        "prove", "theater", "theatre", "honest", "stale", "green", "measured", "probe", "unverified",
        "reproduc", "guess", "exoner", "silently", "not a ", "is not ", "does not ", "disprov"]),
    ("tool_environment_facts", ["cli", "flag", "endpoint", "plist", "launchd", "binary", "dom ", "selector",
        "playwright", "browser", "docker", "cookie", "hook", "timeout", "exit ", "cache", "parser", "stdin",
        "sqlite", "tcc", "alias", "grep", "regex", "pyc", "curl", "bash", "shell", "process", "runtime",
        "worktree", "python", "node", "json", "http", "captcha", "profile", "symlink", "mtime", "wrapper"]),
    ("resource_infra", ["disk", "swap", "memory", "gb", "ram ", "cpu", "oom", "parallel", "cap ", "reboot",
        "backup", "quota", "gpu", "rate limit", "credit", "cost", "billing", "usage", "free space", "load"]),
    ("workflow_delegation", ["delegat", "haiku", "opus", "sonnet", "executor", "worker", "dispatch", "specialist",
        "council", "fugu", "orchestra", "plan", "review", "session", "subagent", "agent", "route", "keeper",
        "follow-up", "question", "continue", "checkpoint", "finish", "ask", "fight", "default"]),
    ("user_preference_style", ["prefer", "style", "tone", "voice", "signature", "english", "address", "format",
        "em-dash", "wording", "language", "plain", "terse", "emoji", "years", "experience", "call g", "call sir",
        "name ", "never say", "always use", "sir ", "years", "monoculture"]),
    ("domain_task_knowledge", ["job", " cv", "resume", "apply", "application", "lead", "outreach", "partner", "agency", "visa", "reply",
        "cold", "gig", "interview", "career", "salary", "offer", "client", "prospect", "ats", "mentor",
        "course", "learning", "pdf", "price", "sample", "podcast", "notebook"]),
])
def classify(m):
    head = (m["name"] + " " + m["desc"]).lower().replace("-", " ").replace("_", " ")
    body = m["body"].lower()
    best, bs = "other", 0
    for c, kws in CATS.items():
        s = sum(3 for k in kws if k in head) + sum(1 for k in kws if k in body)
        if s > bs: best, bs = c, s
    return best
for m in MEM: m["cat"] = classify(m)
R["rq1_categories"] = dict(collections.Counter(m["cat"] for m in MEM).most_common())
R["rq1_categories_by_month"] = {c: dict(sorted(collections.Counter(m["date"][:7] for m in MEM if m["cat"] == c).items()))
                                for c in list(CATS) + ["other"]}

ALIASES = [x.strip() for x in A.user_aliases.split(",") if x.strip()]
UA = re.compile(r"\b(" + "|".join(re.escape(x) for x in ALIASES) + r")(?:'s)?\b") if ALIASES else None
for m in MEM: m["ua"] = bool(UA and UA.search(m["name"] + " " + m["desc"] + " " + m["body"][:300]))
R["rq1_user_mention_proxy"] = sum(m["ua"] for m in MEM) if UA else None   # text mention, NOT verified authorship
R["rq1_user_mention_proxy_by_category"] = ({c: dict(n=sum(1 for m in MEM if m["cat"] == c), ua=sum(1 for m in MEM if m["cat"] == c and m["ua"]))
                                          for c in list(CATS) + ["other"]} if UA else None)

# ------------------------------------------------------------------ prompts
db = sqlite3.connect(f"file:{A.db}?mode=ro", uri=True)
db.row_factory = sqlite3.Row
R["db_observations"] = db.execute("select count(*) from observations").fetchone()[0]
_lo, _hi = db.execute("select min(created_at_epoch), max(created_at_epoch) from observations").fetchone()
DBSTART = D(A.start_date) if A.start_date else utc_date(_lo)
END = D(A.end_date) if A.end_date else utc_date(_hi)
R["study_window_utc"] = [DBSTART.isoformat(), END.isoformat()]
R["db_obs_range"] = list(db.execute("select min(created_at),max(created_at) from observations").fetchone())
R["db_user_prompts"] = db.execute("select count(*) from user_prompts").fetchone()[0]
R["db_sessions"] = db.execute("select count(*) from sdk_sessions").fetchone()[0]
R["db_sessions_by_platform"] = dict(db.execute("select platform_source,count(*) from sdk_sessions group by 1").fetchall())
R["db_obs_by_type"] = dict(db.execute("select type,count(*) c from observations group by 1 order by c desc").fetchall())
obs_week = collections.Counter()
for (e,) in db.execute("select created_at_epoch from observations"):
    obs_week[week_of(utc_date(e)).isoformat()] += 1
_w0, _w1 = week_of(min(D(k) for k in obs_week)), week_of(max(D(k) for k in obs_week))
_cal = [(_w0 + dt.timedelta(days=7 * i)).isoformat() for i in range((_w1 - _w0).days // 7 + 1)]
R["obs_weekly"] = {k: obs_week.get(k, 0) for k in _cal}   # zero-filled calendar weeks
_ov = [R["obs_weekly"][k] for k in _cal][1:-1]
R["obs_weekly_zero_interior_weeks"] = sum(v == 0 for v in _ov)
R["obs_weekly_range_excl_partial_ends"] = [min(_ov), max(_ov)] if _ov else None

rows = db.execute("""select u.id,u.prompt_text t,u.created_at_epoch e,s.project,s.platform_source plat
                     from user_prompts u left join sdk_sessions s using(content_session_id)""").fetchall()
day = utc_date
pref = collections.Counter(r["t"][:40] for r in rows)
def is_human(t, thr=4):
    if len(t) > 2000: return False
    if t.lstrip().startswith(("<", "SYSTEM:", "[")): return False
    if len(t) > 40 and pref[t[:40]] >= thr: return False
    return True
def is_human_strict(t): return is_human(t, 2)
MARK = re.compile(r"\b(again|i told|told (?:you|u)|already|same (?:mistake|thing|problem|issue)|how many times|"
                  r"why (?:is|are|did|do|does|r|u|you|it)|still (?:not|doesn|dont|don't|broken|wrong)|wtf|"
                  r"keep (?:doing|asking|making)|stop (?:doing|asking)|repeat\w*|100 times|once more)\b", re.I)
PR = []
for r in rows:
    d = day(r["e"])
    PR.append(dict(id=r["id"], t=r["t"], d=d, proj=r["project"] or "", plat=r["plat"] or "",
                   h=is_human(r["t"]), h2=is_human_strict(r["t"]), c=bool(MARK.search(r["t"]))))
R["prompts_total"] = len(PR)
R["prompts_human"] = sum(p["h"] for p in PR)
R["prompts_human_strict"] = sum(p["h2"] for p in PR)
R["prompts_human_scope"] = sum(p["h"] and p["proj"] == A.project and p["plat"] == "claude" for p in PR)
R["prompts_human_correction_marked"] = sum(p["h"] and p["c"] for p in PR)
R["prompts_human_scope_correction_marked"] = sum(p["h"] and p["c"] and p["proj"] == A.project and p["plat"] == "claude" for p in PR)
R["prompts_median_len_human"] = float(np.median([len(p["t"]) for p in PR if p["h"]]))

# ------------------------------------------------------------------ RQ3 weekly correction rate
def rq3(sel, minn):
    W = collections.defaultdict(lambda: [0, 0]); LEN = collections.defaultdict(list)
    for p in PR:
        if sel(p):
            w = W[week_of(p["d"])]; w[0] += 1; w[1] += p["c"]; LEN[week_of(p["d"])].append(len(p["t"]))
    cum = np.array([sum(1 for m in MEM if D(m["date"]) < w) for w in sorted(W)])
    ws = sorted(W)
    keep = [i for i, w in enumerate(ws) if W[w][0] >= minn]
    x_week = np.array([(ws[i] - ws[0]).days / 7 for i in keep])
    x_mem = cum[keep].astype(float)
    y = np.array([W[ws[i]][1] / W[ws[i]][0] for i in keep])
    n = len(keep)
    out = dict(weeks_used=n, min_prompts_per_week=minn, mean_rate=float(y.mean()),
               prompts_in_used_weeks=int(sum(W[ws[i]][0] for i in keep)))
    if n >= 5:
        for nm, x in (("vs_week_index", x_week), ("vs_cum_memories", x_mem)):
            lr = stats.linregress(x, y); sp = stats.spearmanr(x, y)
            res = y - (lr.intercept + lr.slope * x)
            pr = [(res[i], res[i + 1]) for i in range(n - 1) if (ws[keep[i + 1]] - ws[keep[i]]).days == 7]
            ac = float(np.corrcoef(*zip(*pr))[0, 1]) if len(pr) > 3 else None
            out[nm] = dict(slope=lr.slope, slope_se=lr.stderr, pearson_r=lr.rvalue, pearson_p=lr.pvalue,
                           spearman_rho=float(sp.statistic), spearman_p=float(sp.pvalue), resid_autocorr_adjacent_calendar_weeks=ac, autocorr_pairs=len(pr))
    if n >= 5:
        ml = np.array([np.median(LEN[ws[i]]) for i in keep]); sp = stats.spearmanr(ml, y)
        out["rate_vs_median_len"] = dict(spearman_rho=float(sp.statistic), spearman_p=float(sp.pvalue))
        sp2 = stats.spearmanr(x_week, ml); out["median_len_vs_week_index"] = dict(spearman_rho=float(sp2.statistic), spearman_p=float(sp2.pvalue))
    out["weekly"] = {ws[i].isoformat(): [W[ws[i]][0], W[ws[i]][1]] for i in keep}
    return out
R["rq3_scope_min30"] = rq3(lambda p: p["h"] and p["proj"] == A.project and p["plat"] == "claude", 30)
R["rq3_scope_min15"] = rq3(lambda p: p["h"] and p["proj"] == A.project and p["plat"] == "claude", 15)
R["rq3_all_min30"] = rq3(lambda p: p["h"], 30)
R["rq3_all_strictfilter_min30"] = rq3(lambda p: p["h2"], 30)

# ------------------------------------------------------------------ RQ2 recurrence
# (a) self-annotation
KW_STRICT = re.compile(r"\b(repeated|recurred|recurrence|reoccurr\w*|re-?leak\w*|came back|second time|third time|"
                       r"happened twice|bitten by this twice|a miss)\b", re.I)
KW_LOOSE = re.compile(r"\b(again|twice|recurring|repeat\w*|same mistake|never-again)\b", re.I)
DATE_ANY = re.compile(r"\b(?:(\d{4})-(\d{2})-(\d{2})|(\d{2})-(\d{2}))\b")
def annot_dates(m):
    txt = " ".join([m["idx"], m["desc"], m["body"]])
    c = D(m["date"]); out = []
    for k in KW_STRICT.finditer(txt):
        seg = txt[max(0, k.start() - 40): k.end() + 70]
        for d in DATE_ANY.finditer(seg):
            try:
                if d.group(1): dd = dt.date(int(d.group(1)), int(d.group(2)), int(d.group(3)))
                else:
                    dd = dt.date(c.year, int(d.group(4)), int(d.group(5)))
                    if dd < c: dd = dt.date(c.year + 1, dd.month, dd.day)
            except ValueError: continue
            if c < dd <= END + dt.timedelta(days=1): out.append(dd)
    return sorted(set(out))
for m in MEM:
    txt = " ".join([m["idx"], m["desc"], m["body"]])
    m["a_strict"] = bool(KW_STRICT.search(txt)); m["a_loose"] = bool(KW_LOOSE.search(txt))
    m["a_dates"] = annot_dates(m)
    m["a_dated"] = bool(m["a_dates"])
    m["a_days"] = (m["a_dates"][0] - D(m["date"])).days if m["a_dates"] else None
R["rq2a_strict_keyword"] = sum(m["a_strict"] for m in MEM)
R["rq2a_loose_keyword_only_or_strict"] = sum(m["a_strict"] or m["a_loose"] for m in MEM)
R["rq2a_dated_recurrence"] = sum(m["a_dated"] for m in MEM)
R["rq2a_dated_user_mention_proxy"] = sum(m["a_dated"] and m["ua"] for m in MEM) if UA else None
R["rq2a_files_with_ge2_dated_recurrences"] = sum(len(m["a_dates"]) >= 2 for m in MEM)
dd = [m["a_days"] for m in MEM if m["a_dated"]]
R["rq2a_max_span_days"] = max([(m["a_dates"][-1] - D(m["date"])).days for m in MEM if m["a_dated"]], default=None)
R["rq2a_days_to_first_dated"] = dict(n=len(dd), median=float(np.median(dd)) if dd else None,
                                     min=min(dd) if dd else None, max=max(dd) if dd else None)

# (b) prompt-based
STOP = set("""about above after again against also because before being between both build cannot could does doing done
down during each every from have having here into just like made make many more most must never only other over same
should some such than that their them then there these they this those through under until upon very what when where
which while will with without would your user users agent agents claude feedback rule rules never always when must
need needs want wants said says tell told sir""".split())
def stem(w):
    for s in ("ing", "ed", "es", "s"):
        if w.endswith(s) and len(w) - len(s) >= 4: return w[: -len(s)]
    return w
def toks(s): return {stem(w) for w in re.findall(r"[a-z][a-z0-9]{3,}", s.lower()) if w not in STOP}
H = [p for p in PR if p["h"]]
for p in PR: p["tok"] = toks(p["t"][:1500]) if p["h"] else set()
df = collections.Counter(w for p in H for w in p["tok"])
DFMAX = 0.02 * len(H)
for m in MEM:
    kw = toks(m["name"].replace("-", " ").replace("_", " ") + " " + m["desc"])
    m["kw"] = {w for w in kw if df[w] < DFMAX and not re.match(r"\d", w)}
R["rq2b_df_threshold_prompts"] = DFMAX
R["rq2b_memories_with_ge3_keywords"] = sum(len(m["kw"]) >= 3 for m in MEM)
R["rq2b_median_keywords_per_memory"] = float(np.median([len(m["kw"]) for m in MEM]))
seen = set(); HS = []   # v2 pool: conversational prompts only (<=500 chars), first occurrence of each text
for p in sorted(H, key=lambda p: p["id"]):
    key = re.sub(r"\s+", " ", p["t"][:200].lower())
    if len(p["t"]) <= 500 and key not in seen: HS.append(p)
    seen.add(key)
R["prompts_v2_pool"] = len(HS)
R["prompts_v2_pool_scope"] = sum(p["proj"] == A.project and p["plat"] == "claude" for p in HS)
R["prompts_v2_pool_scope_marked"] = sum(p["c"] and p["proj"] == A.project and p["plat"] == "claude" for p in HS)

def matches(scope, minhits=2, window=None, direction=1, pool=None):
    """list of (memory index, prompt) with correction marker + >=minhits keyword overlap, strictly after
    (direction=1) or before (direction=-1) the memory date."""
    out = collections.defaultdict(list)
    for i, m in enumerate(MEM):
        if len(m["kw"]) < 3: continue
        c = D(m["date"])
        for p in (pool if pool is not None else H):
            if not p["c"] or not scope(p): continue
            gap = (p["d"] - c).days * direction
            if gap < 1 or (window and gap > window): continue
            if len(m["kw"] & p["tok"]) >= minhits: out[i].append((gap, p))
    return out
inscope = lambda p: p["proj"] == A.project and p["plat"] == "claude"
M_scope = matches(inscope)
M_all = matches(lambda p: True)
M_k3 = matches(inscope, minhits=3)
n_el = sum(len(m["kw"]) >= 3 for m in MEM)
R["rq2b_scope_memories_with_match"] = len(M_scope)
R["rq2b_all_memories_with_match"] = len(M_all)
R["rq2b_scope_k3_memories_with_match"] = len(M_k3)
R["rq2b_eligible_memories"] = n_el
R["rq2b_scope_share_raw"] = len(M_scope) / n_el
_w = lambda k, n, z=1.96: [(k/n + z*z/(2*n) - z*math.sqrt(k/n*(1-k/n)/n + z*z/(4*n*n)))/(1+z*z/n), (k/n + z*z/(2*n) + z*math.sqrt(k/n*(1-k/n)/n + z*z/(4*n*n)))/(1+z*z/n)]
R["rq2a_wilson95"] = {"dated": _w(R["rq2a_dated_recurrence"], len(MEM)), "strict_keyword": _w(R["rq2a_strict_keyword"], len(MEM)),
                      "any_keyword": _w(R["rq2a_loose_keyword_only_or_strict"], len(MEM))}
R["rq2b_scope_matched_prompts"] = len({p["id"] for v in M_scope.values() for _, p in v})
first_gap = {i: min(g for g, _ in v) for i, v in M_scope.items()}
R["rq2b_days_to_first_match"] = dict(n=len(first_gap), median=float(np.median(list(first_gap.values()))) if first_gap else None,
                                     q25=float(np.percentile(list(first_gap.values()), 25)) if first_gap else None,
                                     q75=float(np.percentile(list(first_gap.values()), 75)) if first_gap else None)
# censoring-aware cumulative incidence at horizons (only memories with full follow-up)
inc = {}
for h in (7, 14, 30, 60):
    el = [i for i, m in enumerate(MEM) if len(m["kw"]) >= 3 and (END - D(m["date"])).days >= h]
    hit = sum(1 for i in el if i in first_gap and first_gap[i] <= h)
    inc[str(h)] = dict(eligible=len(el), hit=hit, share=hit / len(el) if el else None)
R["rq2b_cumulative_incidence"] = inc
# pre/post placebo: same window length before vs after memory creation
pre = collections.defaultdict(list)
M_pre = matches(inscope, window=30, direction=-1)
M_post = matches(inscope, window=30, direction=1)
el = [i for i, m in enumerate(MEM) if len(m["kw"]) >= 3 and (D(m["date"]) - DBSTART).days >= 30 and (END - D(m["date"])).days >= 30]
R["rq2b_placebo_30d"] = dict(eligible=len(el), post_hit=sum(i in M_post for i in el), pre_hit=sum(i in M_pre for i in el),
                             both=sum(i in M_post and i in M_pre for i in el))
a, b = sum(i in M_post and i not in M_pre for i in el), sum(i in M_pre and i not in M_post for i in el)
M_pre3 = matches(inscope, minhits=3, window=30, direction=-1); M_post3 = matches(inscope, minhits=3, window=30, direction=1)
R["rq2b_placebo_30d_k3"] = dict(eligible=len(el), post_hit=sum(i in M_post3 for i in el), pre_hit=sum(i in M_pre3 for i in el))
R["rq2b_placebo_30d"]["post_only"], R["rq2b_placebo_30d"]["pre_only"] = a, b
if a + b: R["rq2b_placebo_30d"]["mcnemar_exact_p"] = float(stats.binomtest(a, a + b, 0.5).pvalue)
M2 = matches(inscope, pool=HS)
R["rq2b_v2_memories_with_match"] = len(M2)
R["rq2b_v2_share_raw"] = len(M2) / n_el
M2_pre = matches(inscope, window=30, direction=-1, pool=HS); M2_post = matches(inscope, window=30, direction=1, pool=HS)
R["rq2b_v2_placebo_30d"] = dict(eligible=len(el), post_hit=sum(i in M2_post for i in el), pre_hit=sum(i in M2_pre for i in el))
first2 = {i: min(g for g, _ in v) for i, v in M2.items()}
R["rq2b_v2_first_gaps_days"] = sorted(first2.values())
# overlap between (a) and (b)
sa = {i for i, m in enumerate(MEM) if m["a_dated"]}
R["rq2_overlap_dated_and_prompt"] = len(sa & set(M_scope))
R["rq2_dated_annotated_with_len_ge3"] = len([i for i in sa if len(MEM[i]["kw"]) >= 3])

# ------------------------------------------------------------------ hand-check sample
first_pairs = []
for i, v in M_scope.items():
    g, p = min(v, key=lambda x: (x[0], x[1]["id"]))
    first_pairs.append((i, g, p))
first_pairs.sort(key=lambda x: x[0])
def firsts(Mx):
    out = []
    for i, v in Mx.items():
        g, p = min(v, key=lambda x: (x[0], x[1]["id"])); out.append((i, g, p))
    return sorted(out, key=lambda x: x[0])
first2_pairs = firsts(M2)
S2 = random.Random(11).sample(first2_pairs, min(30, len(first2_pairs)))
CS = random.Random(7).sample(MEM, min(30, len(MEM)))
S = random.sample(first_pairs, min(30, len(first_pairs)))
R["handcheck_pool"] = len(first_pairs)
R["handcheck_n"] = len(S)
fp = lambda items: hashlib.sha256(json.dumps(items).encode()).hexdigest()[:16]
FP = dict(v1=fp([[MEM[i]["file"], p["id"]] for i, g, p in S]), v2=fp([[MEM[i]["file"], p["id"]] for i, g, p in S2]),
          taxonomy=fp([m["file"] for m in CS]))
R["sample_fingerprints"] = FP
EXPORT_CHARS = 1500   # same window the matcher reads (tokens come from the first 1,500 characters)
if A.private_dir:
    os.makedirs(A.private_dir, exist_ok=True)
    wr = lambda name, obj: json.dump(obj, open(os.path.join(A.private_dir, name), "w"), indent=1)
    wr("handcheck_sample.json", dict(fingerprint=FP["v1"], items=[dict(k=k, mem_file=MEM[i]["file"], mem_name=MEM[i]["name"], mem_desc=MEM[i]["desc"],
        mem_date=MEM[i]["date"], gap_days=g, kw_hit=sorted(MEM[i]["kw"] & p["tok"]), prompt=p["t"][:EXPORT_CHARS]) for k, (i, g, p) in enumerate(S)]))
    wr("handcheck_v2_sample.json", dict(fingerprint=FP["v2"], items=[dict(k=k, mem_name=MEM[i]["name"], mem_desc=MEM[i]["desc"], gap_days=g,
        kw_hit=sorted(MEM[i]["kw"] & p["tok"]), prompt=p["t"][:EXPORT_CHARS]) for k, (i, g, p) in enumerate(S2)]))
    wr("catcheck_sample.json", dict(fingerprint=FP["taxonomy"], items=[dict(k=k, file=m["file"], cat=m["cat"], name=m["name"], desc=m["desc"]) for k, m in enumerate(CS)]))
    wr("annotated_recurrence.json", [dict(file=m["file"], date=m["date"], dates=[d.isoformat() for d in m["a_dates"]], cat=m["cat"])
                                     for m in MEM if m["a_strict"] or m["a_dated"]])
    wr("categories.json", [dict(file=m["file"], cat=m["cat"], date=m["date"]) for m in MEM])
def wilson(k, n, z=1.96):
    ph = k / n
    c = (ph + z * z / (2 * n)); r = z * math.sqrt(ph * (1 - ph) / n + z * z / (4 * n * n)); d = 1 + z * z / n
    return (c - r) / d, (c + r) / d
R["handcheck_v2_pool"] = len(first2_pairs); R["handcheck_v2_n"] = len(S2)
LABELS = {}
for flag, key, fpk, size in ((A.labels, "handcheck", "v1", len(S)), (A.labels2, "handcheck_v2", "v2", len(S2)),
                             (A.labels_lenient, "handcheck_lenient", "v1", len(S)), (A.catlabels, "taxonomy_check", "taxonomy", len(CS))):
    if flag:
        lab = lab_load(flag, FP[fpk]); LABELS[key] = flag
        assert len(lab) == size, (key, len(lab), size)
        k, n = sum(lab), len(lab); lo, hi = wilson(k, n)
        R[key] = dict(n=n, positives=k, share=k / n, wilson95=[lo, hi], labels=lab)
        if key == "handcheck":
            R[key]["true_positive"] = k; R[key]["precision"] = k / n
            R["rq2b_v1_judged_earliest_match_pairs_scaled"] = dict(point=R["rq2b_scope_share_raw"] * k / n,
                low=R["rq2b_scope_share_raw"] * lo, high=R["rq2b_scope_share_raw"] * hi,
                note="raw share x precision of judged earliest-match pairs; recall unknown; not a validated recurrence rate")
        if key == "handcheck_v2":
            R["rq2b_v2_judged_earliest_match_pairs_scaled"] = dict(point=R["rq2b_v2_share_raw"] * k / n,
                low=R["rq2b_v2_share_raw"] * lo, high=R["rq2b_v2_share_raw"] * hi,
                note="raw share x precision of judged earliest-match pairs; recall unknown; not a validated recurrence rate")
            pos = [S2[j][2]["id"] for j, v in enumerate(lab) if v == 1]
            R[key]["distinct_prompts_among_positives"] = len(set(pos))
cat_hit = collections.defaultdict(lambda: [0, 0])
for i, m in enumerate(MEM):
    if len(m["kw"]) >= 3:
        cat_hit[m["cat"]][0] += 1; cat_hit[m["cat"]][1] += i in M_scope
R["rq2b_by_category"] = {c: dict(eligible=v[0], matched=v[1]) for c, v in cat_hit.items()}
cat_ann = collections.defaultdict(lambda: [0, 0])
for m in MEM:
    cat_ann[m["cat"]][0] += 1; cat_ann[m["cat"]][1] += m["a_dated"]
R["rq2a_by_category"] = {c: dict(n=v[0], dated=v[1]) for c, v in cat_ann.items()}
R["rq2b_first_gaps_days"] = sorted(first_gap.values())
R["rq2a_gaps_days"] = sorted(dd)

# ------------------------------------------------------------------ RQ4 storage-side economics
r4 = db.execute("""select count(*) n, sum(discovery_tokens) work,
        sum(length(coalesce(title,''))+length(coalesce(subtitle,''))+length(coalesce(narrative,''))+length(coalesce(facts,''))+length(coalesce(concepts,''))) chars,
        sum(coalesce(relevance_count,0)) rel, sum(case when relevance_count>0 then 1 else 0 end) relpos,
        sum(case when discovery_tokens>0 then 1 else 0 end) workpos from observations""").fetchone()
grp = db.execute("""select count(*), sum(dt), sum(c > 1) from (select discovery_tokens dt, count(*) c from observations
                    group by memory_session_id, prompt_number, discovery_tokens)""").fetchone()
R["rq4"] = dict(observations=r4["n"], discovery_tokens_field_sum_over_rows=r4["work"], stored_chars_sum=r4["chars"],
                stored_tokens_proxy_chars_div4=r4["chars"] / 4,
                discovery_tokens_sum_over_distinct_session_prompt_value_groups=grp[1], groups=grp[0], groups_with_repeated_value_rows=grp[2],
                obs_with_discovery_tokens=r4["workpos"], relevance_count_sum=r4["rel"], obs_with_relevance_count_positive=r4["relpos"],
                file_context_injections_rows=db.execute("select count(*) from file_context_injections").fetchone()[0],
                reread_tokens_measurable=False)
R["rq4"]["median_discovery_tokens"] = float(np.median([x[0] for x in db.execute("select discovery_tokens from observations where discovery_tokens>0")] or [0]))
R["rq4"]["index_tokens_proxy_chars_div4"] = R["memory_index_chars"] / 4
R["rq4"]["all_feedback_tokens_proxy_chars_div4"] = R["feedback_chars_total"] / 4
R["rq4"]["index_share_of_feedback_corpus_chars"] = R["memory_index_chars"] / R["feedback_chars_total"]

# ------------------------------------------------------------------ provenance
def memdir_hash():
    h = hashlib.sha256()
    for f in files:
        h.update(os.path.basename(f).encode()); h.update(sha_file(f).encode())
    return h.hexdigest()
def ver(n):
    try:
        import importlib.metadata as im; return im.version(n)
    except Exception: return None
R["provenance"] = dict(db_sha256=sha_file(A.db), memdir_sha256=memdir_hash(), label_files_sha256={k: sha_file(v) for k, v in LABELS.items()},
                       python=sys.version.split()[0], platform=platform.platform(), numpy=ver("numpy"), scipy=ver("scipy"),
                       matplotlib=ver("matplotlib"), seed=SEED, user_aliases_given=bool(ALIASES), analyze_py_sha256=sha_file(__file__))

def clean(o):
    if isinstance(o, dict): return {str(k): clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)): return [clean(v) for v in o]
    if isinstance(o, (np.floating, np.integer)): o = o.item()
    if isinstance(o, float) and (o != o or o in (float("inf"), float("-inf"))): return None
    if isinstance(o, (dt.date,)): return o.isoformat()
    return o
os.makedirs(os.path.dirname(os.path.abspath(A.out)), exist_ok=True)
json.dump(clean(R), open(A.out, "w"), indent=1, sort_keys=True)
print("wrote", A.out)
