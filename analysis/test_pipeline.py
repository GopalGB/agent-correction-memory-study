#!/usr/bin/env python3
"""Self-test: builds a tiny synthetic claude-mem-style SQLite DB and memory dir, then runs
analyze.py and make_figures.py on it. Synthetic data only. Usage: python3 test_pipeline.py"""
import datetime as dt, json, os, random, sqlite3, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
rnd = random.Random(1)
WORDS = "alpha bravo carbon delta echo foxtrot garnet harbor indigo jasper kilo lemon mango nectar olive pepper quartz raven sierra tango umber violet walnut xenon yellow zephyr".split()

def build(tmp):
    db = os.path.join(tmp, "t.db"); mem = os.path.join(tmp, "mem"); os.makedirs(mem)
    c = sqlite3.connect(db)
    c.executescript("""
    create table sdk_sessions(id integer primary key, content_session_id text unique, memory_session_id text, project text,
        platform_source text, started_at text, started_at_epoch integer);
    create table user_prompts(id integer primary key, content_session_id text, prompt_number integer, prompt_text text,
        created_at text, created_at_epoch integer);
    create table observations(id integer primary key, memory_session_id text, project text, type text, title text, subtitle text,
        facts text, narrative text, concepts text, prompt_number integer, discovery_tokens integer, relevance_count integer,
        created_at text, created_at_epoch integer);
    create table file_context_injections(session_id text, file_path text);""")
    c.execute("insert into sdk_sessions values(1,'s1','m1','SynthProject','claude','2026-01-05T00:00:00Z',0)")
    start = dt.datetime(2026, 1, 5, 12, tzinfo=dt.timezone.utc)
    pid = 0
    for w in range(10):
        for k in range(40):
            t = start + dt.timedelta(weeks=w, hours=k)
            a, b, d = rnd.sample(WORDS, 3)
            text = f"{a}{k}{w} {b} {d} please look at section {k}"
            if k == 0 and w in (5, 7, 9): text = f"{a}{k}{w} why did you again skip the deployment checksum"
            pid += 1
            e = int(t.timestamp() * 1000)
            c.execute("insert into user_prompts values(?,?,?,?,?,?)", (pid, "s1", pid, text, t.isoformat(), e))
        if w != 4:   # week 4 has zero observations (interior gap)
            for k in range(5):
                t = start + dt.timedelta(weeks=w, hours=k)
                c.execute("insert into observations(memory_session_id,project,type,title,narrative,facts,subtitle,concepts,prompt_number,discovery_tokens,relevance_count,created_at,created_at_epoch) values('m1','SynthProject','discovery','t','n','f','s','c',1,100,0,?,?)",
                          (t.isoformat(), int(t.timestamp() * 1000)))
    c.commit(); c.close()
    for i in range(10):
        d = (dt.date(2026, 1, 12) + dt.timedelta(weeks=i)).isoformat()
        desc = "Always verify the deployment checksum before reporting success" if i == 0 else f"Rule {WORDS[i]} about {WORDS[i+5]} handling and {WORDS[i+10]} limits"
        open(os.path.join(mem, f"feedback_rule_{i}_{d}.md"), "w").write(
            f"---\nname: rule-{i}\ndescription: \"{desc}\"\n---\nBody for Alice repeated {d}.\n")
    open(os.path.join(mem, "MEMORY.md"), "w").write("- [rule 0](feedback_rule_0.md) - hook\n")
    return db, mem

def run(*a):
    r = subprocess.run([sys.executable, "-I", *a], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-2000:]
    return r.stdout

def main():
    with tempfile.TemporaryDirectory() as tmp:
        db, mem = build(tmp)
        out = os.path.join(tmp, "res", "agg.json")
        run(os.path.join(HERE, "analyze.py"), "--db", db, "--memdir", mem, "--project", "SynthProject", "--out", out)
        R = json.load(open(out))
        assert R["feedback_files"] == 10 and sum(R["rq1_categories"].values()) == 10
        assert R["obs_weekly_zero_interior_weeks"] == 1 and len(R["obs_weekly"]) == 10   # zero week kept, no gaps
        assert R["rq1_user_mention_proxy"] is None                                      # no aliases -> proxy skipped
        assert R["rq2b_scope_memories_with_match"] >= 1                                 # planted recurrence found
        assert R["provenance"]["db_sha256"] and R["provenance"]["memdir_sha256"]
        assert "handcheck" not in R                                                     # no labels -> no hand-check block
        out2 = os.path.join(tmp, "res", "agg2.json")
        run(os.path.join(HERE, "analyze.py"), "--db", db, "--memdir", mem, "--project", "SynthProject", "--out", out2, "--user-aliases", "Alice")
        assert json.load(open(out2))["rq1_user_mention_proxy"] == 10
        figs = os.path.join(tmp, "figs")
        note = run(os.path.join(HERE, "make_figures.py"), "--agg", out, "--out", figs)
        got = sorted(os.listdir(figs)); assert len(got) == 4 and all(g.endswith(".png") for g in got), got
        assert "no hand-check labels" in note
        print("PASS: analyze.py + make_figures.py on synthetic data;", len(got), "figures;",
              "weeks with zero observations preserved; hand-check panels skipped without labels")

if __name__ == "__main__":
    main()
