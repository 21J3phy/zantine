"""Command-line interface: ``zantine play ...``."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from zantine.config import GameConfig
from zantine.engine import Game
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
    p.add_argument("--out", type=Path, help="write the JSONL transcript here")
    p.add_argument("--quiet", action="store_true", help="print only the result line")
    args = parser.parse_args(argv)

    try:
        config = GameConfig(seed=args.seed, round_cap=args.round_cap)
        game = play(config, args.bots)
    except ValueError as exc:
        parser.error(str(exc))
    if not args.quiet:
        print_timeline(game)
    print_result(game)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        game.transcript.write(args.out)
        print(f"transcript: {args.out}")
    return 0


def print_timeline(game: Game) -> None:
    names = game.state.roster()
    impostors = [p.name for p in game.state.players if p.is_impostor]
    print(f"seed {game.config.seed}; Impostors: {', '.join(impostors)}")
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
        case "vote_tally" if d["ejected"] is None:
            return f"no one ejected (skips: {d['skips']})"
    return None


def print_result(game: Game) -> None:
    s = game.state
    assert s.winner is not None
    print(
        f"{s.winner.value} win by {s.win_reason} after {s.round} rounds, {s.meetings_held} meetings"
    )


if __name__ == "__main__":
    sys.exit(main())
