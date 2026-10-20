"""Command-line interface: ``zantine play ...``, ``replay``, ``schedule``, ``run``,
``report``, ``calibrate``, ``audit``, ``stylemask``."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from zantine import runs
from zantine.analysis import report
from zantine.config import GameConfig
from zantine.engine import Game
from zantine.harness.pricing import EFFORT_LEVELS
from zantine.roles import Team
from zantine.runner import play
from zantine.seats.live import harness_names
from zantine.seats.registry import controller_names


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
    p.add_argument(
        "--style-mask",
        metavar="MASKER",
        help="Style Mask condition: 'neutral' (free rule-based rewrite) or an id from "
        "stylemask/maskers.toml (paid; needs --live and ZANTINE_LIVE=1). Off by default",
    )
    p.add_argument(
        "--mask-extractor",
        default="pattern",
        help="Claim extractor for the Style Mask meaning check: 'pattern' (free) or an id "
        "from audit/extractors.toml (paid)",
    )
    p.add_argument(
        "--mask-judge",
        default="keyword",
        choices=("keyword", "jev"),
        help="judge for the meaning check's accusation target: 'keyword' (free) or 'jev' (paid)",
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
    sm = sub.add_parser("stylemask", help="Style Mask reports")
    sm_sub = sm.add_subparsers(dest="stylemask_command", required=True)
    ev = sm_sub.add_parser(
        "eval", help="style classifier: can the source Model be told from original vs masked text?"
    )
    ev.add_argument("paths", nargs="+", type=Path, help="transcripts or directories of them")
    ev.add_argument("--folds", type=int, default=5, help="cross-validation folds (default 5)")
    ev.add_argument("--out", type=Path, help="write the JSON report here")
    args = parser.parse_args(argv)
    if args.command == "stylemask":
        return stylemask_eval(parser, args)
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

    try:
        config = GameConfig(
            seed=args.seed,
            round_cap=args.round_cap,
            num_impostors=args.impostors,
            style_mask=args.style_mask is not None,
        )
        paid_mask = config.style_mask and (
            args.style_mask != "neutral"
            or args.mask_extractor != "pattern"
            or args.mask_judge != "keyword"
        )
        if args.live or "live" in args.bots or paid_mask:
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


def audit(parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    from zantine.audit import PatternExtractor, load_transcript, run_audit, write_audit
    from zantine.audit.extractors import ClaimExtractor
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


def stylemask_eval(parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    import json

    from zantine.stylemask.classifier import evaluate

    if args.folds < 2:
        parser.error("--folds must be at least 2")
    for path in args.paths:
        if not path.exists():
            parser.error(f"no such file or directory: {path}")
    report = evaluate(args.paths, folds=args.folds)
    print(
        f"style classifier {report['classifier']['name']}: {report['transcripts']} transcripts, "
        f"{report['masked_games']} with a Style Mask"
    )
    for key, label in (
        ("original_all", "original, all games"),
        ("original", "original, masked games"),
        ("masked", "masked"),
    ):
        print(f"  {label:<23} {_classifier_line(report[key])}")
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        print(f"report: {args.out}")
    return 0


def _classifier_line(r: dict[str, Any]) -> str:
    if r["balanced_accuracy"] is None:
        return f"{r['messages']} messages: {r['note']}"
    lo, hi = r["balanced_accuracy_ci95"]
    alo, ahi = r["accuracy_ci95"]
    return (
        f"{r['messages']} messages, {len(r['models'])} Models: balanced accuracy "
        f"{r['balanced_accuracy']:.0%} [{lo:.0%}, {hi:.0%}] vs chance "
        f"{r['chance_balanced_accuracy']:.0%}; accuracy {r['accuracy']:.0%} [{alo:.0%}, "
        f"{ahi:.0%}] vs majority {r['chance_accuracy']:.0%} (p={r['p_value_vs_chance']:.3g})"
    )


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
    if report["aborted"]:
        print(f"ABORTED ({report['aborted']['reason']}): {report['aborted']['detail']}")
    elif not report["complete"]:
        print(
            f"INCOMPLETE: {report['statement_errors']} statement(s) not fully audited; "
            "the rates above may be partial"
        )
    if report["cost"]:
        print(f"extractor spend ${report['cost']['cost_usd']:.4f}")


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
    from zantine.harness.ledger import Ledger
    from zantine.harness.live import play_live
    from zantine.stylemask import build_style_mask

    # One ledger: Seats, the masker and its meaning check share the spend cap.
    ledger = Ledger(args.spend_cap)
    style_mask = None
    if config.style_mask:
        style_mask = build_style_mask(
            args.style_mask,
            args.mask_extractor,
            args.mask_judge,
            ledger=ledger,
            live=args.live,
        )
    run = play_live(
        config,
        args.bots,
        spend_cap_usd=args.spend_cap,
        live=args.live,
        effort=args.effort,
        ledger=ledger,
        style_mask=style_mask,
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
