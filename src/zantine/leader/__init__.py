"""The v1.4 Leader experiment (issue #24): schedule, cross-game results and graphs.

The Leader condition itself is ``GameConfig.leader`` (prompt lines only, see
``docs/rules.md``); per-game Compliance and time to eject come from the
judgments (``judge/leader.py``). This package plans the 196 games
(``schedule``; ``zantine schedule --experiment leader``), pools audited games
into per-condition estimates with ``analysis/bootstrap.py`` intervals
(``analysis``) and draws them (``graphs``).
"""
