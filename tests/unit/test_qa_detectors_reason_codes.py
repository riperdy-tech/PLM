"""Every P29 category maps to a classified reason code with a named emitter, and every
code P5 emits as a hold is classified (R4). Both directions are checked against the
architecture text and ``hold_classes.json``, not against a copy of either."""

from __future__ import annotations

import re
from pathlib import Path

from plm_platform.exceptions import HoldClass, load_hold_class_policy
from quality_gate.detectors.reason_codes import P5_HOLD_REASON_CODES, P29_CATEGORY_CODES

REPO_ROOT = Path(__file__).resolve().parents[2]
P5_EVALUATORS = REPO_ROOT / "services" / "quality_gate" / "evaluators"


def _p29_categories() -> list[str]:
    text = (REPO_ROOT / "docs" / "architecture.md").read_text(encoding="utf-8")
    section = text[text.index("# P29. Hard-stop publication criteria") :]
    start = section.index("```text") + len("```text")
    block = section[start : section.index("```", start)]
    return [line.strip() for line in block.strip().splitlines() if line.strip()]


def test_every_p29_category_in_the_architecture_has_a_reason_code() -> None:
    """Proves: the P29 list in docs/architecture.md and the code map agree exactly."""
    assert sorted(_p29_categories()) == sorted(P29_CATEGORY_CODES)


def test_every_p29_code_is_a_classified_hard_stop() -> None:
    """Proves: each category's codes are mapped in hold_classes.json as HARD_STOP and
    carry a p29_* category, so none falls through to 'unmapped'."""
    policy = load_hold_class_policy()
    for category, (codes, _phase) in P29_CATEGORY_CODES.items():
        for code in codes:
            classification = policy.classify(code)
            assert classification.mapped, (category, code)
            assert classification.rule.hold_class is HoldClass.HARD_STOP, code
            assert classification.rule.category.startswith("p29_"), code


def test_every_p29_category_classified_in_policy_is_on_the_map() -> None:
    """Proves the reverse direction: no p29_* code in hold_classes.json is missing from
    the P29 map (a code nobody claims to emit)."""
    mapped = {code for codes, _ in P29_CATEGORY_CODES.values() for code in codes}
    policy = load_hold_class_policy()
    p29_codes = {c for c, r in policy.reason_codes.items() if r.category.startswith("p29_")}
    assert p29_codes == mapped


def test_p5_emits_every_p29_code_it_claims() -> None:
    """Proves: for each category whose emitter is P5, the code appears in a P5 evaluator
    module (the behavioural emissions are in test_qa_detectors_gate.py), and is in the set
    the engine treats as a hold. DISTRIBUTOR_REJECTION and PLATFORM_CLAIM happen after
    the gate and are owned by P8 and P7."""
    source = "\n".join(p.read_text(encoding="utf-8") for p in P5_EVALUATORS.glob("*.py"))
    for category, (codes, phase) in P29_CATEGORY_CODES.items():
        if phase != "P5":
            assert phase in {"P7", "P8"}, category
            continue
        for code in codes:
            assert code in P5_HOLD_REASON_CODES, code
            assert re.search(rf"\b{code}\b", source), f"{code} is not emitted by a P5 evaluator"


def test_every_p5_hold_code_is_classified() -> None:
    """Proves: no code the engine quarantines on falls through to the 'unmapped' rule."""
    policy = load_hold_class_policy()
    unmapped = sorted(c for c in P5_HOLD_REASON_CODES if not policy.classify(c).mapped)
    assert unmapped == []
