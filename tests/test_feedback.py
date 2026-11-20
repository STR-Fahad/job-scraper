"""Good fit / Not for me answers: an append-only opinion that moves jobs between dashboard tabs."""

import sqlite3
from types import SimpleNamespace

import pytest
from test_dashboard import call, env, post  # noqa: F401  (env is a fixture)
from test_storage import job

from job_scraper import cli, feedback
from job_scraper.dashboard import focus
from job_scraper.storage import Store


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "jobs.sqlite3") as opened:
        opened.upsert(job())
        yield opened


def test_record_validates_and_stores_the_tag_the_user_saw(store):
    first = feedback.record(
        store,
        1,
        "good_fit",
        note="  looks right  ",
        final_status="ELIGIBLE",
        final_code="good_enough_fit",
        assessment_id=7,
    )
    assert first["verdict"] == "good_fit" and first["note"] == "looks right"
    assert (first["final_status"], first["final_code"], first["assessment_id"]) == (
        "ELIGIBLE",
        "good_enough_fit",
        7,
    )
    with pytest.raises(ValueError, match="verdict must be"):
        feedback.record(store, 1, "maybe")
    with pytest.raises(ValueError, match="No job with id"):
        feedback.record(store, 99, "good_fit")
    with pytest.raises(ValueError, match="note exceeds"):
        feedback.record(store, 1, "good_fit", note="x" * 1001)


def test_latest_follows_the_newest_pick_and_a_cleared_pick_is_not_a_pick(store):
    assert feedback.latest(store.db) == {} and feedback.history(store.db) == []
    feedback.record(store, 1, "good_fit")
    feedback.record(store, 1, "not_for_me")
    assert feedback.latest(store.db)[1]["verdict"] == "not_for_me"
    feedback.record(store, 1, "cleared")
    assert feedback.latest(store.db) == {}
    assert [e["verdict"] for e in feedback.history(store.db, 1)] == [
        "good_fit",
        "not_for_me",
        "cleared",
    ]


def test_the_table_is_append_only_and_created_after_a_verified_backup(store, tmp_path):
    assert not feedback.has_schema(store.db)
    feedback.record(store, 1, "good_fit")
    pattern = "jobs-pre-feedback-*.sqlite3"
    backups = list((tmp_path / "backups").glob(pattern))
    assert len(backups) == 1
    feedback.record(store, 1, "not_for_me")  # no second backup
    assert len(list((tmp_path / "backups").glob(pattern))) == 1
    with sqlite3.connect(backups[0]) as saved:
        assert saved.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        store.db.execute("UPDATE fit_feedback SET verdict='good_fit'")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        store.db.execute("DELETE FROM fit_feedback")


def test_picks_move_jobs_between_the_tag_tabs_without_changing_tags(env):  # noqa: F811
    app, path, assessed = env
    title = "Software Engineer, AI Coding Tasks"
    _, _, before = call(app, "GET", "/?d=UNVERIFIED")
    assert title in before and "Good fit" in before and "Not for me" in before
    assert 'href="/?d=REVIEW"' in before
    tag_before = app.cache.current()[0].by_id[assessed]["final_status"]
    assert tag_before == "UNVERIFIED"

    # Good fit moves it into the Eligible tab
    status, headers, _ = post(
        app, f"/jobs/{assessed}/fit", {"verdict": "good_fit", "next": "/?d=UNVERIFIED"}
    )
    assert status == 303 and "Marked%20Good%20fit" in headers["Location"]
    _, _, unverified = call(app, "GET", "/?d=UNVERIFIED")
    assert title not in unverified
    _, _, eligible = call(app, "GET", "/?d=ELIGIBLE")
    assert title in eligible and "You: Good fit" in eligible
    assert "the tool said Unverified" in eligible
    assert "Mark applied" in eligible and "Undo" in eligible

    # Not for me moves it into the Ineligible tab (and out of Eligible)
    post(app, f"/jobs/{assessed}/fit", {"verdict": "not_for_me", "next": "/?d=ELIGIBLE"})
    _, _, ineligible = call(app, "GET", "/?d=INELIGIBLE")
    assert title in ineligible and "You: Not for me" in ineligible
    _, _, eligible_after = call(app, "GET", "/?d=ELIGIBLE")
    assert title not in eligible_after

    # Undo withdraws the answer: the tool's own tag decides again
    post(app, f"/jobs/{assessed}/fit", {"verdict": "cleared", "next": "/?d=INELIGIBLE"})
    _, _, back = call(app, "GET", "/?d=UNVERIFIED")
    assert title in back and "You:" not in back
    # the answer is only an opinion: the tool's tag never changed
    assert app.cache.current()[0].by_id[assessed]["final_status"] == tag_before
    with Store(path) as opened:
        rows = feedback.history(opened.db, assessed)
    assert [r["verdict"] for r in rows] == ["good_fit", "not_for_me", "cleared"]
    assert rows[0]["final_status"] == tag_before


def test_a_bad_verdict_and_a_missing_token_are_refused(env):  # noqa: F811
    app, path, assessed = env
    status, headers, _ = post(app, f"/jobs/{assessed}/fit", {"verdict": "maybe", "next": "/"})
    assert status == 303 and "error=" in headers["Location"]
    status, _, _ = call(app, "POST", f"/jobs/{assessed}/fit", {"verdict": "good_fit"})
    assert status == 403


def test_an_applied_job_leaves_the_tabs_even_with_a_pick(env):  # noqa: F811
    app, path, assessed = env
    post(app, f"/jobs/{assessed}/fit", {"verdict": "good_fit", "next": "/"})
    post(app, f"/jobs/{assessed}/status", {"status": "APPLIED", "next": "/?d=ELIGIBLE"})
    app.cache.wait()
    _, _, eligible = call(app, "GET", "/?d=ELIGIBLE")
    assert "Software Engineer, AI Coding Tasks" not in eligible
    _, _, applied = call(app, "GET", "/?d=APPLIED")
    assert "Software Engineer, AI Coding Tasks" in applied


def _row(job_id, status, code, title):
    return {
        "job_id": job_id,
        "final_status": status,
        "final_code": code,
        "lifecycle": "active",
        "listed": True,
        "sources": ["remoteok"],
        "score": 10,
        "title": title,
        "company": "Acme",
    }


def _snapshot(rows):
    return SimpleNamespace(
        rows=rows,
        by_id={r["job_id"]: r for r in rows},
        population_by_id={},
        counts={"final": {"not_judged": 0}},
    )


def test_good_enough_tags_wait_in_to_review_until_you_answer():
    rows = [
        _row(1, "ELIGIBLE", "good_enough_fit", "Weak match"),
        _row(2, "ELIGIBLE", "session_judgment_eligible", "Strong match"),
        _row(3, "UNVERIFIED", "session_judgment_unverified", "Unclear"),
        _row(4, "INELIGIBLE", "out_of_scope", "Support role"),
    ]
    snap = _snapshot(rows)

    def tab(view, fit=None):
        return focus.build(snap, {}, {"d": view}, fit)

    counts = tab("REVIEW")["counts"]
    assert [counts[k] for k in ("REVIEW", "ELIGIBLE", "UNVERIFIED", "INELIGIBLE")] == [1, 1, 1, 1]
    assert [i["job_id"] for i in tab("REVIEW")["items"]] == [1]
    assert [i["job_id"] for i in tab("ELIGIBLE")["items"]] == [2]
    # Good fit on the waiting job confirms it into Eligible; Not for me sends it to Ineligible
    good = {1: {"verdict": "good_fit", "final_status": "ELIGIBLE", "note": None}}
    assert sorted(i["job_id"] for i in tab("ELIGIBLE", good)["items"]) == [1, 2]
    assert tab("REVIEW", good)["items"] == []
    bad = {1: {"verdict": "not_for_me", "final_status": "ELIGIBLE", "note": None}}
    assert sorted(i["job_id"] for i in tab("INELIGIBLE", bad)["items"]) == [1, 4]
    # a Good fit on an Ineligible job (a miss) moves it into Eligible
    miss = {4: {"verdict": "good_fit", "final_status": "INELIGIBLE", "note": None}}
    assert 4 in [i["job_id"] for i in tab("ELIGIBLE", miss)["items"]]


def test_report_compares_your_answers_with_the_tool_tags(store):
    assert feedback.report(store.db)["picks"] == 0
    assert "No Good fit" in feedback.render_report(feedback.report(store.db))
    picks = (
        ("good_fit", "ELIGIBLE", "good_enough_fit"),
        ("not_for_me", "ELIGIBLE", "good_enough_fit"),
        ("good_fit", "ELIGIBLE", "session_judgment_eligible"),
        ("good_fit", "INELIGIBLE", "experience_gap"),
        ("not_for_me", "INELIGIBLE", "out_of_scope"),
    )
    for number, (verdict, tag, code) in enumerate(picks):
        store.upsert(job(source_id=f"s{number}", source_url=f"https://x.test/{number}"))
        job_id = store.db.execute("SELECT max(id) FROM jobs").fetchone()[0]
        feedback.record(store, job_id, verdict, final_status=tag, final_code=code)
    data = feedback.report(store.db)
    assert data["picks"] == 5
    assert data["matrix"]["ELIGIBLE"] == {"good_fit": 2, "not_for_me": 1}
    assert data["matrix"]["INELIGIBLE"] == {"good_fit": 1, "not_for_me": 1}
    assert data["eligible_confirmed_pct"] == 67 and data["ineligible_wanted_pct"] == 50
    assert data["agreement_pct"] == 60  # (2 + 1) of 5
    expected = {"tag": "ELIGIBLE", "good_fit": 1, "not_for_me": 1}
    assert data["by_rule"]["good_enough_fit"] == expected
    text = feedback.render_report(data)
    assert "Eligible tags you confirmed: 67%" in text and "good_enough_fit" in text


def test_calibration_page_and_cli_report(env, tmp_path, capsys):  # noqa: F811
    app, path, assessed = env
    _, _, empty = call(app, "GET", "/calibration")
    assert "No answers yet" in empty
    post(app, f"/jobs/{assessed}/fit", {"verdict": "good_fit", "next": "/"})
    _, _, page = call(app, "GET", "/calibration")
    assert "1 answer(s)" in page and "UNVERIFIED" in page
    config = tmp_path / "config.toml"
    config.write_text(
        f'database = "{path.as_posix()}"\n[[sources]]\nsource="arbeitnow"', encoding="utf-8"
    )
    assert cli.main(["feedback", "report", "--config", str(config), "--json"]) == 0
    assert '"picks": 1' in capsys.readouterr().out
    assert cli.main(["feedback", "list", "--config", str(config)]) == 0
    assert "tool said UNVERIFIED" in capsys.readouterr().out


def _post_form(app, path, pairs):
    """POST several fields, including repeated ones (checkboxes share a name)."""
    import io
    from urllib.parse import urlencode

    body = urlencode([("csrf", app.csrf), *pairs]).encode()
    environ = {
        "REQUEST_METHOD": "POST",
        "PATH_INFO": path,
        "QUERY_STRING": "",
        "HTTP_HOST": "127.0.0.1:8765",
        "CONTENT_LENGTH": str(len(body)),
        "wsgi.input": io.BytesIO(body),
    }
    captured = {}

    def start(status, headers):
        captured["status"] = status

    b"".join(app(environ, start))
    return int(captured["status"].split()[0])


def test_reasons_are_validated_ordered_and_stored(store):
    entry = feedback.record(
        store, 1, "not_for_me", reasons=["language", "too_senior", "language"], note="German"
    )
    # stored in the fixed list order, duplicates dropped
    assert entry["reasons"] == "too_senior,language"
    assert entry["reason_list"] == ["too_senior", "language"]
    with pytest.raises(ValueError, match="unknown reason: nonsense"):
        feedback.record(store, 1, "not_for_me", reasons=["nonsense"])
    plain = feedback.record(store, 1, "good_fit")
    assert plain["reasons"] is None and plain["reason_list"] == []


def test_an_earlier_table_without_reasons_gets_the_column_after_a_backup(tmp_path):
    path = tmp_path / "jobs.sqlite3"
    with Store(path) as opened:
        opened.upsert(job())
        opened.db.execute(
            "CREATE TABLE fit_feedback (id INTEGER PRIMARY KEY, job_id INTEGER NOT NULL, "
            "verdict TEXT NOT NULL, note TEXT, final_status TEXT, final_code TEXT, "
            "assessment_id INTEGER, created_at TEXT NOT NULL)"
        )
        opened.db.execute(
            "INSERT INTO fit_feedback(job_id,verdict,created_at) VALUES (1,'good_fit','t')"
        )
        opened.db.commit()
        # a read of the old table works before the migration
        assert feedback.history(opened.db)[0]["reasons"] is None
        feedback.record(opened, 1, "not_for_me", reasons=["location"])
        assert [e["reasons"] for e in feedback.history(opened.db)] == [None, "location"]
    assert list((tmp_path / "backups").glob("jobs-pre-feedback-reasons-*.sqlite3"))


def test_reasons_and_note_come_from_the_card_and_show_in_the_report(env):  # noqa: F811
    app, path, assessed = env
    title = "Software Engineer, AI Coding Tasks"
    _, _, opened = call(app, "GET", "/?d=UNVERIFIED")
    assert 'name="reason" value="too_senior"' in opened and 'name="note"' in opened
    status = _post_form(
        app,
        f"/jobs/{assessed}/fit",
        [
            ("verdict", "not_for_me"),
            ("reason", "too_senior"),
            ("reason", "language"),
            ("note", "needs fluent German"),
            ("next", "/"),
        ],
    )
    assert status == 303
    _, _, ineligible = call(app, "GET", "/?d=INELIGIBLE")
    assert title in ineligible and "You: Not for me" in ineligible
    assert "Too senior or too many years, Language" in ineligible
    assert "needs fluent German" in ineligible
    assert 'value="too_senior" checked' in ineligible  # the card remembers your reasons
    with Store(path) as opened_store:
        data = feedback.report(opened_store.db)
    assert data["by_reason"]["too_senior"] == {"total": 1, "by_tag": {"UNVERIFIED": 1}}
    assert "Why you said Not for me" in feedback.render_report(data)
    _, _, page = call(app, "GET", "/calibration")
    assert "Why you said Not for me" in page and "Language" in page
