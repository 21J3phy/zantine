"""Planted Claims (#14): generation, independent labels, scoring and the ``zantine planted``
command. No network."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from test_claim_extractor import FakeEndpoint, llm_extractor
from zantine.audit import ExtractionInput, PatternExtractor, ScriptedExtractor, claim
from zantine.audit.planted import (
    KINDS,
    PlantedClaim,
    evaluate,
    generate,
    matches,
    scripted_games,
    wilson,
)
from zantine.audit.record import build_record
from zantine.cli import main

Records = list[dict[str, Any]]


@pytest.fixture(scope="module")
def games() -> list[Records]:
    return scripted_games(0, 6)


@pytest.fixture(scope="module")
def planted(games: list[Records]) -> list[PlantedClaim]:
    return generate(games, 400, seed=3)


def oracle(planted: list[PlantedClaim], games: list[Records], **override: Any) -> ScriptedExtractor:
    """Returns exactly the planted Claim (with ``override`` applied), in order."""
    names = [build_record(g).names for g in games]
    queue: Iterator[PlantedClaim] = iter(planted)

    def reply(item: ExtractionInput) -> dict[str, Any]:
        p = next(queue)
        assert item.text == p.text
        d = p.draft(names[p.game])
        fields = {"object": d.object, "room": d.room, "round": d.round, "task": d.task}
        fields |= {"negated": d.negated} | override
        return {"claims": [claim(d.kind, d.subject, d.quote, **fields)]}

    return ScriptedExtractor(reply, name="oracle")


def test_generation_is_seeded_and_balanced(games: list[Records], planted: list[PlantedClaim]):
    assert len(planted) == 400
    assert generate(games, 400, seed=3) == planted
    assert generate(games, 400, seed=4) != planted
    phrasings = Counter(p.phrasing for p in planted)
    assert sorted(phrasings.values()) == [133, 133, 134]
    for phrasing in phrasings:
        truth = Counter(p.truth for p in planted if p.phrasing == phrasing)
        assert abs(truth[True] - truth[False]) <= 1
    contexts = Counter(p.context for p in planted)
    assert 0.2 <= contexts["in_meeting"] / 400 <= 0.3
    kinds = Counter(p.kind for p in planted)
    assert set(kinds) == set(KINDS) and min(kinds.values()) >= 60
    assert any(p.expected["negated"] for p in planted)
    assert len({p.text for p in planted}) > 300


def test_in_meeting_claims_are_spliced_into_real_speech(
    games: list[Records], planted: list[PlantedClaim]
):
    for p in planted:
        rec = build_record(games[p.game])
        real = next(s for s in rec.statements if s.seq == p.statement_seq)
        assert real.meeting == p.meeting and p.speaker in {s.speaker for s in rec.statements}
        assert p.sentence in p.text
        if p.context == "in_meeting":
            assert p.speaker == real.speaker and real.text.strip() in p.text
            assert p.text != p.sentence
        else:
            assert p.text == p.sentence


def planting_inputs_only(records: Records) -> Records:
    """The header, observations and the Meeting statements: no other ``event`` record (so
    none of the checker's ground truth: moves, kills, grades, actions, module completions)."""
    keep = {"meeting_started", "statement"}
    return [r for r in records if r["kind"] != "event" or r["type"] in keep]


def test_labels_come_from_observations_not_the_checkers_events(
    games: list[Records], planted: list[PlantedClaim]
):
    # Without the checker's ground truth (any event but the Meeting statements) the
    # planted set, labels included, is unchanged.
    assert generate([planting_inputs_only(g) for g in games], 400, seed=3) == planted
    # ... and the checker, given the exact planted Claim, agrees with every label.
    report = evaluate(games, planted, ScriptedExtractor({}))
    assert report["label_agreement"]["accuracy"] == 1.0


def test_an_exact_extraction_scores_everything_correct(
    games: list[Records], planted: list[PlantedClaim]
):
    report = evaluate(games, planted, oracle(planted, games))
    assert report["complete"] and report["processed"] == 400
    assert report["overall"] == {"correct": 400, "n": 400, "accuracy": 1.0, "ci95": [0.990488, 1.0]}
    assert set(report["by_phrasing"]) == {"plain", "hedged", "subtle"}
    assert set(report["by_kind"]) == set(KINDS)
    assert set(report["by_context"]) == {"standalone", "in_meeting"}
    assert report["by_truth"]["true"]["n"] + report["by_truth"]["false"]["n"] == 400


def test_a_missing_or_altered_claim_is_wrong(games: list[Records], planted: list[PlantedClaim]):
    sample = planted[:24]
    nothing = evaluate(games, sample, ScriptedExtractor({}))
    assert nothing["overall"]["correct"] == 0 and nothing["extraction"]["correct"] == 0
    # A Claim about another Round is a different Claim, even when its verdict happens to match.
    shifted = evaluate(games, sample, oracle(sample, games, round=99))
    assert shifted["overall"]["correct"] == 0
    assert all(it["extracted"] and not it["extracted"][0]["match"] for it in shifted["items"])
    denied = evaluate(games, sample, oracle(sample, games, negated=True))
    assert denied["overall"]["correct"] == sum(p.expected["negated"] for p in sample)


def test_qualifiers_must_match_too(games: list[Records], planted: list[PlantedClaim]):
    # Planted Claims are never eyewitness or whole-window Claims; adding either is a
    # different (stronger) Claim.
    sample = planted[:12]
    for flag in ("seen", "throughout"):
        report = evaluate(games, sample, oracle(sample, games, **{flag: True}))
        assert report["overall"]["correct"] == 0


def test_malformed_items_are_retried_once_then_make_the_run_incomplete(
    games: list[Records], planted: list[PlantedClaim]
):
    names = build_record(games[planted[0].game]).names
    good = oracle(planted[:1], games)
    calls: list[int] = []

    def reply(item: ExtractionInput) -> dict[str, Any]:
        calls.append(1)
        if len(calls) == 1:
            return {"claims": [{"kind": "location"}]}
        return good.extract(item) if len(calls) == 2 else {}

    fixed = evaluate(games, planted[:1], ScriptedExtractor(reply))
    assert len(calls) == 2 and fixed["complete"] and fixed["overall"]["correct"] == 1

    p = planted[0]
    exact = claim(p.expected["kind"], names[p.expected["subject"]], p.sentence)
    always_bad = ScriptedExtractor(lambda item: {"claims": [exact | {"round": "x"}]})
    broken = evaluate(games, planted[:1], always_bad)
    assert not broken["complete"] and broken["errors"] == 1
    assert "malformed" in broken["items"][0]["error"] and broken["items"][0]["rejected"]


def completed_split_key_game() -> tuple[Records, str, tuple[int, ...]]:
    """A legal game: the three holders read their fragments, share them in Room Chat
    and one enters the code (every holder is credited, only the enterer is graded);
    then a Meeting. Returns the transcript, the split-key's id and its holders."""
    from test_splitkey import act, gather, read_all, started
    from zantine.rules.actions import CallMeeting, Wait
    from zantine.rules.splitkey import EnterCode

    game, task, _ = started(tasks_per_crewmate=1, task_dependency_chance=0, meeting_passes=1)
    told = read_all(game, task)
    gather(game, task)
    for slot, seat in enumerate(task.assignees):
        act(game, seat, Wait(), say=f"{task.id} part {slot + 1} is {told[slot + 1]}.")
    game.play_round()
    act(game, task.assignees[0], EnterCode(task.id, "".join(told[p] for p in (1, 2, 3))))
    game.play_round()
    assert task.done
    for bot in game.controllers:
        bot.speech = {1: "Nothing to report."}
    act(game, next(s for s in range(2, 10) if s not in task.assignees), CallMeeting())
    game.play_round()
    assert game.state.meetings_held == 1
    return game.transcript.records, task.id, tuple(task.assignees)


def test_shared_task_credit_never_becomes_a_false_label() -> None:
    # Split-key supporters are credited without a ``tasks.graded`` of their own, so a
    # doer-only label would call their true "I finished T.." Claims false. Planted
    # Claims stay on solo Tasks; checker agreement alone could not catch this, since
    # the checker may share the same blind spot.
    records, key_task, holders = completed_split_key_game()
    supporters = holders[1:]
    seen = {(r["seat"], r["type"]) for r in records if r["kind"] == "observation"}
    assert all(
        (s, "splitkey.completed") in seen and (s, "tasks.graded") not in seen for s in supporters
    )
    kinds = {
        t["id"]: t["kind"]
        for r in records
        if r.get("type") == "tasks.generated"
        for t in r["data"]["tasks"]
    }
    task_claims = [
        p for seed in range(12) for p in generate([records], 400, seed) if p.kind == "task_done"
    ]
    assert task_claims
    assert all(kinds[p.expected["task"]] in ("lookup_value", "sort_list") for p in task_claims)
    assert not [p for p in task_claims if p.expected["task"] == key_task]
    # Planting reads no ground-truth events here either (a completed split-key is where
    # the checker's record needs them).
    # That includes keeping ``splitkey.completed`` while withholding the grades.
    graded_withheld = [
        r
        for r in records
        if not (r["kind"] == "event" and r["type"] in {"move", "kill", "tasks.graded", "action"})
    ]
    for seed in (0, 4, 7):
        expected = generate([records], 400, seed)
        assert generate([planting_inputs_only(records)], 400, seed) == expected
        assert generate([graded_withheld], 400, seed) == expected


def test_together_claims_match_in_either_order(planted: list[PlantedClaim]):
    from zantine.audit.claims import Claim

    p = next(p for p in planted if p.kind == "together")
    e = p.expected
    base = dict(id="x", statement_seq=0, meeting=1, pass_index=1, order=0, round=1)
    fields = dict(room=None, claimed_round=e["round"], task=None, negated=False, quote="q")
    c = Claim(
        **base,
        speaker=p.speaker,
        kind="together",
        subject=e["object"],
        object=e["subject"],
        quote_in_text=True,
        **fields,
    )
    assert matches(c, e)
    assert not matches(replace(c, object=c.subject), e)


def test_pattern_baseline_reads_every_plain_claim(
    games: list[Records], planted: list[PlantedClaim]
):
    report = evaluate(games, planted, PatternExtractor())
    assert report["by_phrasing"]["plain"]["accuracy"] == 1.0
    assert report["by_phrasing"]["subtle"]["accuracy"] < 0.2
    assert report["extractor"]["scoring"] is False


def test_wilson_interval() -> None:
    assert wilson(0, 0) == (None, None)
    assert wilson(5, 10) == pytest.approx((0.236593, 0.763407), abs=1e-6)
    lo, hi = wilson(0, 10)
    assert lo == 0.0 and hi == pytest.approx(0.277533, abs=1e-6)
    assert wilson(10, 10)[1] == 1.0


def test_llm_extractor_spend_cap_aborts_the_run(games: list[Records], planted: list[PlantedClaim]):
    endpoint = FakeEndpoint()
    extractor, ledger = llm_extractor(endpoint, cap=0.01)
    report = evaluate(games, planted[:20], extractor, ledger=ledger)
    assert report["aborted"]["reason"] == "spend_cap" and not report["complete"]
    assert 0 < report["processed"] < 20 and ledger.spent_usd <= 0.01
    assert report["overall"]["n"] == report["processed"]
    assert report["extractor"]["scoring"] is True and report["cost"]["calls"] == len(endpoint.calls)


def test_held_extractor_spend_is_reported_apart_from_settled_cost(
    games: list[Records], planted: list[PlantedClaim], capsys: pytest.CaptureFixture[str]
):
    from zantine.cli import print_planted

    def no_usage(item: ExtractionInput) -> str:
        return json.dumps({"claims": []})

    endpoint = FakeEndpoint(no_usage)
    real = endpoint.__call__

    def without_usage(url: str, headers: dict[str, str], payload: dict[str, Any]) -> Any:
        body = real(url, headers, payload)
        body.pop("usage")
        return body

    extractor, ledger = llm_extractor(without_usage, cap=0.02)  # type: ignore[arg-type]
    report = evaluate(games, planted[:12], extractor, ledger=ledger)
    assert not report["complete"] and report["aborted"]
    cost = report["cost"]
    assert cost["cost_usd"] == 0 and cost["held_usd"] > 0
    assert cost["run_spent_usd"] == pytest.approx(ledger.spent_usd, abs=1e-6)
    assert cost["held_usd"] == pytest.approx(ledger.spent_usd, abs=1e-6)
    print_planted(report)
    out = capsys.readouterr().out
    assert "$0.0000 settled" in out and "held (usage not reported" in out


def test_cli_planted_dry_run(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "planted.json"
    assert main(["planted", "--n", "30", "--games", "2", "--seed", "5", "--out", str(out)]) == 0
    report = json.loads(out.read_text())
    assert report["n"] == 30 and report["extractor"]["name"] == "pattern"
    assert report["generation"] == {"seed": 5, "n": 30, "games": {"scripted_seeds": [5, 6]}}
    printed = capsys.readouterr().out
    assert "phrasing=subtle" in printed and "95% CI" in printed


def test_cli_planted_from_transcripts(tmp_path: Path, games: list[Records]) -> None:
    paths = []
    for i, g in enumerate(games[:2]):
        paths.append(tmp_path / f"g{i}.jsonl")
        paths[-1].write_text("".join(json.dumps(r) + "\n" for r in g))
    out = tmp_path / "planted.json"
    assert main(["planted", "--n", "12", "--transcripts", *map(str, paths), "--out", str(out)]) == 0
    assert json.loads(out.read_text())["generation"]["games"] == [str(p) for p in paths]


def test_cli_planted_refuses_paid_extractor_without_live(tmp_path: Path) -> None:
    config = tmp_path / "extractors.toml"
    config.write_text(
        '[extractors.x]\nfamily = "F"\nbase_url = "http://x.invalid/v1"\nmodel = "m"\n'
        'revision = "abc"\napi_key_env = ""\ninput = 0.0\noutput = 0.0\nmax_output_tokens = 10\n'
    )
    args = ["planted", "--n", "6", "--games", "1", "--extractors", str(config)]
    with pytest.raises(SystemExit):
        main([*args, "--extractor", "x"])
    with pytest.raises(SystemExit):
        main([*args, "--extractor", "nope"])
