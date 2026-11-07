#!/usr/bin/env python3
"""Make figures from results/aggregates.json only (no raw data).
Usage: python3 make_figures.py --agg results/aggregates.json --out figures/
Works without hand-check label files: the hand-check counts are then simply omitted from the Figure 3 legend."""
import argparse, datetime as dt, json, os
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from scipy import stats

P = argparse.ArgumentParser(); P.add_argument("--agg", required=True); P.add_argument("--out", required=True)
A = P.parse_args(); R = json.load(open(A.agg)); os.makedirs(A.out, exist_ok=True)
BLUE, ORANGE, GREY, GREEN = "#0072B2", "#D55E00", "#6b6b6b", "#009E73"
plt.rcParams.update({"font.size": 9, "axes.spines.top": False, "axes.spines.right": False, "figure.dpi": 150})
D = dt.date.fromisoformat

# Fig 1: growth
fig, ax = plt.subplots(2, 1, figsize=(6.4, 5), sharex=True)
w = R["rq1_weekly_new"]; first = D(min(w)); last = D(max(w))
weeks = [first + dt.timedelta(days=7 * i) for i in range((last - first).days // 7 + 1)]
y = [w.get(x.isoformat(), 0) for x in weeks]
ax[0].bar(weeks, y, width=5, color=BLUE, label="new correction files per week")
ax[0].set_ylabel("new files / week")
a2 = ax[0].twinx(); a2.plot(weeks, np.cumsum(y), color=ORANGE, lw=2, label="cumulative"); a2.set_ylabel("cumulative files")
a2.spines["right"].set_visible(True)
ax[0].set_title("Correction memory files created per week")
ow = R["obs_weekly"]
ks = sorted(ow)   # zero-filled calendar weeks (no gaps)
ax[1].plot([D(k) for k in ks], [ow[k] for k in ks], color=GREY, lw=1.5, marker="o", ms=2.5)
ax[1].set_ylabel("observations / week (zero weeks shown)"); ax[1].set_xlabel("week starting (UTC)")
ax[1].set_title("Layer 1: auto-captured observations per week")
fig.autofmt_xdate(); fig.tight_layout(); fig.savefig(f"{A.out}/fig1_growth.png"); plt.close(fig)

# Fig 2: taxonomy
c = R["rq1_categories"]; ua = R.get("rq1_user_mention_proxy_by_category")
names = sorted(c, key=c.get); vals = [c[k] for k in names]
fig, ax = plt.subplots(figsize=(6.4, 3.4))
ax.barh([n.replace("_", " ") for n in names], vals, color=BLUE, label="all files")
if ua:
    ax.barh([n.replace("_", " ") for n in names], [ua[k]["ua"] for k in names], color=ORANGE, label="of which the text names the user (proxy, not authorship)")
for i, v in enumerate(vals): ax.text(v + 0.4, i, str(v), va="center")
ax.set_xlabel("number of feedback files (n=%d)" % sum(vals)); ax.legend(frameon=False, loc="lower right") if ua else None
ax.set_title("Rule-based taxonomy of correction memories"); fig.tight_layout(); fig.savefig(f"{A.out}/fig2_taxonomy.png"); plt.close(fig)

# Fig 3: recurrence
fig, ax = plt.subplots(1, 2, figsize=(7.2, 3.4))
g1 = R["rq2b_first_gaps_days"]; g2 = R["rq2b_v2_first_gaps_days"]; ga = R["rq2a_gaps_days"]
hc = R.get("handcheck"); hc2 = R.get("handcheck_v2")
tag1 = f", true {hc['true_positive']}/{hc['n']}" if hc else ""
tag2 = f", true {hc2['positives']}/{hc2['n']}" if hc2 else ""
if not (hc and hc2): print("note: no hand-check labels in aggregates; hand-check counts omitted from Figure 3 legend")
for g, col, lab in ((g1, BLUE, f"v1 heuristic (n={len(g1)}{tag1})"), (g2, ORANGE, f"v2 heuristic (n={len(g2)}{tag2})"),
                    (ga, GREEN, f"self-annotated dated (n={len(ga)})")):
    if not len(g): continue
    xs = np.sort(g); ax[0].step(np.r_[0, xs], np.r_[0, np.arange(1, len(xs) + 1)], where="post", color=col, lw=2, label=lab)
ax[0].set_xlabel("days from memory creation to first recurrence signal"); ax[0].set_ylabel("memories (cumulative count)")
ax[0].legend(frameon=False, fontsize=7, loc="lower right"); ax[0].set_title("Time to first recurrence signal")
pl = R["rq2b_placebo_30d"]; p2 = R["rq2b_v2_placebo_30d"]
x = np.arange(2); wd = 0.35
ax[1].bar(x - wd / 2, [pl["pre_hit"], p2["pre_hit"]], wd, color=GREY, label="30 days BEFORE creation")
ax[1].bar(x + wd / 2, [pl["post_hit"], p2["post_hit"]], wd, color=BLUE, label="30 days AFTER creation")
for i, (a, b) in enumerate(((pl["pre_hit"], pl["post_hit"]), (p2["pre_hit"], p2["post_hit"]))):
    ax[1].text(i - wd / 2, a + .1, str(a), ha="center"); ax[1].text(i + wd / 2, b + .1, str(b), ha="center")
ax[1].set_xticks(x, ["v1 (all human prompts)", "v2 (short, deduplicated)"]); ax[1].set_ylabel(f"memories with a match (of {pl['eligible']})")
ax[1].legend(frameon=False, fontsize=7); ax[1].set_title("Placebo: matches before vs after")
fig.tight_layout(); fig.savefig(f"{A.out}/fig3_recurrence.png"); plt.close(fig)

# Fig 4: correction rate vs memory growth
fig, ax = plt.subplots(1, 2, figsize=(7.2, 3.4), sharey=True)
feed = R["rq1_weekly_new"]
def cum_before(wk):
    return sum(v for k, v in feed.items() if D(k) < D(wk))
for a, key, ttl, col in ((ax[0], "rq3_scope_min30", "Memory-scope sessions", BLUE), (ax[1], "rq3_all_min30", "All human-typed prompts", ORANGE)):
    W = R[key]["weekly"]; ks = sorted(W)
    if len(ks) < 3:
        a.set_title(f"{ttl}: too few weeks"); continue
    xs = np.array([cum_before(k) for k in ks], float); ys = np.array([W[k][1] / W[k][0] for k in ks]) * 100
    a.scatter(xs, ys, color=col, s=24)
    lr = stats.linregress(xs, ys); xx = np.array([xs.min(), xs.max()])
    a.plot(xx, lr.intercept + lr.slope * xx, color="black", lw=1)
    sp = R[key]["vs_cum_memories"]
    a.set_title(f"{ttl}\n{len(ks)} weeks, Spearman rho={sp['spearman_rho']:.2f} (p={sp['spearman_p']:.2f})", fontsize=8)
    a.set_xlabel("correction files existing at week start")
ax[0].set_ylabel("% of weekly prompts with a correction marker")
fig.tight_layout(); fig.savefig(f"{A.out}/fig4_rate_vs_memory.png"); plt.close(fig)
print("figures written to", A.out)
