"""Solo task calibration: the Solo Harness, exact bounds, resume, spend cap, the CLI."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from fake_provider import FakeClient, fake_factory, message, text, tool_use
from fake_providers import (
    GoogleFake,
    OpenAIFake,
    calls_made,
    google_reply,
    google_tail,
    openai_reply,
    openai_tail,
    xai_reply,
)
from zantine import calibration as cal
from zantine.cli import main
from zantine.harness.anthropic_adapter import AnthropicAdapter
from zantine.harness.google_adapter import GoogleAdapter
from zantine.harness.ledger import Ledger
from zantine.harness.live import LiveDisabled
from zantine.harness.meta_adapter import MetaAdapter
from zantine.harness.neutral import action_tool
from zantine.harness.openai_adapter import OpenAIAdapter
from zantine.harness.pricing import ModelSpec, load_models
from zantine.harness.provider import (
    ProviderAdapter,
    ProviderError,
    ProviderResponse,
    ToolCall,
    Usage,
)
from zantine.harness.solo import SOLO_SYSTEM_PROMPT, parse_answer, solo_request, submit_tool
from zantine.harness.xai_adapter import XAIAdapter
from zantine.rules.tasks import WorkOnTask
from zantine.tasks import run_solo_trial, task_kind, task_kind_names

MODEL = "claude-fable-5-1"
KINDS = ["sort_list", "lookup_value"]


def brief_in(tail: str) -> dict[str, Any]:
    assert tail.startswith("Your Task:\n")
    return json.loads(tail.split("\n\n")[0].removeprefix("Your Task:\n"))


def brief_of(params: dict[str, Any]) -> dict[str, Any]:
    return brief_in(params["messages"][0]["content"][-1]["text"])


def solves(params: dict[str, Any]) -> Any:
    """A Model that always submits the right answer."""
    brief = brief_of(params)
    return message(tool_use("submit_answer", answer=task_kind(brief["kind"]).solve(brief)))


def run(
    tmp_path: Path,
    respond: Any = solves,
    *,
    cap: float = 100.0,
    trials: int = 5,
    kinds: list[str] | None = None,
    models: list[str] | None = None,
    seed: int = 3,
) -> tuple[cal.CalibrationRun, FakeClient]:
    client = FakeClient(respond)
    models = models or [MODEL]
    ledger = Ledger(cap)
    solvers = cal.model_solvers(models, ledger, adapter_factory=fake_factory(client))
    result = cal.calibrate(
        models,
        kinds or KINDS,
        out=tmp_path,
        solvers=solvers,
        trials=trials,
        seed=seed,
        ledger=ledger,
    )
    return result, client


def cell(summary: dict[str, Any], kind: str, model: str = MODEL) -> dict[str, Any]:
    return next(c for c in summary["cells"] if c["kind"] == kind and c["model"] == model)


# Exact bounds -----------------------------------------------------------------------


def test_zero_failures_in_sixty_bounds_the_failure_rate_under_five_percent() -> None:
    b = cal.failure_bounds(0, 60)
    assert b["upper_95_one_sided"] == pytest.approx(1 - 0.05 ** (1 / 60), abs=1e-6)
    assert b["upper_95_one_sided"] == pytest.approx(0.048703, abs=1e-6)
    assert b["upper_95_one_sided"] < 0.05
    assert b["ci_95_two_sided"] == [0.0, pytest.approx(0.059629, abs=1e-6)]
    assert b["rule_of_three"] == 0.05


@pytest.mark.parametrize(("k", "n"), [(1, 60), (3, 60), (7, 20), (59, 60)])
def test_clopper_pearson_bounds_solve_the_binomial_tail(k: int, n: int) -> None:
    u = cal.cp_upper(k, n, 0.05)
    assert cal.binom_cdf(k, n, u) == pytest.approx(0.05, abs=1e-9)
    lo, hi = cal.cp_lower(k, n, 0.025), cal.cp_upper(k, n, 0.025)
    assert 1 - cal.binom_cdf(k - 1, n, lo) == pytest.approx(0.025, abs=1e-9)
    assert lo < k / n < u < hi


def test_known_clopper_pearson_values() -> None:
    b = cal.failure_bounds(1, 60)
    assert b["upper_95_one_sided"] == pytest.approx(0.07664, abs=1e-5)
    assert b["ci_95_two_sided"] == [
        pytest.approx(0.000422, abs=1e-6),
        pytest.approx(0.089399, abs=1e-6),
    ]
    assert b["rule_of_three"] is None
    assert cal.failure_bounds(60, 60)["upper_95_one_sided"] == 1.0
    assert cal.failure_bounds(0, 0)["upper_95_one_sided"] is None


# Seeds and variants -----------------------------------------------------------------


def test_trial_seeds_are_fresh_per_kind_and_index_and_paired_across_models() -> None:
    seeds = {cal.trial_seed(7, kind, i) for kind in KINDS for i in range(60)}
    assert len(seeds) == 120
    assert cal.trial_seed(7, "sort_list", 4) == cal.trial_seed(7, "sort_list", 4)
    assert cal.trial_seed(7, "sort_list", 4) != cal.trial_seed(8, "sort_list", 4)
    briefs = {
        json.dumps(cal.variant("sort_list", cal.trial_seed(7, "sort_list", i))[1])
        for i in range(60)
    }
    assert len(briefs) == 60


@pytest.mark.parametrize("kind", KINDS)
def test_variants_match_run_solo_trial(kind: str) -> None:
    seed = cal.trial_seed(1, kind, 0)
    content, brief = cal.variant(kind, seed)
    trial = run_solo_trial(kind, seed, task_kind(kind).solve)
    assert brief == trial.brief and content.answer == trial.expected and trial.correct


# The Solo Harness -------------------------------------------------------------------


def test_solo_request_shows_the_brief_and_one_tool_with_the_in_game_answer_schema() -> None:
    _, brief = cal.variant("sort_list", 11)
    req = solo_request(brief)
    assert req.system == SOLO_SYSTEM_PROMPT and req.memory == ()
    assert [t.name for t in req.tools] == ["submit_answer"]
    in_game = action_tool(WorkOnTask).parameters["properties"]["answer"]
    assert req.tools[0].parameters["properties"]["answer"] == in_game
    assert json.dumps(brief, sort_keys=True) in req.tail
    assert "answer" not in brief
    assert "rejected: no tool call" in solo_request(brief, retry="no tool call").tail
    assert submit_tool() == req.tools[0]


def _response(*calls: ToolCall, stop: str = "tool_use") -> ProviderResponse:
    return ProviderResponse(tuple(calls), "", stop, Usage())


@pytest.mark.parametrize(
    ("response", "reason"),
    [
        (_response(stop="refusal"), "refused"),
        (_response(stop="max_tokens"), "ran out of output tokens"),
        (_response(stop="end_turn"), "no tool call"),
        (_response(ToolCall("submit_answer", {"answer": 1}), ToolCall("x", {})), "more than one"),
        (_response(ToolCall("speak", {"text": "hi"})), "not a tool here"),
        (_response(ToolCall("submit_answer", {})), "needs an 'answer'"),
        (_response(ToolCall("submit_answer", {"answer": True})), "text, a number"),
        (_response(ToolCall("submit_answer", {"answer": {"a": 1}})), "text, a number"),
    ],
)
def test_unusable_replies_are_rejected_with_a_reason(
    response: ProviderResponse, reason: str
) -> None:
    answer, error = parse_answer(response)
    assert answer is None and error is not None and reason in error


def test_usable_answers_parse() -> None:
    assert parse_answer(_response(ToolCall("submit_answer", {"answer": [3, "4"]}))) == (
        [3, "4"],
        None,
    )


# Running calibration ----------------------------------------------------------------


def test_dry_run_reference_solvers_keep_every_solo_kind(tmp_path: Path) -> None:
    result = cal.calibrate(
        [MODEL, "claude-opus-5-5"],
        ["chain_relay", *KINDS],
        out=tmp_path,
        solvers=cal.reference_solvers(),
        trials=60,
    )
    s = result.summary
    assert s["kinds"]["sort_list"]["decision"] == "keep"
    assert s["kinds"]["lookup_value"]["decision"] == "keep"
    relay = s["kinds"]["chain_relay"]
    assert relay["decision"] == "not_solo_calibratable" and "no solo form" in relay["reason"]
    assert all(c["verdict"] == "pass" and c["n"] == 60 for c in s["cells"])
    assert s["total_cost_usd"] == 0
    assert len(cal.read_trials(tmp_path / cal.TRIALS_FILE)) == 240
    assert "| sort_list | keep |" in (tmp_path / cal.SUMMARY_MD).read_text()
    assert json.loads((tmp_path / cal.SUMMARY_JSON).read_text()) == s


def test_correct_wrong_and_malformed_answers_are_counted(tmp_path: Path) -> None:
    def respond(params: dict[str, Any]) -> Any:
        brief = brief_of(params)
        answer = task_kind(brief["kind"]).solve(brief)
        if brief["kind"] == "lookup_value":
            return message(tool_use("submit_answer", answer=answer))
        n = respond.sorts = getattr(respond, "sorts", 0) + 1
        if n <= 2:  # trial 0: unusable twice (no tool call), so it fails after its retry
            return message(text("The answer is obvious."), stop_reason="end_turn")
        if n == 3:  # trial 1: a wrong answer
            return message(tool_use("submit_answer", answer=list(reversed(answer))))
        if n == 4:  # trial 2: unusable once, then right on the retry
            return message(tool_use("submit_answer", answer=True))
        return message(tool_use("submit_answer", answer=answer))

    result, client = run(tmp_path, respond, trials=5)
    s = result.summary
    sort = cell(s, "sort_list")
    assert (sort["n"], sort["failures"], sort["unusable"]) == (5, 2, 1)
    assert sort["failed_trials"] == [0, 1]
    assert sort["verdict"] == "fail"
    assert sort["upper_95_one_sided"] == pytest.approx(cal.cp_upper(2, 5, 0.05), abs=1e-6)
    assert cell(s, "lookup_value")["verdict"] == "pass"
    assert s["kinds"]["sort_list"] == {
        "decision": "simplify",
        "reason": None,
        "failing_models": [MODEL],
    }
    assert s["kinds"]["lookup_value"]["decision"] == "keep"

    records = {(r["kind"], r["trial"]): r for r in cal.read_trials(tmp_path / cal.TRIALS_FILE)}
    unusable = records[("sort_list", 0)]
    assert unusable["correct"] is False and "no tool call" in unusable["error"]
    assert unusable["attempts"] == 2 and unusable["calls"] == 2
    assert records[("sort_list", 1)]["error"] is None
    assert records[("sort_list", 1)]["expected"] == sorted(records[("sort_list", 1)]["answer"])
    assert (
        records[("sort_list", 2)]["correct"] is True and records[("sort_list", 2)]["attempts"] == 2
    )
    # Every call is metered and logged: 12 calls in all.
    assert len(client.messages.calls) == 12 == s["models"][MODEL]["calls"]
    assert s["models"][MODEL]["cost_usd"] == pytest.approx(
        sum(r["cost_usd"] for r in records.values())
    )
    assert s["total_cost_usd"] > 0


def test_a_crashed_run_resumes_without_redoing_finished_trials(tmp_path: Path) -> None:
    def crashes(params: dict[str, Any]) -> Any:
        crashes.n = getattr(crashes, "n", 0) + 1
        if crashes.n == 4:
            raise KeyboardInterrupt
        return solves(params)

    with pytest.raises(KeyboardInterrupt):
        run(tmp_path, crashes, trials=3)
    assert len(cal.read_trials(tmp_path / cal.TRIALS_FILE)) == 3
    # A truncated last line (crash mid-write) is skipped and doesn't corrupt the next record.
    with (tmp_path / cal.TRIALS_FILE).open("a") as f:
        f.write('{"kind": "sort_li')

    result, client = run(tmp_path, solves, trials=3)
    assert len(client.messages.calls) == 3  # only the 3 lookup_value trials
    assert all(c["verdict"] == "pass" and c["n"] == 3 for c in result.summary["cells"])
    assert len(cal.read_trials(tmp_path / cal.TRIALS_FILE)) == 6
    # A different master seed is a different set of variants: nothing is reused.
    _, client = run(tmp_path, solves, trials=3, seed=4)
    assert len(client.messages.calls) == 6


def test_the_spend_cap_stops_cleanly_and_a_larger_cap_resumes(tmp_path: Path) -> None:
    # Worst case per Fable call is ~$0.80; each fake call actually costs ~$0.11.
    result, client = run(tmp_path, cap=1.0, trials=3)
    assert result.aborted == "spend_cap" and "spend cap reached" in result.detail
    assert len(client.messages.calls) == 2
    records = cal.read_trials(tmp_path / cal.TRIALS_FILE)
    assert [r["status"] for r in records] == ["done", "done", "aborted"]
    s = result.summary
    assert cell(s, "sort_list")["verdict"] == "incomplete"
    assert s["kinds"]["sort_list"]["decision"] == "incomplete"
    assert s["run"]["aborted"] == "spend_cap"
    assert "Run stopped early (spend_cap)" in (tmp_path / cal.SUMMARY_MD).read_text()
    assert s["run"]["spent_this_run_usd"] == pytest.approx(
        cal.spent_usd(tmp_path / cal.TRIALS_FILE)
    )

    result, client = run(tmp_path, cap=100.0, trials=3)
    assert result.aborted is None and len(client.messages.calls) == 4
    assert all(c["verdict"] == "pass" for c in result.summary["cells"])


def test_provider_errors_leave_trials_unfinished_or_stop_the_run(tmp_path: Path) -> None:
    # The fake client raises a returned exception; the adapter passes ProviderError through.
    def flaky(params: dict[str, Any]) -> Any:
        flaky.n = getattr(flaky, "n", 0) + 1
        return ProviderError("overloaded", fatal=False) if flaky.n == 1 else solves(params)

    result, _ = run(tmp_path, flaky, trials=2, kinds=["sort_list"])
    first = cal.read_trials(tmp_path / cal.TRIALS_FILE)[0]
    assert first["status"] == "provider_error" and "overloaded" in first["error"]
    assert cell(result.summary, "sort_list")["n"] == 1
    assert result.summary["kinds"]["sort_list"]["decision"] == "incomplete"
    result, client = run(tmp_path, trials=2, kinds=["sort_list"])
    assert len(client.messages.calls) == 1 and cell(result.summary, "sort_list")["n"] == 2

    def fatal(params: dict[str, Any]) -> Any:
        return ProviderError("bad key", fatal=True)

    result, client = run(tmp_path / "f", fatal, trials=2)
    assert result.aborted == "provider_error" and len(client.messages.calls) == 1


def test_every_registered_kind_is_calibrated_or_reported(tmp_path: Path) -> None:
    result = cal.calibrate(
        [MODEL], task_kind_names(), out=tmp_path, solvers=cal.reference_solvers(), trials=3
    )
    kinds = result.summary["kinds"]
    assert set(kinds) == set(task_kind_names())
    for kind, report in kinds.items():
        try:
            cal.variant(kind, 0)
        except NotImplementedError:
            assert report["decision"] == "not_solo_calibratable" and report["reason"]
        else:
            assert report["decision"] == "keep"
    assert kinds["audit"]["decision"] == "keep"
    assert kinds["chain_relay"]["decision"] == "not_solo_calibratable"


def test_every_pool_model_is_calibrated_through_its_own_adapter(tmp_path: Path) -> None:
    def answer(tail: str) -> tuple[str, dict[str, Any]]:
        brief = brief_in(tail)
        return ("submit_answer", {"answer": task_kind(brief["kind"]).solve(brief)})

    def factory(spec: ModelSpec) -> ProviderAdapter:
        adapters[spec.id] = {
            "anthropic": lambda: AnthropicAdapter(spec, FakeClient(solves)),
            "openai": lambda: OpenAIAdapter(
                spec, OpenAIFake(lambda p: openai_reply(answer(openai_tail(p))))
            ),
            "google": lambda: GoogleAdapter(
                spec, GoogleFake(lambda p: google_reply(answer(google_tail(p))))
            ),
            "xai": lambda: XAIAdapter(
                spec, OpenAIFake(lambda p: xai_reply(answer(openai_tail(p))))
            ),
            "meta": lambda: MetaAdapter(
                spec, OpenAIFake(lambda p: openai_reply(answer(openai_tail(p))))
            ),
        }[spec.provider]()
        return adapters[spec.id]

    adapters: dict[str, ProviderAdapter] = {}
    pool = sorted(load_models())
    ledger = Ledger(100)
    solvers = cal.model_solvers(pool, ledger, adapter_factory=factory)
    result = cal.calibrate(
        pool, ["audit", *KINDS], out=tmp_path, solvers=solvers, trials=2, ledger=ledger
    )
    s = result.summary
    assert result.aborted is None
    assert all(calls_made(adapters[m]) == 6 for m in pool)
    assert {c["model"] for c in s["cells"]} == set(pool)
    assert all(c["verdict"] == "pass" and c["n"] == 2 for c in s["cells"])
    assert all(s["kinds"][k]["decision"] == "keep" for k in ["audit", *KINDS])
    assert all(s["models"][m]["cost_usd"] > 0 for m in pool)


def test_kinds_without_a_solo_form_are_reported_not_run(tmp_path: Path) -> None:
    result, client = run(tmp_path, kinds=["chain_relay"], trials=2)
    assert client.messages.calls == []
    assert result.summary["kinds"]["chain_relay"]["decision"] == "not_solo_calibratable"
    assert result.summary["cells"] == []


def test_unknown_kinds_and_models_fail_before_any_call(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unknown task kind"):
        run(tmp_path, kinds=["nope"])
    with pytest.raises(ValueError, match="unknown model"):
        run(tmp_path, models=["nope"])


# The live gate and the CLI ----------------------------------------------------------


def test_real_adapters_need_both_opt_ins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ZANTINE_LIVE", raising=False)
    with pytest.raises(LiveDisabled, match="--live"):
        cal.model_solvers([MODEL], Ledger(5))
    with pytest.raises(LiveDisabled, match="ZANTINE_LIVE=1"):
        cal.model_solvers([MODEL], Ledger(5), live=True)


def test_cli_dry_run_writes_the_report(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    args = ["--kinds", "sort_list,chain_relay", "--trials", "60", "--seed", "9", "--dry-run"]
    code = main(["calibrate", "--models", MODEL, "--out", str(tmp_path), *args])
    out = capsys.readouterr().out
    assert code == 0
    assert "| sort_list | claude-fable-5-1 | 60/60 | 0 | 0 | 0.0487 |" in out
    assert "| chain_relay | not_solo_calibratable |" in out
    assert (tmp_path / cal.SUMMARY_JSON).exists()


def test_cli_refuses_paid_calls_without_the_opt_ins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("ZANTINE_LIVE", raising=False)
    for extra in ([], ["--live"]):
        with pytest.raises(SystemExit):
            main(["calibrate", "--models", MODEL, "--out", str(tmp_path), *extra])
    assert "ZANTINE_LIVE=1" in capsys.readouterr().err
    assert not (tmp_path / cal.TRIALS_FILE).exists()
