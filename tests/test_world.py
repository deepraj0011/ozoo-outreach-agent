"""Task 19: the realistic lead simulation runs the real agent and reaches the right outcome for
each kind of lead (a small subset here; `python -m outreach.world` runs them all)."""
from __future__ import annotations

from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from outreach.world import __main__ as world_cli
from outreach.world.engine import World, pick_personas
from outreach.world.personas import BY_KEY, MIX, STORY
from outreach.world.report import collect, render, summary

KEYS = ("defender_ghost", "smart_sandbox_ghost", "skimmer", "late_replier", "unsubscriber", "ooo")


@pytest.fixture(scope="module")
def results(tmp_path_factory):
    world = World(tmp_path_factory.mktemp("world"), [BY_KEY[k] for k in KEYS]).run()
    try:
        yield world, collect(world)
    finally:
        world.engine.dispose()


def test_every_lead_reaches_the_expected_outcome(results):
    _, res = results
    assert {r.sim.persona.key: r.outcome for r in res} == {k: BY_KEY[k].expect for k in KEYS}


def test_scanners_never_make_a_sales_alert(results):
    _, res = results
    for r in res:
        if not r.sim.persona.interested:
            assert "hot_lead" not in r.alerts, r.sim.persona.key


def test_defender_clicks_are_all_bot_and_score_nothing(results):
    _, res = results
    r = next(r for r in res if r.sim.persona.key == "defender_ghost")
    assert r.sim.machine_clicks > 0 and set(r.clicks) == {"bot"} and r.score == 0


def test_a_scanner_click_never_emails_the_lead(results):
    world, res = results
    r = next(r for r in res if r.sim.persona.key == "smart_sandbox_ghost")
    to_lead = [m for m in world.outbox.messages if m["To"] == r.sim.email]
    assert len(to_lead) == r.emails_sent == 4  # the sequence only: no booking-slots email


def test_skimmer_is_a_verified_hot_lead(results):
    _, res = results
    r = next(r for r in res if r.sim.persona.key == "skimmer")
    assert r.visits == 2 and "hot_lead" in r.alerts


def test_sequence_emails_go_out_in_india_working_hours(results):
    """Answers to a person who just replied go at once; the scheduled sequence emails wait for
    working hours, and the follow-ups do not all go at the same time of day."""
    world, _ = results
    assert world.sends
    hours = set()
    for s in world.sends:
        local = s["sent_at"].astimezone(ZoneInfo("Asia/Kolkata"))
        assert local.weekday() < 5 and (9, 30) <= (local.hour, local.minute) < (17, 30), local
        hours.add(local.hour)
    assert len(hours) >= 2


def test_summary_and_report(results):
    _, res = results
    s = summary(res)
    assert s["outcome_as_expected"] == len(KEYS) and s["sales_alerts_hot_wrong"] == 0
    text = render(res)
    assert "## Summary" in text and "## Timelines" in text and "❌" not in text


def test_pick_personas_is_reproducible_and_follows_the_mix():
    a, b = pick_personas(60, seed=3), pick_personas(60, seed=3)
    assert [p.key for p in a] == [p.key for p in b] and [p.acts for p in a] == [p.acts for p in b]
    assert {p.key for p in a} <= {k for k, _ in MIX}
    assert pick_personas(None, seed=1) == list(STORY)


def test_cli_writes_the_report(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(world_cli, "simulate",  # one quiet lead: the CLI plumbing, not the world
                        lambda workdir, n, seed, days: World(Path(workdir), [BY_KEY["gmail_ghost"]]).run(days))
    out = tmp_path / "report.md"
    assert world_cli.main(["--days", "20", "--out", str(out)]) == 0
    assert "Realistic lead run" in out.read_text() and "Realistic lead run" in capsys.readouterr().out


def test_cli_rejects_a_silly_lead_count():
    with pytest.raises(SystemExit):
        world_cli.main(["--leads", "0"])


def test_cli_rejects_a_silly_day_count():
    with pytest.raises(SystemExit):
        world_cli.main(["--days", "5000"])
