"""Command-line interface: ``zantine play ...``, ``replay``, ``schedule``, ``run``,
``report``, ``calibrate``, ``audit``."""

from __future__ import annotations

import argparse
import json
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
    a.add_argument("--live", action="store_true", help="allow paid extractor calls")
    a.add_argument("--spend-cap", type=float, default=2.0, help="USD cap on extractor spend")
    mo = sub.add_parser("moments", help="find candidate single-game Moments for sharing")
    mo.add_argument("path", type=Path, help="a JSONL transcript or a run dir of transcripts")
    mo.add_argument("--top", type=int, help="keep only the N highest-scoring Moments")
    mo.add_argument(
        "--out",
        type=Path,
        help="JSON to write, plus a .md list beside it "
        "(default: <transcript>.moments.json, or <run dir>/moments.json)",
    )
    mo.add_argument(
        "--no-replay", action="store_true", help="don't write missing replay pages for the links"
    )
    args = parser.parse_args(argv)
    if args.command == "report":
        return report.command(parser, args)
    if args.command == "moments":
        return moments(parser, args)
    if args.command == "replay":
        return replay(parser, args.transcript, args.out)
    if args.command in ("schedule", "run"):
        return runs.command(parser, args)
    if args.command == "calibrate":
        return calibrate(parser, args)
    if args.command == "audit":
        return audit(parser, args)

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


def audit(parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    from zantine.audit import PatternExtractor, load_transcript, run_audit, write_audit
    from zantine.audit.extractors import ClaimExtractor
    from zantine.audit.llm import LLMExtractor, load_extractor_specs
    from zantine.harness.ledger import Ledger

    if not args.transcript.is_file():
        parser.error(f"no such transcript: {args.transcript}")
    ledger: Ledger | None = None
    extractor: ClaimExtractor
    if args.extractor == "pattern":
        extractor = PatternExtractor()
    else:
        specs = load_extractor_specs(args.extractors)
        if args.extractor not in specs:
            parser.error(
                f"unknown extractor {args.extractor!r}; known: pattern, {', '.join(specs)}"
            )
        try:
            ledger = Ledger(args.spend_cap)
            extractor = LLMExtractor.from_env(specs[args.extractor], ledger, live=args.live)
        except ValueError as exc:
            parser.error(str(exc))
    report = run_audit(load_transcript(args.transcript), extractor, ledger=ledger)
    out = args.out or args.transcript.with_suffix(".audit.json")
    write_audit(report, out)
    print_audit(report)
    print(f"audit: {out}")
    return 0 if report["complete"] else 1


def moments(parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    from zantine.moments import find_in_path, to_markdown

    if not args.path.exists():
        parser.error(f"no such transcript or run dir: {args.path}")
    if args.top is not None and args.top < 1:
        parser.error("--top must be at least 1")
    default = (
        args.path / "moments.json" if args.path.is_dir() else args.path.with_suffix(".moments.json")
    )
    out = args.out or default
    try:
        report = find_in_path(
            args.path, out_dir=out.parent, top=args.top, write_replays=not args.no_replay
        )
    except ValueError as exc:
        parser.error(str(exc))
    if not report["games"]:
        parser.error(f"no transcripts found in {args.path}")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, sort_keys=True, indent=1) + "\n", encoding="utf-8")
    md = out.with_suffix(".md")
    if md == out:
        md = out.with_name(out.name + ".md")
    md.write_text(to_markdown(report), encoding="utf-8")
    for g in report["games"]:
        if g["audit_rejected"]:
            print(f"warning: {g['transcript']}: audit not used: {g['audit_rejected']}")
    for i, m in enumerate(report["moments"], 1):
        print(f"{i:>3}. [{m['score']:.2f}] {m['caption']}  -> {m['link']}")
    shown = len(report["moments"])
    print(f"{shown} of {report['candidates']} Moments from {len(report['games'])} games")
    print(f"moments: {out} and {md}")
    return 0


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
