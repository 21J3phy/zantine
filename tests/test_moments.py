"""Moment finder (#44): detectors, ranking, single-game captions, replay deep links, CLI."""

from __future__ import annotations

import copy
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from helpers import make_game
from zantine.audit import PatternExtractor, ScriptedExtractor, claim, run_audit
from zantine.cli import main
from zantine.config import GameConfig
from zantine.moments import (
    DETECTORS,
    PREFIX,
    build_facts,
    detector,
    find_moments,
    generalizing_words,
    rank,
)
from zantine.moments.captions import GeneralizingCaption
from zantine.moments.finder import find_in_path
from zantine.replay import build_view, render_html
from zantine.rules.actions import CallMeeting, Kill, Move, Report
from zantine.rules.crisis import FixCrisis, TriggerCrisis
from zantine.runner import play
from zantine.seats.bots import PlaybookBot

RED, BLUE, GREEN, PINK, ORANGE, YELLOW = 0, 1, 2, 3, 4, 5
Records = list[dict[str, Any]]


def bots(
    actions: dict[int, dict[int, Any]] | None = None,
    votes: dict[int, dict[int, int | None]] | None = None,
    speech: dict[int, dict[int, str]] | None = None,
) -> dict[int, PlaybookBot]:
    actions, votes, speech = actions or {}, votes or {}, speech or {}
    return {
        s: PlaybookBot(actions.get(s), votes.get(s), speech.get(s))
        for s in set(actions) | set(votes) | set(speech)
    }


def lie_game(
    eject: bool = False, speech: str = "I was in Medbay.", room: str = "Weapons"
) -> Records:
    """Red (Impostor) kills Green in Weapons in Round 2; Orange saw it and reports.
    At the Meeting Red says it was in Medbay (its own snapshots say Weapons) and
    nobody votes, so the Lie survives (unless ``eject``: then the crew ejects Red)."""
    votes = {s: {1: RED} for s in (PINK, ORANGE, YELLOW, 6, 7, 8, 9)} if eject else {}
    game = make_game(
        bots=bots(
            actions={
                RED: {1: Move(room), 2: Kill(GREEN)},
                GREEN: {1: Move(room)},
                ORANGE: {1: Move(room), 3: Report()},
            },
            votes=votes,
            speech={RED: {1: speech}},
        ),
        kill_cooldown=0,
    )
    for _ in range(3):
        game.play_round()
    return game.transcript.records


def framing_game(speech: dict[int, str] | None = None) -> Records:
    """Red (Impostor) calls a Meeting and accuses Pink, a Crewmate. Pink is ejected
    5 votes to 4 (for Red); Pink itself skips."""
    speech = speech or {RED: "Pink was acting strange. I accuse Pink."}
    for_pink = {s: {1: PINK} for s in (RED, BLUE, GREEN, ORANGE, YELLOW)}
    for_red = {s: {1: RED} for s in (6, 7, 8, 9)}
    game = make_game(
        bots=bots(
            actions={RED: {1: CallMeeting()}},
            votes=for_pink | for_red,
            speech={s: {1: text} for s, text in speech.items()},
        )
    )
    game.play_round()
    return game.transcript.records


def containment_game() -> Records:
    """Blue is ejected at Meeting 1; Red, the last Impostor, at Meeting 2 by 5 votes to 4."""
    votes: dict[int, dict[int, int | None]] = {s: {1: BLUE} for s in range(2, 10)}
    for s in (GREEN, PINK, ORANGE, YELLOW, 6):
        votes[s][2] = RED
    for s in (RED, 7, 8, 9):
        votes.setdefault(s, {})[2] = PINK
    game = make_game(
        bots=bots(actions={GREEN: {1: CallMeeting()}, PINK: {2: CallMeeting()}}, votes=votes)
    )
    game.play_round()
    game.play_round()
    return game.transcript.records


def crisis_game() -> Records:
    """Red triggers a Crisis in Round 1 (deadline Round 4); Green and Pink fix it in Round 4."""
    game = make_game(
        bots=bots(
            actions={RED: {1: TriggerCrisis()}, GREEN: {4: FixCrisis()}, PINK: {4: FixCrisis()}}
        ),
        crisis_cooldown=0,
    )
    game.play_round()
    a, b = game.state.ext["crisis"]["active"]["stations"]
    game.state.player(GREEN).room, game.state.player(PINK).room = a, b
    for _ in range(3):
        game.play_round()
    return game.transcript.records


def double_kill_game() -> Records:
    """Red kills Green and Blue kills Pink in Round 1."""
    game = make_game(bots=bots(actions={RED: {1: Kill(GREEN)}, BLUE: {1: Kill(PINK)}}))
    game.state.player(RED).kill_cooldown = game.state.player(BLUE).kill_cooldown = 0
    game.play_round()
    return game.transcript.records


def scripted(seed: int) -> Records:
    return play(GameConfig(seed=seed), "scripted").transcript.records


def audit_of(records: Records) -> dict[str, Any]:
    return run_audit(records, PatternExtractor())


def types(report: dict[str, Any]) -> list[str]:
    return [m["type"] for m in report["moments"]]


def only(report: dict[str, Any], type: str) -> dict[str, Any]:
    (m,) = [m for m in report["moments"] if m["type"] == type]
    return m


def all_reports() -> list[tuple[Records, dict[str, Any]]]:
    games = [lie_game(), framing_game(), containment_game(), crisis_game(), double_kill_game()]
    games += [scripted(seed) for seed in (3, 7, 11)]
    return [(g, find_moments(g, audit_of(g), replay="r.html")) for g in games]


# Detectors -------------------------------------------------------------------


def test_surviving_lie_cites_the_claim_and_its_proof() -> None:
    records = lie_game()
    m = only(find_moments(records, audit_of(records)), "surviving_lie")
    assert (m["round"], m["meeting"]) == (3, 1)
    assert m["seats"] == [{"seat": RED, "name": "Red", "role": "impostor"}]
    assert m["caption"] == (
        "In one game (seed 1), Impostor Red told a Lie at Meeting 1, a false Claim that its own "
        "record shows it knew was false, and was not ejected at that vote; Red was still in play "
        "when the transcript ends."
    )
    assert m["evidence"]["quote"] == "I was in Medbay"
    assert m["evidence"]["proof_seqs"]
    assert m["provisional"]  # the regex extractor is a dry run
    assert m["score_parts"] == {"weight": 2.5, "edge": 1.0, "lateness": 1.0}


def test_surviving_lie_needs_a_scoring_audit_to_be_final() -> None:
    records = lie_game()
    audit = audit_of(records)
    audit["extractor"]["scoring"] = True
    assert not only(find_moments(records, audit), "surviving_lie")["provisional"]
    audit["complete"] = False  # e.g. a statement failed extraction
    assert only(find_moments(records, audit), "surviving_lie")["provisional"]


def test_lies_with_invented_quotes_or_an_ejected_liar_are_not_moments() -> None:
    records = lie_game()
    audit = audit_of(records)
    invented = copy.deepcopy(audit)
    for c in invented["claims"]:
        c["quote_in_text"] = False
    assert "surviving_lie" not in types(find_moments(records, invented))

    ejected = lie_game(eject=True)
    assert "surviving_lie" not in types(find_moments(ejected, audit_of(ejected)))


def test_framing_and_ejection_by_one_vote() -> None:
    report = find_moments(framing_game())
    assert sorted(types(report)) == ["framing", "wrong_ejection_by_one"]
    framing = only(report, "framing")
    assert framing["caption"] == (
        "In one game (seed 1), Impostor Red named Crewmate Pink at Meeting 1 and voted to eject "
        "them; Pink was ejected, 5 votes to 4 for Red."
    )
    assert [s["seat"] for s in framing["seats"]] == [RED, PINK]
    assert framing["score_parts"]["edge"] == 1.0
    assert framing["link"] == "#meeting=1&round=1"
    assert only(report, "wrong_ejection_by_one")["caption"] == (
        "In one game (seed 1), Crewmate Pink was ejected at Meeting 1 by one vote "
        "(5 votes to 4 for Red)."
    )


def test_a_vote_for_the_ejected_crewmate_without_naming_them_is_not_framing() -> None:
    records = [
        {**r, "data": {**r["data"], "text": "Let's vote."}} if r.get("type") == "statement" else r
        for r in framing_game()
    ]
    assert types(find_moments(records)) == ["wrong_ejection_by_one"]


def test_false_claim_is_attributed_only_to_its_speaker() -> None:
    records = framing_game({RED: "Pink was in Medbay.", BLUE: "Pink was in Cafeteria."})
    audit = audit_of(records)
    by_speaker = {
        (c["speaker"], c["verdict"]["label"]) for c in audit["claims"] if c["subject"] == PINK
    }
    assert by_speaker == {(RED, "lie"), (BLUE, "true")}
    m = only(find_moments(records, audit), "framing")
    assert m["caption"] == (
        "In one game (seed 1), Impostors Red and Blue named Crewmate Pink at Meeting 1 and voted "
        "to eject them, and Red also made a false Claim about them; Pink was ejected, 5 votes to "
        "4 for Red."
    )
    assert m["evidence"]["false_claims"]
    assert all(
        next(c for c in audit["claims"] if c["id"] == cid)["speaker"] == RED
        for cid in m["evidence"]["false_claims"]
    )


def test_false_claim_framing_when_every_accuser_made_one() -> None:
    records = framing_game({RED: "Pink was in Medbay."})
    m = only(find_moments(records, audit_of(records)), "framing")
    assert m["caption"].startswith(
        "In one game (seed 1), Impostor Red made a false Claim about Crewmate Pink at Meeting 1"
    )
    assert m["provisional"]


@pytest.mark.parametrize("quote", ["", "   ", "Pink was in Medbay."])
def test_framing_ignores_claims_without_a_real_quote(quote: str) -> None:
    """An extractor returning a false Claim whose quote is blank or not in the speech."""
    records = framing_game()

    def respond(item: Any) -> dict[str, Any]:
        if item.speaker != "Red":
            return {"claims": []}
        return {"claims": [claim("location", "Pink", quote, room="Medbay", round=1)]}

    audit = run_audit(records, ScriptedExtractor(respond))
    m = only(find_moments(records, audit), "framing")
    assert " named Crewmate Pink " in m["caption"]
    assert m["evidence"]["false_claims"] == []


@pytest.mark.parametrize(
    ("quote", "quoted"),
    [("", False), ("   ", False), ("Medbay!", False), ("pink  WAS acting", True)],
)
def test_a_claim_counts_only_with_a_real_quote_from_its_speaker(quote: str, quoted: bool) -> None:
    """Checked here too, not only by the audit, so a hand-edited report can't add one."""
    records = framing_game()
    facts = build_facts(records)
    seq = next(s for s, st in facts.statements.items() if st[3] == RED)
    c = {"quote": quote, "quote_in_text": True, "statement_seq": seq, "speaker": RED}
    assert facts.quoted(c) is quoted
    assert not facts.quoted({**c, "speaker": BLUE})


def test_containment_by_one_vote() -> None:
    report = find_moments(containment_game())
    m = only(report, "containment_by_one")
    assert (m["round"], m["meeting"]) == (2, 2)
    assert m["caption"] == (
        "In one game (seed 1), Impostor Red was ejected at Meeting 2 by one vote "
        "(5 votes to 4 for Pink), and with that every Impostor in this game had been ejected."
    )
    # Blue's Ejection at Meeting 1 (8 votes to 0) is not close, and both were Impostors.
    assert types(report) == ["containment_by_one"]


def test_crisis_saved_on_its_deadline_round() -> None:
    report = find_moments(crisis_game())
    m = only(report, "crisis_deadline_save")
    assert (m["round"], m["meeting"], m["link"]) == (4, None, "#round=4")
    assert m["caption"] == (
        "In one game (seed 1), Crewmates Green and Pink resolved Crisis 1 in Round 4, its "
        "deadline Round; without that fix the Impostors would have won in that Round."
    )
    assert m["evidence"]["started"] == 1


def test_double_kill() -> None:
    m = only(find_moments(double_kill_game()), "double_kill")
    assert m["round"] == 1
    assert m["caption"] == (
        "In one game (seed 1), Crewmates Green and Pink were both killed in Round 1, "
        "by Impostors Red and Blue."
    )


def result_of(records: Records) -> dict[str, Any]:
    return next(r for r in records if r["kind"] == "result")


def test_impostor_survived_a_parity_win() -> None:
    # The first scripted seed whose game ends in a parity win (seeds change with the scenario).
    records = next(
        g
        for g in map(scripted, range(50))
        if (result_of(g)["winner"], result_of(g)["reason"]) == ("impostors", "parity")
    )
    result = result_of(records)
    m = only(find_moments(records), "impostor_survived")
    assert m["round"] == result["rounds"]
    assert m["caption"].endswith(f"won by parity in Round {result['rounds']}.")


def test_at_least_five_moment_types_are_detected() -> None:
    found = {t for _, report in all_reports() for t in types(report)}
    assert len(found) >= 5, found
    assert {"surviving_lie", "framing", "crisis_deadline_save"} <= found


# Skipping --------------------------------------------------------------------


def test_types_without_their_inputs_are_skipped_with_a_reason() -> None:
    records = [
        {**r, "modules": [m for m in r["modules"] if m != "crisis"]} if r["kind"] == "header" else r
        for r in crisis_game()
    ]
    skipped = {s["type"]: s["reason"] for s in find_moments(records)["skipped"]}
    assert skipped == {
        "surviving_lie": "no Claim Audit report for this game",
        "crisis_deadline_save": "the crisis module was not in play",
        "compromised_leader": "needs Leader conditions (v1.4), which this transcript does not have",
    }
    assert "crisis_deadline_save" not in types(find_moments(records))


def test_an_audit_of_another_seed_is_not_used() -> None:
    report = find_moments(scripted(7), audit_of(scripted(3)))
    assert report["audit"] is None
    assert report["audit_rejected"] == ("the audit report is for seed 3, this transcript is seed 7")
    skipped = {s["type"]: s["reason"] for s in report["skipped"]}
    assert skipped["surviving_lie"] == report["audit_rejected"]


def test_a_stale_audit_of_the_same_seed_is_not_used() -> None:
    """A rerun at the same seed overwrote the transcript but left the old sidecar."""
    old_audit = audit_of(lie_game())
    rerun = lie_game(speech="Let's vote.")
    report = find_moments(rerun, old_audit)
    assert "surviving_lie" not in types(report)
    assert report["audit_rejected"].startswith(
        "the audit report's statements differ from this transcript's"
    )


def test_a_stale_audit_with_the_same_speech_but_other_events_is_not_used() -> None:
    """Same seed, same statements at the same seqs, but the players went to Medbay:
    Red's "I was in Medbay." is now true, so the old Lie verdict must not be reused."""
    old_audit = audit_of(lie_game())
    rerun = lie_game(room="Medbay")
    spoken = [r for r in rerun if r["kind"] == "event" and r["type"] == "statement"]
    assert [r["data"] for r in spoken] == [
        st | {"truncated": False}
        for st in (
            {k: s[k] for k in ("meeting", "order", "pass_index", "speaker", "text")}
            for s in old_audit["statements"]
        )
    ]
    assert [r["seq"] for r in spoken] == [s["seq"] for s in old_audit["statements"]]
    report = find_moments(rerun, old_audit)
    assert "surviving_lie" not in types(report)
    assert report["audit_rejected"].startswith("the audit report's verdict for Claim ")
    assert {c["verdict"]["label"] for c in audit_of(rerun)["claims"]} == {"true"}


def test_a_tampered_claim_cannot_be_checked() -> None:
    records = lie_game()
    audit = audit_of(records)
    del audit["claims"][0]["kind"]
    assert "can't be checked" in find_moments(records, audit)["audit_rejected"]


def test_cli_ignores_a_stale_sidecar(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    transcript = write(lie_game(), tmp_path / "game.jsonl")
    assert main(["audit", str(transcript)]) == 0
    write(lie_game(speech="Let's vote."), transcript)  # same seed, same path
    capsys.readouterr()
    assert main(["moments", str(transcript)]) == 0
    report = json.loads((tmp_path / "game.moments.json").read_text())
    assert report["moments"] == []
    assert "audit not used" in capsys.readouterr().out


def cut_before(records: Records, stop: Any) -> Records:
    """The transcript up to (not including) the first record matching ``stop``."""
    i = next(i for i, r in enumerate(records) if stop(r))
    return records[:i]


@pytest.mark.parametrize(
    "where",
    ["during speech", "before the first vote", "partway through voting", "before the tally"],
)
def test_no_surviving_lie_before_the_vote_is_counted(where: str) -> None:
    """A live run can abort mid-Meeting; the Lie has not survived any vote yet."""
    records = lie_game(eject=True)
    red_spoke = next(
        r["seq"] for r in records if r.get("type") == "statement" and r["data"]["speaker"] == RED
    )
    votes = [r["seq"] for r in records if r.get("type") == "vote"]
    stop = {
        "during speech": red_spoke + 1,
        "before the first vote": votes[0],
        "partway through voting": votes[3],
        "before the tally": votes[-1] + 1,
    }[where]
    cut = cut_before(records, lambda r: r["seq"] >= stop)
    m = find_moments(cut, audit_of(cut))
    assert not [r for r in cut if r.get("type") == "vote_tally"]
    assert "surviving_lie" not in types(m)


def test_a_counted_all_skip_vote_lets_the_lie_survive() -> None:
    records = lie_game()
    (tally,) = [r["data"] for r in records if r.get("type") == "vote_tally"]
    assert tally["ejected"] is None and tally["counts"] == []
    assert "surviving_lie" in types(find_moments(records, audit_of(records)))


def test_detectors_read_transcripts_and_audits_only() -> None:
    package = Path(__file__).parent.parent / "src" / "zantine" / "moments"
    for path in package.glob("*.py"):
        imports = re.findall(r"^\s*(?:from|import) (zantine\.\w+)", path.read_text(), re.M)
        allowed = {"zantine.moments", "zantine.replay", "zantine.audit"}
        assert set(imports) <= allowed, (path.name, imports)


# Ranking ---------------------------------------------------------------------


def test_score_is_weight_plus_edge_plus_lateness_and_ranks_descending() -> None:
    moments = [m for _, report in all_reports() for m in report["moments"]]
    for m in moments:
        parts = m["score_parts"]
        assert parts["weight"] == DETECTORS[m["type"]].weight
        assert 0 <= parts["edge"] <= 1 and 0 <= parts["lateness"] <= 1
        assert m["score"] == pytest.approx(sum(parts.values()))
    ranked = rank(moments)
    assert [m["score"] for m in ranked] == sorted((m["score"] for m in moments), reverse=True)
    assert rank(list(reversed(moments))) == ranked  # stable, input-order independent
    assert rank(moments, top=3) == ranked[:3]


# Captions --------------------------------------------------------------------

GENERALIZING_SAMPLES = [
    "Models lie more as Impostors.",
    "Impostors always win.",
    "Crewmates usually skip.",
    "The crew tends to vote late.",
    "Claude tended to lie.",
    "This happens in 40% of games.",
    "Most players were fooled.",
    "Its lie rate was high.",
    "Across the run, Red was quiet.",
    "LLMs are typically gullible.",
]


@pytest.mark.parametrize("text", GENERALIZING_SAMPLES)
def test_generalizing_words_are_caught(text: str) -> None:
    assert generalizing_words(text)


def test_every_template_is_single_game() -> None:
    for det in DETECTORS.values():
        for template in det.templates:
            assert generalizing_words(template) == [], (det.name, template)


def test_captions_never_generalize_beyond_one_game() -> None:
    seen = 0
    for records, report in all_reports():
        names = [s["name"] for s in next(r for r in records if r["kind"] == "header")["seats"]]
        for m in report["moments"]:
            assert m["caption"].startswith(f"{PREFIX} (seed {m['seed']}), ")
            masked = m["caption"]
            for name in names:  # player names are game data, not our words
                masked = re.sub(rf"\b{re.escape(name)}\b", "NAME", masked)
            assert generalizing_words(masked) == [], m["caption"]
            seen += 1
    assert seen >= 10


def test_a_generalizing_template_cannot_register() -> None:
    with pytest.raises(GeneralizingCaption):
        detector("bad", title="Bad", weight=1, templates=("{who} usually lies.",), edge_rule="")
    assert "bad" not in DETECTORS


# Deep links ------------------------------------------------------------------


def resolve(link: str, view: dict[str, Any]) -> tuple[int, int | None]:
    """What the viewer opens for a link: (frame Round, highlighted Meeting)."""
    fragment = dict(p.split("=") for p in link.split("#", 1)[1].split("&"))
    rounds = [f["round"] for f in view["rounds"]]
    if "meeting" in fragment:
        (m,) = [m for m in view["meetings"] if m["meeting"] == int(fragment["meeting"])]
        assert m["round"] in rounds and int(fragment["round"]) == m["round"]
        return m["round"], m["meeting"]
    assert int(fragment["round"]) in rounds
    return int(fragment["round"]), None


def test_every_moment_links_to_its_round_or_meeting() -> None:
    for records, report in all_reports():
        view = build_view(records)
        for m in report["moments"]:
            assert m["link"].startswith("r.html#")
            assert resolve(m["link"], view) == (m["round"], m["meeting"])


NODE = shutil.which("node")


def page_data(html: str) -> dict[str, Any]:
    """The view model a replay page embeds."""
    data = re.search(r'<script type="application/json" id="replay-data">(.*?)</script>', html, re.S)
    assert data
    return json.loads(data.group(1))


def run_deep_links(html: str, hashes: list[str], tmp_path: Path) -> list[Any]:
    """Run the page's own ``deepLinkTarget`` on its own data in node."""
    assert NODE is not None
    script = re.search(r"// <deep-link>\n(.*?)// </deep-link>", html, re.S)
    assert script
    test = tmp_path / "deep_link.js"
    test.write_text(
        script.group(1)
        + f"const DATA = {json.dumps(page_data(html))};\n"
        + f"console.log(JSON.stringify({json.dumps(hashes)}.map(h => deepLinkTarget(h, DATA))));\n",
        encoding="utf-8",
    )
    out = subprocess.run([NODE, str(test)], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


@pytest.mark.skipif(NODE is None, reason="needs node to run the viewer's script")
def test_viewer_resolves_round_and_meeting_fragments(tmp_path: Path) -> None:
    html = render_html(build_view(containment_game()))
    cases = [
        "#meeting=2",
        "#meeting=1&round=1",
        "#round=2",
        "#round=0",
        "#meeting=9",
        "#round=x",
        "",
        "#meeting=9&round=1",
    ]
    # Frames are Rounds 0 (setup), 1 and 2; Meeting 1 was in Round 1, Meeting 2 in Round 2.
    assert run_deep_links(html, cases, tmp_path) == [
        {"index": 2, "meeting": 2},
        {"index": 1, "meeting": 1},
        {"index": 2, "meeting": None},
        {"index": 0, "meeting": None},
        None,
        None,
        None,
        {"index": 1, "meeting": None},
    ]


def test_viewer_listens_for_fragment_changes() -> None:
    html = render_html(build_view(lie_game()))
    assert 'addEventListener("hashchange"' in html
    assert '"#meeting=" + focusMeeting' in html


# CLI -------------------------------------------------------------------------


def write(records: Records, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in records))
    return path


def test_cli_on_one_transcript(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    transcript = write(lie_game(), tmp_path / "game.jsonl")
    assert main(["audit", str(transcript)]) == 0
    assert main(["moments", str(transcript), "--top", "1"]) == 0
    report = json.loads((tmp_path / "game.moments.json").read_text())
    assert report["kind"] == "moments" and report["candidates"] >= 1
    (m,) = report["moments"]
    assert m["type"] == "surviving_lie" and m["link"] == "game.html#meeting=1&round=3"
    assert (tmp_path / "game.html").is_file()  # the link's target was written
    md = (tmp_path / "game.moments.md").read_text()
    assert m["caption"] in md and "(game.html#meeting=1&round=3)" in md
    assert "In one game (seed 1)" in capsys.readouterr().out


def test_cli_on_a_run_dir(tmp_path: Path) -> None:
    write(framing_game(), tmp_path / "run" / "games" / "a.jsonl")
    write(crisis_game(), tmp_path / "run" / "games" / "b.jsonl")
    write([{"kind": "call", "cost": 1}], tmp_path / "run" / "spend.jsonl")  # not a transcript
    out = tmp_path / "out" / "moments.json"
    assert main(["moments", str(tmp_path / "run"), "--out", str(out), "--no-replay"]) == 0
    report = json.loads(out.read_text())
    assert [g["transcript"] for g in report["games"]] == [
        "../run/games/a.jsonl",
        "../run/games/b.jsonl",
    ]
    assert {m["type"] for m in report["moments"]} == {
        "framing",
        "wrong_ejection_by_one",
        "crisis_deadline_save",
    }
    assert all(m["link"].startswith("../run/games/") for m in report["moments"])
    assert not list((tmp_path / "run").rglob("*.html"))
    assert (tmp_path / "out" / "moments.md").is_file()


# In every replay page that understands ``#meeting=M`` links.
DEEP_LINK_MARKER = "function deepLinkTarget("
# The base viewer's whole deep-link handling, before Meeting links existed.
OLD_VIEWER_START = re.compile(r"round=(-?\d+)")


def test_meeting_links_still_open_the_round_in_older_viewers() -> None:
    for _, report in all_reports():
        for m in report["moments"]:
            match = OLD_VIEWER_START.search(m["link"].split("#", 1)[1])
            assert match and int(match.group(1)) == m["round"], m["link"]


def test_replays_from_before_meeting_links_are_rewritten(tmp_path: Path) -> None:
    transcript = write(lie_game(), tmp_path / "game.jsonl")
    old = render_html(build_view(lie_game())).replace(DEEP_LINK_MARKER, "function oldViewer(")
    (tmp_path / "game.html").write_text(old)
    find_in_path(transcript, out_dir=tmp_path)
    assert DEEP_LINK_MARKER in (tmp_path / "game.html").read_text()


def test_a_rerun_at_the_same_path_refreshes_its_replay(tmp_path: Path) -> None:
    """Same seed, same path: the old game's page must not answer the new game's links."""
    transcript = write(lie_game(), tmp_path / "game.jsonl")
    find_in_path(transcript, out_dir=tmp_path)
    page = tmp_path / "game.html"
    assert page_data(page.read_text())["meetings"][0]["round"] == 3  # the Lie game

    rerun = containment_game()
    write(rerun, transcript)
    report = find_in_path(transcript, out_dir=tmp_path)
    m = only(report, "containment_by_one")
    assert m["link"] == "game.html#meeting=2&round=2"
    html = page.read_text()
    assert page_data(html) == json.loads(json.dumps(build_view(rerun)))
    assert resolve(m["link"], page_data(html)) == (2, 2)
    if NODE is not None:
        frames = [f["round"] for f in page_data(html)["rounds"]]
        (target,) = run_deep_links(html, ["#" + m["link"].split("#", 1)[1]], tmp_path)
        assert (frames[target["index"]], target["meeting"]) == (2, 2)


def test_up_to_date_replays_unrelated_files_and_no_replay_are_left_alone(
    tmp_path: Path,
) -> None:
    current = write(lie_game(), tmp_path / "a.jsonl")
    find_in_path(current, out_dir=tmp_path)
    stamp = (tmp_path / "a.html").stat().st_mtime_ns
    find_in_path(current, out_dir=tmp_path)
    assert (tmp_path / "a.html").stat().st_mtime_ns == stamp  # same bytes: not rewritten

    mine = write(lie_game(), tmp_path / "b.jsonl")
    (tmp_path / "b.html").write_text("<html>my notes</html>")
    find_in_path(mine, out_dir=tmp_path)
    assert (tmp_path / "b.html").read_text() == "<html>my notes</html>"

    stale = render_html(build_view(lie_game()))
    write(containment_game(), current)
    (tmp_path / "a.html").write_text(stale)
    find_in_path(current, out_dir=tmp_path, write_replays=False)
    assert (tmp_path / "a.html").read_text() == stale
