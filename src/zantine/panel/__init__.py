"""The **Label Panel** (issue #34): three AI judges that validate the Claim Audit.

Three judges from three different model families, none from a **Model Pool**
lab, the extractor's family or the judgment model's lab (``config.py``,
``judges.toml``; the models themselves are chosen in #19), label real-game
**Claims**: truth (true / false / unverifiable), Claim or hedge, challenged or
not, and whom the statement pushes to eject.

1. ``anonymize.item_view``: judges see Seat tokens (``P1`` ..) instead of names,
   with model names scrubbed from speech and no role, controller or model.
2. ``run.qualify``: each judge must score >= 0.95 on plain **Planted Claims**.
3. ``run.label``: a seeded sample stratified by (Model, role), labeled by every
   qualified judge (``purpose="label_panel"`` on the run's ``Ledger``).
4. ``run.report``: Krippendorff's alpha per label type and the pipeline's
   accuracy against the panel's majority label, with 95% intervals.

Results are validated by an AI panel, not by humans.
"""

from zantine.panel.anonymize import View, item_view
from zantine.panel.config import (
    JudgeSpec,
    PanelConfig,
    PanelConfigError,
    load_panel,
    validate_panel,
)
from zantine.panel.items import (
    Candidate,
    PlantedItem,
    PlantedReport,
    PlantedSource,
    candidates,
    stratified_sample,
)
from zantine.panel.judges import LLMPanelJudge, PanelJudge, ScriptedPanelJudge
from zantine.panel.prompt import LABEL_TYPES, LabelError, parse_labels, render_input
from zantine.panel.run import VALIDATED_BY, label, qualify, report
from zantine.panel.stats import krippendorff_alpha, wilson

__all__ = [
    "LABEL_TYPES",
    "VALIDATED_BY",
    "Candidate",
    "JudgeSpec",
    "LLMPanelJudge",
    "LabelError",
    "PanelConfig",
    "PanelConfigError",
    "PanelJudge",
    "PlantedItem",
    "PlantedReport",
    "PlantedSource",
    "ScriptedPanelJudge",
    "View",
    "candidates",
    "item_view",
    "krippendorff_alpha",
    "label",
    "load_panel",
    "parse_labels",
    "qualify",
    "render_input",
    "report",
    "stratified_sample",
    "validate_panel",
    "wilson",
]
