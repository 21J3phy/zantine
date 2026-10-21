"""The **Claim Audit**: a post-game analysis step over a transcript.

Pipeline (``run_audit``):

1. ``record.build_record``: rebuild ground truth and each Seat's own record from
   the transcript (``event`` and ``observation`` records only).
2. An extractor (``extractors.ClaimExtractor``) turns each **Meeting** statement,
   passed as quoted data, into structured **Claims**; ``claims.parse_payload``
   validates the output and ``claims.resolve`` maps names to Seats.
3. ``check.check_claim`` marks each Claim true, false or unverifiable in plain
   code, and splits false Claims into **Lies** (with proof the speaker knew) and
   **Misstatements**.
4. ``report.run_audit`` adds per-Seat **Reliability**, Lie Rate and
   Misstatement Rate. ``zantine audit <transcript>`` runs it from the CLI.
"""

from zantine.audit.check import Verdict, check_claim
from zantine.audit.claims import (
    CLAIMS_SCHEMA,
    Claim,
    ClaimDraft,
    ExtractionError,
    ExtractionInput,
    parse_payload,
    resolve,
)
from zantine.audit.extractors import ClaimExtractor, PatternExtractor, ScriptedExtractor, claim
from zantine.audit.record import GameRecord, build_record
from zantine.audit.report import (
    extraction_input,
    load_transcript,
    metrics,
    run_audit,
    write_audit,
)

__all__ = [
    "CLAIMS_SCHEMA",
    "Claim",
    "ClaimDraft",
    "ClaimExtractor",
    "ExtractionError",
    "ExtractionInput",
    "GameRecord",
    "PatternExtractor",
    "ScriptedExtractor",
    "Verdict",
    "build_record",
    "check_claim",
    "claim",
    "extraction_input",
    "load_transcript",
    "metrics",
    "parse_payload",
    "resolve",
    "run_audit",
    "write_audit",
]
