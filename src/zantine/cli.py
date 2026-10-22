"""Command-line interface: ``zantine play ...``, ``replay``, ``schedule``, ``run``,
``report``, ``calibrate``, ``audit``, and ``repro`` / ``commit`` / ``verify`` / ``release``."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from zantine import repro, runs
from zantine.analysis import report
from zantine.config import GameConfig, LeaderConfig
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
    p.add_argument(
        "--leader",
        choices=("known", "hidden"),
        help="v1.4 Leader condition: a Known Leader (everyone is told who leads) or a Hidden "
        "Leader (only the Leader is told). Only prompt lines change.",
    )
    p.add_argument("--leader-seat", type=int, default=0, help="the Leader's Seat (default 0)")
    p.add_argument(
        "--leader-role",
        choices=("crewmate", "impostor", "drawn"),
        default="crewmate",
        help="force a Crewmate or Compromised (Impostor) Leader, or keep the seeded draw",
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
    repro.add_commands(sub)  # `zantine repro`, `commit`, `verify`, `release`
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
    add_panel_parser(sub)
    lr = sub.add_parser(
        "leader-report", help="v1.4 Compliance, time to eject and Vote Pull graphs from audits"
    )
    lr.add_argument("audits", type=Path, nargs="+", help="audit JSON files from `zantine audit`")
    lr.add_argument("--out", type=Path, default=Path("leader-report"), help="output directory")
    lr.add_argument("--replicates", type=int, default=2000, help="bootstrap replicates")
    lr.add_argument("--bootstrap-seed", type=int, default=0)
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
    if args.command == "panel":
        return panel(parser, args)
    if args.command == "leader-report":
        return leader_report(parser, args)
    if args.command == "moments":
        return moments(parser, args)
    if args.command == "replay":
        return replay(parser, args.transcript, args.out)
    if args.command in ("schedule", "run"):
        return runs.command(parser, args)
    if args.command in repro.COMMANDS:
        return repro.command(parser, args)
    if args.command == "calibrate":
        return calibrate(parser, args)
    if args.command == "audit":
        return audit(parser, args)
    if args.command == "planted":
        return planted(parser, args)

    try:
        leader = None
        if args.leader:
            leader = LeaderConfig(
                seat=args.leader_seat,
                known=args.leader == "known",
                compromised={"crewmate": False, "impostor": True}.get(args.leader_role),
            )
        config = GameConfig(
            seed=args.seed,
            round_cap=args.round_cap,
            num_impostors=args.impostors,
            leader=leader,
        )
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


def leader_report(parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    import json

    from zantine.leader.analysis import condition_label, leader_results, leader_rows
    from zantine.leader.graphs import write_graphs

    reports = []
    for path in args.audits:
        if not path.is_file():
            parser.error(f"no such audit: {path}")
        reports.append(json.loads(path.read_text("utf-8")))
    try:
        rows = leader_rows(reports)
        if not rows:
            parser.error("no audit has a Leader section (run `zantine audit` on Leader games)")
        results = leader_results(rows, replicates=args.replicates, seed=args.bootstrap_seed)
    except ValueError as exc:
        parser.error(str(exc))
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "leader.json").write_text(json.dumps(results, indent=1) + "\n", "utf-8")
    graphs = write_graphs(results, args.out)
    if results["label"]:
        print(results["label"])
    excluded = results["excluded_games"]
    print(
        f"{results['games']} played Leader games ({results['incomplete_games']} with incomplete "
        f"judgments; left out: {excluded['aborted']} aborted, {excluded['truncated']} truncated)"
    )
    for condition, c in results["conditions"].items():
        line = f"  {condition_label(condition):<28} {c['games']:>3} games  Compliance "
        line += _ci(c["compliance"])
        if "time_to_eject" in c:
            line += "  time to eject " + _ci(c["time_to_eject"]["mean_rounds_censored_at_cap"], 1)
        print(line)
    print(f"report: {args.out / 'leader.json'}; graphs: {', '.join(p.name for p in graphs)}")
    return 0


def _ci(e: dict[str, Any], digits: int | None = None) -> str:
    if e["estimate"] is None:
        return "-"

    def f(v: float) -> str:
        return f"{v:.0%}" if digits is None else f"{v:.{digits}f}"

    if e["ci_low"] is None:
        return f(e["estimate"])
    return f"{f(e['estimate'])} [{f(e['ci_low'])}, {f(e['ci_high'])}]"


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


def add_panel_parser(sub: Any) -> None:
    pan = sub.add_parser(
        "panel", help="Label Panel: three AI judges that validate the Claim Audit (#34)"
    )
    ps = pan.add_subparsers(dest="panel_command", required=True)

    def paid(p: argparse.ArgumentParser) -> None:
        p.add_argument("--judges", type=Path, help="alternative panel judges.toml")
        p.add_argument("--live", action="store_true", help="allow paid judge calls")
        p.add_argument(
            "--spend-cap", type=float, required=True, help="USD cap on Label Panel spend"
        )
        p.add_argument("--seed", type=int, default=0)

    q = ps.add_parser("qualify", help="score each judge on plain Planted Claims (>= 0.95)")
    q.add_argument("--planted", type=Path, required=True, help="Planted Claims report (#14)")
    q.add_argument(
        "games", type=Path, nargs="+", help="transcripts the planted items' `game` indexes"
    )
    q.add_argument("--out", type=Path, default=Path("runs/panel/qualification.json"))
    paid(q)
    lab = ps.add_parser("label", help="label a stratified sample of real-game Claims")
    lab.add_argument(
        "transcripts", type=Path, nargs="+", help="audited transcripts (<t>.audit.json beside)"
    )
    lab.add_argument("--qualification", type=Path, required=True)
    lab.add_argument("--n", type=int, default=600, help="Claims to sample (default 600)")
    lab.add_argument("--out", type=Path, default=Path("runs/panel/labels.json"))
    paid(lab)
    rep = ps.add_parser("report", help="Krippendorff's alpha and pipeline accuracy")
    rep.add_argument("labels", type=Path)
    rep.add_argument("--out", type=Path, help="JSON to write (default: <labels>.report.json)")


def panel(parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    import json

    from zantine.audit import load_transcript, write_audit
    from zantine.harness.ledger import Ledger
    from zantine.panel import LLMPanelJudge, PlantedReport, candidates, load_panel
    from zantine.panel import label as run_label
    from zantine.panel import qualify as run_qualify
    from zantine.panel import report as run_report

    def read_json(path: Path) -> Any:
        if not path.is_file():
            parser.error(f"no such file: {path}")
        return json.loads(path.read_text(encoding="utf-8"))

    if args.panel_command == "report":
        try:
            out = run_report(read_json(args.labels))
        except ValueError as exc:
            parser.error(str(exc))
        write_audit(out, args.out or args.labels.with_suffix(".report.json"))
        print_panel_report(out)
        return 0 if out["complete"] else 1

    ledger = Ledger(args.spend_cap)
    try:
        config = load_panel(args.judges)
        judges = [LLMPanelJudge.from_env(j, ledger, live=args.live) for j in config.judges]
    except ValueError as exc:
        parser.error(str(exc))
    print(VALIDATED_BY_LINE)
    if args.panel_command == "qualify":
        games = [load_transcript(g) for g in args.games]
        try:
            planted = PlantedReport.from_json(games, read_json(args.planted))
            out = run_qualify(
                judges, planted, seed=args.seed, min_planted=config.min_planted, ledger=ledger
            )
        except ValueError as exc:
            parser.error(str(exc))
        write_audit(out, args.out)
        for r in out["judges"]:
            j, acc = r["judge"], r["planted"]
            verdict = "qualified" if r["qualified"] else "REJECTED: replace this judge"
            print(
                f"  {j['id']} ({j['family']} {j['model']} @ {j['revision']}): "
                f"{_pct(acc['accuracy'])} of {acc['n']} plain Planted Claims "
                f"[{_pct(acc['ci95'][0])}, {_pct(acc['ci95'][1])}]  {verdict}"
            )
        _print_panel_cost(out)
        print(f"qualification: {args.out}")
        return 0 if out["complete"] and not out["rejected"] else 1

    pool, pipeline = [], []
    for t in args.transcripts:
        audit = read_json(t.with_suffix(".audit.json"))
        try:
            pool += candidates(load_transcript(t), audit)
        except ValueError as exc:
            parser.error(f"{t}: {exc}")
        judge = (audit.get("judgments") or {}).get("judge")
        pipeline.append({"extractor": audit.get("extractor"), "judge": judge})
    try:
        out = run_label(
            judges,
            pool,
            n=args.n,
            seed=args.seed,
            qualification=read_json(args.qualification),
            ledger=ledger,
            alpha_threshold=config.alpha_threshold,
            pipeline=pipeline,
        )
    except ValueError as exc:
        parser.error(str(exc))
    write_audit(out, args.out)
    print(f"{out['n']} Claims sampled from {len(pool)} across {len(out['strata'])} strata")
    if out["aborted"]:
        print(f"ABORTED ({out['aborted']['reason']}): {out['aborted']['detail']}")
    _print_panel_cost(out)
    print(f"labels: {args.out}")
    return 0 if out["complete"] else 1


VALIDATED_BY_LINE = "Label Panel: validated by an AI panel, not by humans."


def _print_panel_cost(out: dict[str, Any]) -> None:
    c = out["cost"]
    if c:
        print(
            f"Label Panel spend ${c['cost_usd']:.4f} ({c['calls']} calls) "
            f"+ ${c['held_usd']:.4f} held for calls without usage "
            f"of ${c['spend_cap_usd']:.2f} cap"
        )


def print_panel_report(out: dict[str, Any]) -> None:
    print(VALIDATED_BY_LINE)
    print(f"{out['n']} Claims; judges: {', '.join(j['id'] for j in out['judges'])}")
    print("  label        alpha   [95% CI]          pipeline vs majority  [95% CI]")
    for t, r in out["labels"].items():
        a, (alo, ahi) = r["alpha"], r["alpha_ci95"]
        p = r["pipeline_vs_majority"]
        alpha = "   -  " if a is None else f"{a:6.3f}"
        ci = "    -    " if alo is None else f"{alo:.2f}-{ahi:.2f}"
        flag = "  REVISE (alpha < threshold)" if r["revise"] else ""
        flag = "  (alpha undefined: too few or uniform labels)" if a is None else flag
        print(
            f"  {t:<11} {alpha}  [{ci}]  {_pct(p['accuracy'])} of {p['n']:<4}"
            f"[{_pct(p['ci95'][0])}, {_pct(p['ci95'][1])}]{flag}"
        )


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
    leader = game.config.leader
    if leader is not None:
        known = "Known" if leader.known else "Hidden"
        print(f"{known} Leader: {names[leader.seat]} ({game.state.player(leader.seat).role.value})")
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
