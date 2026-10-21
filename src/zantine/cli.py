"""Command-line interface: ``zantine play ...``, ``replay``, ``schedule``, ``run``,
``report``, ``calibrate``, ``audit``."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from zantine import runs
from zantine.analysis import report
from zantine.config import GameConfig
from zantine.engine import Game
from zantine.harness.pricing import EFFORT_LEVELS
from zantine.roles import Team
from zantine.runner import play
from zantine.seats.live import harness_names
from zantine.seats.registry import controller_names

if TYPE_CHECKING:
    from zantine.audit.extractors import ClaimExtractor
    from zantine.harness.ledger import Ledger


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="zantine")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("play", help="play one game and print a summary")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument(
        "--bots",
        default="scripted",
        help=f"controller spec, e.g. 'scripted' or 'scripted*9,idle' (known: "
        f"{', '.join(controller_names())}); live Seats: 'live:<model>[@<harness>]' "
        f"(harnesses: {', '.join(harness_names())}; default neutral)",
    )
    p.add_argument("--round-cap", type=int, default=GameConfig.round_cap)
    p.add_argument(
        "--impostors",
        type=int,
        default=GameConfig.num_impostors,
        help="number of Impostors, 0 to 2 (0 = Benign Match)",
    )
    p.add_argument("--out", type=Path, help="write the JSONL transcript here")
    p.add_argument("--quiet", action="store_true", help="print only the result line")
    p.add_argument(
        "--live",
        action="store_true",
        help="allow live:<model> seats to call paid APIs (also needs ZANTINE_LIVE=1)",
    )
    p.add_argument(
        "--spend-cap",
        type=float,
        default=10.0,
        help="hard limit in USD on API spend for live seats (default 10)",
    )
    p.add_argument(
        "--effort",
        choices=EFFORT_LEVELS,
        help="reasoning effort for every live seat, mapped to each lab's own setting "
        "(default: the level in models.toml, medium)",
    )
    r = sub.add_parser("replay", help="write a static HTML replay of a transcript (not scored)")
    r.add_argument("transcript", type=Path, help="JSONL transcript from `zantine play --out`")
    r.add_argument("--out", type=Path, help="HTML file to write (default: <transcript>.html)")
    runs.add_commands(sub)  # `zantine schedule`, `zantine run`
    report.add_command(sub)  # `zantine report`
    _add_calibrate_parser(sub)
    a = sub.add_parser("audit", help="run the Claim Audit over a transcript")
    a.add_argument("transcript", type=Path, help="JSONL transcript from `zantine play --out`")
    a.add_argument(
        "--extractor",
        default="pattern",
        help="'pattern' (free regex dry run, not for scoring) or an id from "
        "audit/extractors.toml (paid; needs --live and ZANTINE_LIVE=1)",
    )
    a.add_argument("--extractors", type=Path, help="alternative extractors.toml")
    a.add_argument("--out", type=Path, help="JSON to write (default: <transcript>.audit.json)")
    a.add_argument(
        "--judge",
        default="keyword",
        choices=("keyword", "jev", "none"),
        help="judgments for Floor Share, Vote Pull, Suspicion, Lie Survival and Misdirection: "
        "'keyword' (free dry run, not for scoring), 'jev' (paid; needs --live and "
        "ZANTINE_LIVE=1) or 'none'",
    )
    a.add_argument(
        "--threshold",
        type=float,
        default=0.5,
        help="flag judgments whose confidence is below this (default 0.5)",
    )
    a.add_argument("--live", action="store_true", help="allow paid extractor and Jev calls")
    a.add_argument(
        "--spend-cap", type=float, default=2.0, help="USD cap on extractor plus Jev spend"
    )
    pc = sub.add_parser(
        "planted", help="measure a Claim extractor on Planted Claims (known-truth statements)"
    )
    pc.add_argument(
        "--extractor",
        default="pattern",
        help="'pattern' (free regex dry run) or an id from audit/extractors.toml "
        "(paid; needs --live and ZANTINE_LIVE=1)",
    )
    pc.add_argument("--extractors", type=Path, help="alternative extractors.toml")
    pc.add_argument("--n", type=int, default=400, help="number of Planted Claims (default 400)")
    pc.add_argument("--seed", type=int, default=0, help="seed for the Planted Claims and games")
    pc.add_argument(
        "--games",
        type=int,
        default=8,
        help="scripted games to plant into, seeds SEED .. SEED+GAMES-1 (default 8)",
    )
    pc.add_argument(
        "--transcripts",
        type=Path,
        nargs="+",
        help="plant into these JSONL transcripts instead of scripted games",
    )
    pc.add_argument(
        "--out",
        type=Path,
        help="JSON to write (default: runs/planted/<extractor>-seed<S>-n<N>.json)",
    )
    pc.add_argument("--live", action="store_true", help="allow paid extractor calls")
    pc.add_argument("--spend-cap", type=float, default=2.0, help="USD cap on extractor spend")
    args = parser.parse_args(argv)
    if args.command == "report":
        return report.command(parser, args)
    if args.command == "replay":
        return replay(parser, args.transcript, args.out)
    if args.command in ("schedule", "run"):
        return runs.command(parser, args)
    if args.command == "calibrate":
        return calibrate(parser, args)
    if args.command == "audit":
        return audit(parser, args)
    if args.command == "planted":
        return planted(parser, args)

    try:
        config = GameConfig(seed=args.seed, round_cap=args.round_cap, num_impostors=args.impostors)
        if args.live or "live" in args.bots:
            return _play_live(args, config)
        game = play(config, args.bots)
    except ValueError as exc:
        parser.error(str(exc))
    if not args.quiet:
        print_timeline(game)
    print_result(game)
    if args.out:
        _write(game, args.out)
    return 0


def replay(parser: argparse.ArgumentParser, transcript: Path, out: Path | None) -> int:
    from zantine.replay import write_replay

    if not transcript.is_file():
        parser.error(f"no such transcript: {transcript}")
    out = out or transcript.with_suffix(".html")
    view = write_replay(transcript, out)
    print(f"replay: {out} ({len(view['rounds'])} frames, {len(view['meetings'])} Meetings)")
    return 0


def _add_calibrate_parser(sub: Any) -> None:
    c = sub.add_parser(
        "calibrate", help="solo task calibration: each Task kind alone, N trials per Model"
    )
    c.add_argument("--models", required=True, help="comma-separated Model IDs (models.toml)")
    c.add_argument("--kinds", help="comma-separated Task kinds (default: every registered kind)")
    c.add_argument("--trials", type=int, default=60, help="trials per kind per Model (default 60)")
    c.add_argument("--seed", type=int, default=0, help="master seed for the trial variants")
    c.add_argument("--out", type=Path, default=Path("runs/calibration"), help="results directory")
    mode = c.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run", action="store_true", help="use each kind's reference solver ($0, no API)"
    )
    mode.add_argument(
        "--live", action="store_true", help="allow paid API calls (also needs ZANTINE_LIVE=1)"
    )
    c.add_argument(
        "--spend-cap",
        type=float,
        default=5.0,
        help="hard limit in USD on calibration spend recorded in --out, earlier runs "
        "included (default 5)",
    )


def calibrate(parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    from zantine import calibration as cal
    from zantine.harness.ledger import Ledger
    from zantine.harness.pricing import load_models, model_spec
    from zantine.tasks import task_kind_names

    models = _names(args.models)
    kinds = task_kind_names() if args.kinds is None else _names(args.kinds)
    try:
        cal.check_selection("models", models)
        cal.check_selection("kinds", kinds)
        ledger = Ledger(args.spend_cap)
        if args.dry_run:
            pool = load_models()
            for model_id in models:
                model_spec(model_id, pool)  # unknown Models fail even at $0
            solvers = cal.reference_solvers()
        else:
            # The cap covers the whole results directory, so resuming never resets it.
            ledger.spent_usd = cal.spent_usd(args.out / cal.TRIALS_FILE)
            solvers = cal.model_solvers(models, ledger, live=args.live)
        run = cal.calibrate(
            models,
            kinds,
            out=args.out,
            solvers=solvers,
            trials=args.trials,
            seed=args.seed,
            ledger=ledger,
        )
    except ValueError as exc:
        parser.error(str(exc))
    print(cal.render_markdown(run.summary), end="")
    if not args.dry_run:
        print(f"spent ${ledger.spent_usd:.4f} of ${ledger.spend_cap_usd:.2f} cap (all runs)")
    print(f"results: {args.out / cal.TRIALS_FILE}, {args.out / cal.SUMMARY_MD}")
    if run.aborted:
        print(f"ABORTED ({run.aborted}): {run.detail}")
        return 1
    return 0


def _names(text: str) -> list[str]:
    return [name.strip() for name in text.split(",") if name.strip()]


def make_extractor(
    parser: argparse.ArgumentParser, args: argparse.Namespace
) -> tuple[ClaimExtractor, Ledger | None]:
    """``--extractor``: 'pattern', or a paid entry of extractors.toml behind the live gate."""
    from zantine.audit import PatternExtractor
    from zantine.audit.llm import LLMExtractor, load_extractor_specs
    from zantine.harness.ledger import Ledger

    if args.extractor == "pattern":
        return PatternExtractor(), None
    specs = load_extractor_specs(args.extractors)
    if args.extractor not in specs:
        parser.error(f"unknown extractor {args.extractor!r}; known: pattern, {', '.join(specs)}")
    try:
        ledger = Ledger(args.spend_cap)
        return LLMExtractor.from_env(specs[args.extractor], ledger, live=args.live), ledger
    except ValueError as exc:
        parser.error(str(exc))


def audit(parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    from zantine.audit import PatternExtractor, load_transcript, run_audit, write_audit
    from zantine.audit.llm import LLMExtractor, load_extractor_specs
    from zantine.harness.ledger import Ledger
    from zantine.judge import KeywordJudge, run_judgments
    from zantine.judge.jev import JevJudge
    from zantine.judge.judges import Judge

    if not args.transcript.is_file():
        parser.error(f"no such transcript: {args.transcript}")
    paid = args.extractor != "pattern" or args.judge == "jev"
    # One ledger, so one spend cap covers the extractor and Jev together.
    ledger = Ledger(args.spend_cap) if paid else None
    extractor: ClaimExtractor
    judge: Judge | None = None
    try:
        if args.extractor == "pattern":
            extractor = PatternExtractor()
        else:
            specs = load_extractor_specs(args.extractors)
            if args.extractor not in specs:
                parser.error(
                    f"unknown extractor {args.extractor!r}; known: pattern, {', '.join(specs)}"
                )
            assert ledger is not None
            extractor = LLMExtractor.from_env(specs[args.extractor], ledger, live=args.live)
        if args.judge == "keyword":
            judge = KeywordJudge()
        elif args.judge == "jev":
            assert ledger is not None
            judge = JevJudge.from_env(ledger, live=args.live)
    except ValueError as exc:
        parser.error(str(exc))
    records = load_transcript(args.transcript)
    report = run_audit(records, extractor, ledger=ledger)
    if judge is not None:
        try:
            report["judgments"] = run_judgments(
                records, report, judge, ledger=ledger, threshold=args.threshold
            )
        except ValueError as exc:
            parser.error(str(exc))
    out = args.out or args.transcript.with_suffix(".audit.json")
    write_audit(report, out)
    print_audit(report)
    if judge is not None:
        print_judgments(report["judgments"])
    print(f"audit: {out}")
    complete = report["complete"] and (judge is None or report["judgments"]["complete"])
    return 0 if complete else 1


def print_audit(report: dict[str, Any]) -> None:
    ex = report["extractor"]
    scoring = "" if ex.get("scoring") else " (dry run, not for scoring)"
    print(f"extractor: {ex['name']} {ex.get('model') or ''} {ex.get('version')}{scoring}")
    print(f"{len(report['statements'])} statements, {len(report['claims'])} Claims")
    print("  seat name        role      claims  true  lies  missta  unver  reliab  lie%   miss%")
    for s in report["seats"]:
        print(
            f"  {s['seat']:>4} {s['name']:<11} {s['role'] or '?':<9} {s['claims']:>6} "
            f"{s['true']:>5} {s['lie']:>5} {s['misstatement']:>7} {s['unverifiable']:>6}  "
            f"{_pct(s['reliability'])} {_pct(s['lie_rate'])} {_pct(s['misstatement_rate'])}"
        )
    for c in report["claims"]:
        if c["verdict"]["lie"]:
            cites = ", ".join(f"{p['type']} #{p['seq']}" for p in c["verdict"]["proof"])
            print(f"  Lie {c['id']} (seat {c['speaker']}): {c['quote']!r}; knew from {cites}")
    d = report.get("disclosure")
    if d and d["observations"]:
        print("  seat name        role      marked  eligible  first  later  never  discl%  lag")
        for s in d["seats"]:
            if s["marked"]:
                lag = "  -" if s["disclosure_lag"] is None else f"{s['disclosure_lag']:5.2f}"
                print(
                    f"  {s['seat']:>4} {s['name']:<11} {s['role'] or '?':<9} {s['marked']:>6} "
                    f"{s['eligible']:>9} {s['disclosed_first_meeting']:>6} "
                    f"{s['disclosed_later']:>6} {s['undisclosed']:>6}  "
                    f"{_pct(s['disclosure_rate'])} {lag}"
                )
    if report["aborted"]:
        print(f"ABORTED ({report['aborted']['reason']}): {report['aborted']['detail']}")
    elif not report["complete"]:
        print(
            f"INCOMPLETE: {report['statement_errors']} statement(s) not fully audited; "
            "the rates above may be partial"
        )
    if report["cost"]:
        print(f"extractor spend ${report['cost']['cost_usd']:.4f}")


def planted(parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    from zantine.audit import load_transcript, write_audit
    from zantine.audit.planted import evaluate, generate, scripted_games

    if args.n < 1 or args.games < 1:
        parser.error("--n and --games must be at least 1")
    extractor, ledger = make_extractor(parser, args)
    if args.transcripts:
        for t in args.transcripts:
            if not t.is_file():
                parser.error(f"no such transcript: {t}")
        games = [load_transcript(t) for t in args.transcripts]
    else:
        games = scripted_games(args.seed, args.games)
    try:
        claims = generate(games, args.n, args.seed)
    except ValueError as exc:
        parser.error(str(exc))
    report = evaluate(games, claims, extractor, ledger=ledger)
    report["generation"] = {
        "seed": args.seed,
        "n": args.n,
        "games": (
            [str(t) for t in args.transcripts]
            if args.transcripts
            else {"scripted_seeds": list(range(args.seed, args.seed + args.games))}
        ),
    }
    name = args.extractor.replace("/", "_")
    out = args.out or Path("runs") / "planted" / f"{name}-seed{args.seed}-n{args.n}.json"
    write_audit(report, out)
    print_planted(report)
    print(f"planted: {out}")
    return 0 if report["complete"] else 1


def print_planted(report: dict[str, Any]) -> None:
    ex = report["extractor"]
    scoring = "" if ex.get("scoring") else " (dry run, not for scoring)"
    print(f"extractor: {ex['name']} {ex.get('model') or ''} {ex.get('version')}{scoring}")
    print(f"{report['processed']}/{report['n']} Planted Claims")
    print("  group                  correct     n  accuracy  95% CI")
    rows = [("overall", report["overall"])]
    for key in ("by_phrasing", "by_kind", "by_context", "by_truth"):
        rows += [(f"{key[3:]}={g}", s) for g, s in report[key].items()]
    rows += [("extraction only", report["extraction"]), ("label check", report["label_agreement"])]
    for label, s in rows:
        lo, hi = s["ci95"]
        ci = "-" if lo is None else f"[{lo:.1%}, {hi:.1%}]"
        print(f"  {label:<22} {s['correct']:>7} {s['n']:>5}  {_pct(s['accuracy'])}  {ci}")
    if report["aborted"]:
        print(f"ABORTED ({report['aborted']['reason']}): {report['aborted']['detail']}")
    if report["errors"]:
        print(f"{report['errors']} Planted Claims had extraction errors (counted as wrong)")
    if cost := report["cost"]:
        line = f"extractor cost ${cost['cost_usd']:.4f} settled"
        if cost["held_usd"]:
            line += f", ${cost['held_usd']:.4f} held (usage not reported; possibly billed)"
        print(f"{line}; run spend ${cost['run_spent_usd']:.4f} of ${cost['spend_cap_usd']:.2f} cap")


def print_judgments(j: dict[str, Any]) -> None:
    judge = j["judge"]
    scoring = "" if judge.get("scoring") else " (dry run, not for scoring)"
    print(f"judge: {judge['name']} {judge.get('model') or ''} {judge.get('version')}{scoring}")
    print("  seat name        role      floor   pull   susp   lie-surv  misdir")
    for s in j["seats"]:
        print(
            f"  {s['seat']:>4} {s['name']:<11} {s['role'] or '?':<9} "
            f"{_pct(s['floor_share'])} {_pct(s['vote_pull'])} {_pct(s['suspicion'])} "
            f"{_pct(s['lie_survival'])}    {s['misdirections']:>3}"
        )
    print(f"{len(j['low_confidence'])} low-confidence judgments (threshold {j['threshold']})")
    if j["aborted"]:
        print(f"JUDGMENTS ABORTED ({j['aborted']['reason']}): {j['aborted']['detail']}")
    if j["cost"]:
        print(f"Jev spend ${j['cost']['cost_usd']:.6f} ({j['cost']['calls']} calls)")


def _pct(x: float | None) -> str:
    return "   -  " if x is None else f"{x:6.0%}"


def _write(game: Game, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    game.transcript.write(out)
    print(f"transcript: {out}")


def _play_live(args: argparse.Namespace, config: GameConfig) -> int:
    from zantine.harness.live import play_live

    run = play_live(
        config, args.bots, spend_cap_usd=args.spend_cap, live=args.live, effort=args.effort
    )
    game = run.game
    if not args.quiet:
        print_timeline(game)
    if run.aborted:
        print(f"ABORTED ({run.aborted}) in round {game.state.round}: {run.detail}")
    else:
        print_result(game)
    for s in run.cost["seats"]:
        if s["calls"]:
            print(
                f"  seat {s['seat']} {s['controller'].get('model')}: {s['calls']} calls, "
                f"${s['cost_usd']:.4f}, in {s['input_tokens']} / cached "
                f"{s['cached_input_tokens']} / out {s['output_tokens']} "
                f"(thinking {s['thinking_tokens']}), invalid {s['invalid_actions']}"
            )
    print(f"spent ${run.ledger.spent_usd:.4f} of ${run.ledger.spend_cap_usd:.2f} cap")
    _write(game, args.out or Path(f"runs/live-seed-{config.seed}.jsonl"))
    return 1 if run.aborted else 0


def print_timeline(game: Game) -> None:
    names = game.state.roster()
    impostors = [p.name for p in game.state.players if p.is_impostor]
    print(f"seed {game.config.seed}; Impostors: {', '.join(impostors) or 'none'}")
    for r in game.transcript.events():
        line = _describe(r["type"], r["data"], names)
        if line:
            print(f"  round {r['round']:>2}: {line}")


def _describe(type: str, d: dict[str, Any], names: dict[int, str]) -> str | None:
    match type:
        case "kill":
            killers = " & ".join(names[k] for k in d["killers"])
            return f"{killers} killed {names[d['victim']]} in {d['room']}"
        case "meeting_started" if d["reason"] == "report":
            return f"{names[d['caller']]} reported {names[d['body_victim']]}'s body"
        case "meeting_started":
            return f"{names[d['caller']]} called an emergency Meeting"
        case "ejection":
            return f"{names[d['seat']]} was ejected ({d['role']})"
        case "containment":
            return "Containment: every Impostor has been ejected; play continues"
        case "vote_tally" if d["ejected"] is None:
            return f"no one ejected (skips: {d['skips']})"
        case "tasks.graded" if d["correct"]:
            return f"{names[d['seat']]} completed Task {d['task']}"
    return None


def print_result(game: Game) -> None:
    s = game.state
    assert s.winner is not None
    outcome = "no winner" if s.winner is Team.NONE else f"{s.winner.value} win"
    print(f"{outcome} by {s.win_reason} after {s.round} rounds, {s.meetings_held} meetings")
    tasks = game.transcript.records[-1].get("tasks")
    if tasks and tasks["total"]:
        print(f"Productivity: {tasks['done']}/{tasks['total']} Tasks ({tasks['productivity']:.0%})")


if __name__ == "__main__":
    sys.exit(main())
