"""Command-line interface: ``zantine play ...``, ``replay``, ``schedule``, ``run``."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from zantine import runs
from zantine.config import GameConfig
from zantine.engine import Game
from zantine.harness.pricing import EFFORT_LEVELS
from zantine.roles import Team
from zantine.runner import play
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
        f"{', '.join(controller_names())})",
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
    args = parser.parse_args(argv)
    if args.command == "replay":
        return replay(parser, args.transcript, args.out)
    if args.command in ("schedule", "run"):
        return runs.command(parser, args)

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
