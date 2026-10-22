"""Results analysis: metrics, cluster bootstrap, Sabotage Damage and graphs (#18, #33).

Reads run directories (``manifest.json`` + transcripts) only; never imports the
engine. ``zantine report <run-dir>`` writes ``<run-dir>/report/``. Definitions:
``docs/metrics.md``.

- ``metrics.py``: per-game and per-Seat rows from transcripts (+ Claim Audit hook)
- ``bootstrap.py``: game-level cluster bootstrap, stratified by game kind
- ``sabotage.py``: Sabotage Damage, a lineup fixed-effects regression
- ``report.py``: every estimate, ``metrics.json``, ``progress.md``, the CLI
- ``graphs.py``: the charts (matplotlib, imported only when drawing)
- ``verification.py``: the v1.1 Verification Protocol experiment (#37)
"""
