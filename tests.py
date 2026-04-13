"""
tests.py
DI Workbench v2 — core governance tests.

Run with:  python tests.py
Or:        pytest tests.py -v
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from engine.workflow_state import WorkflowState, WorkflowRecord
from engine.contract_validator import ContractValidator
from engine.diff_guard import DiffGuard, ChangeClass
from engine.decision_engine import DecisionEngine

# ── Fixtures ──────────────────────────────────────────────────────────────────

CONTRACT = {
    "artifact": "pattern_engine",
    "schema_locked": True,
    "locked_fields": [
        "output.score",
        "output.summary",
        "canonical_record.metrics.<n>.value",
    ],
    "forbidden_changes": [
        "rename_locked_field",
        "remove_locked_field",
        "weaken_output_contract",
        "collapse_pattern_and_insight_layers",
        "introduce_unapproved_scope",
        "replace_structured_output",
    ],
    "review_scope": [
        "missing_thresholds",
        "missing_definition",
        "edge_case_handling",
        "implementation_risk",
    ],
}


def _fresh_record() -> WorkflowRecord:
    r = WorkflowRecord(artifact="pattern_engine")
    r.transition(WorkflowState.ARCHITECTED, "test setup")
    r.transition(WorkflowState.UNDER_REVIEW, "test setup")
    return r


def _make_issue(
    iid="R1",
    itype="missing_definition",
    severity="medium",
    scope="allowed",
    target="trend_threshold",
    message="Threshold not defined",
    proposed_fix="Define 0.1 as minimum delta",
) -> dict:
    return {
        "id": iid,
        "type": itype,
        "severity": severity,
        "scope": scope,
        "target": target,
        "message": message,
        "proposed_fix": proposed_fix,
    }


cv = ContractValidator()
dg = DiffGuard()
de = DecisionEngine()

PASS = 0
FAIL = 0


def check(label: str, got, expected) -> None:
    global PASS, FAIL
    if got == expected:
        print(f"  PASS  {label}")
        PASS += 1
    else:
        print(f"  FAIL  {label}")
        print(f"        got:      {got!r}")
        print(f"        expected: {expected!r}")
        FAIL += 1


# ── Test 1: Malformed reviewer schema is rejected ─────────────────────────────

def test_malformed_review_rejected():
    print("\n── Test 1: Malformed reviewer schema is rejected ──")

    # Missing 'decision' field
    r = {"issues": []}
    res = cv.validate_review_schema(r)
    check("missing decision → invalid", res.valid, False)
    check("error mentions 'decision'", any("decision" in e for e in res.errors), True)

    # Invalid decision enum value
    r2 = {"decision": "MAYBE", "issues": []}
    res2 = cv.validate_review_schema(r2)
    check("bad decision enum → invalid", res2.valid, False)

    # Issue missing required fields
    r3 = {"decision": "REVISE", "issues": [{"id": "R1"}]}
    res3 = cv.validate_review_schema(r3)
    check("issue missing required fields → invalid", res3.valid, False)

    # Invalid severity
    r4 = {"decision": "REVISE", "issues": [_make_issue(severity="critical")]}
    res4 = cv.validate_review_schema(r4)
    check("bad severity enum → invalid", res4.valid, False)

    # Invalid scope
    r5 = {"decision": "REVISE", "issues": [_make_issue(scope="maybe")]}
    res5 = cv.validate_review_schema(r5)
    check("bad scope enum → invalid", res5.valid, False)

    # Invalid type
    r6 = {"decision": "REVISE", "issues": [_make_issue(itype="opinion")]}
    res6 = cv.validate_review_schema(r6)
    check("bad type enum → invalid", res6.valid, False)

    # Engine rejects schema-invalid review and never reaches diff guard
    dec = de.process_review(_fresh_record(), CONTRACT, {"issues": []})
    check("engine action = REVIEW_SCHEMA_INVALID", dec.action, "REVIEW_SCHEMA_INVALID")
    check("engine state = BLOCKED", dec.next_state, WorkflowState.BLOCKED)

    # Valid review passes
    valid = {"decision": "REVISE", "issues": [_make_issue()], "summary": "ok"}
    res7 = cv.validate_review_schema(valid)
    check("valid review passes schema", res7.valid, True)


# ── Test 2: Forbidden schema-change review is classified as forbidden ──────────

def test_forbidden_schema_change_classified():
    print("\n── Test 2: Forbidden schema-change review classified as forbidden ──")

    # Self-reported forbidden scope
    r = {"decision": "REVISE", "issues": [_make_issue(scope="forbidden")], "summary": "x"}
    diff = dg.classify(r, CONTRACT)
    check("self-reported forbidden → REVISE_FORBIDDEN", diff.overall_decision, "REVISE_FORBIDDEN")
    check("classified as FORBIDDEN", diff.forbidden[0].classification, ChangeClass.FORBIDDEN)

    # Targets locked field
    r2 = {"decision": "REVISE", "issues": [_make_issue(target="output.score", scope="allowed")], "summary": "x"}
    diff2 = dg.classify(r2, CONTRACT)
    check("locked field target → REVISE_FORBIDDEN", diff2.overall_decision, "REVISE_FORBIDDEN")

    # contract_violation type
    r3 = {"decision": "REVISE", "issues": [_make_issue(itype="contract_violation")], "summary": "x"}
    diff3 = dg.classify(r3, CONTRACT)
    check("contract_violation type → REVISE_FORBIDDEN", diff3.overall_decision, "REVISE_FORBIDDEN")

    # Keyword: rename
    r4 = {"decision": "REVISE", "issues": [_make_issue(
        message="rename the field", proposed_fix="rename output.score to output.rating"
    )], "summary": "x"}
    diff4 = dg.classify(r4, CONTRACT)
    check("keyword 'rename' → REVISE_FORBIDDEN", diff4.overall_decision, "REVISE_FORBIDDEN")

    # Contextual rule: output target + simplification intent
    r5 = {"decision": "REVISE", "issues": [_make_issue(
        target="output_schema",
        message="response is too heavy",
        proposed_fix="streamline the output to return fewer fields"
    )], "summary": "x"}
    diff5 = dg.classify(r5, CONTRACT)
    check("contextual: output target + simplify intent → REVISE_FORBIDDEN", diff5.overall_decision, "REVISE_FORBIDDEN")

    # Contextual rule: layer target + merge intent
    r6 = {"decision": "REVISE", "issues": [_make_issue(
        target="insight_layer",
        message="two layers is excessive",
        proposed_fix="consolidate into a single module"
    )], "summary": "x"}
    diff6 = dg.classify(r6, CONTRACT)
    check("contextual: layer target + consolidate intent → REVISE_FORBIDDEN", diff6.overall_decision, "REVISE_FORBIDDEN")


# ── Test 3: Allowed missing-definition review is classified as allowed ─────────

def test_allowed_missing_definition_classified():
    print("\n── Test 3: Allowed missing-definition review classified as allowed ──")

    r = {"decision": "REVISE", "issues": [_make_issue()], "summary": "thresholds missing"}
    diff = dg.classify(r, CONTRACT)
    check("missing_definition → REVISE_ALLOWED", diff.overall_decision, "REVISE_ALLOWED")
    check("classified as ALLOWED", diff.allowed[0].classification, ChangeClass.ALLOWED)
    check("no forbidden changes", len(diff.forbidden), 0)

    # Multiple allowed issues
    r2 = {"decision": "REVISE", "issues": [
        _make_issue("R1", target="trend_threshold", message="No trend threshold defined",
                    proposed_fix="Define delta > 0.1 as threshold"),
        _make_issue("R2", target="insufficient_data_window",
                    message="Minimum data window not specified",
                    proposed_fix="Require at least 7 days of data"),
    ], "summary": "two gaps"}
    diff2 = dg.classify(r2, CONTRACT)
    check("two allowed issues → REVISE_ALLOWED", diff2.overall_decision, "REVISE_ALLOWED")
    check("allowed count = 2", len(diff2.allowed), 2)

    # Edge case handling is allowed
    r3 = {"decision": "REVISE", "issues": [_make_issue(
        target="null_input_handling",
        message="No null input handling specified",
        proposed_fix="Return empty result with error code on null input"
    )], "summary": "edge case"}
    diff3 = dg.classify(r3, CONTRACT)
    check("edge case handling → allowed", diff3.allowed[0].classification, ChangeClass.ALLOWED)


# ── Test 4: Decision engine ignores reviewer decision field ────────────────────

def test_decision_engine_ignores_reviewer_decision():
    print("\n── Test 4: Decision engine ignores reviewer decision field ──")

    # Reviewer says APPROVE but has high-severity implementation_risk → BLOCKED
    r_fake_approve = {"decision": "APPROVE", "issues": [_make_issue(
        itype="implementation_risk", severity="high",
        target="data_pipeline", message="Race condition possible",
        proposed_fix="Add mutex locking"
    )], "summary": "fine"}
    dec = de.process_review(_fresh_record(), CONTRACT, r_fake_approve)
    check("fake APPROVE with high risk → BLOCKED", dec.next_state, WorkflowState.BLOCKED)

    # Reviewer says BLOCKED but has only low allowed issues → REVIEW_REVISE_ALLOWED
    r_fake_blocked = {"decision": "BLOCKED", "issues": [_make_issue(
        severity="low", target="window_size",
        message="Window size not defined", proposed_fix="Define 7-day window"
    )], "summary": "blocked for no reason"}
    dec2 = de.process_review(_fresh_record(), CONTRACT, r_fake_blocked)
    check("fake BLOCKED with low allowed → REVIEW_REVISE_ALLOWED",
          dec2.next_state, WorkflowState.REVIEW_REVISE_ALLOWED)

    # Reviewer says REVISE but issues is empty → APPROVED
    r_fake_revise = {"decision": "REVISE", "issues": [], "summary": "revise nothing"}
    dec3 = de.process_review(_fresh_record(), CONTRACT, r_fake_revise)
    check("fake REVISE with empty issues → APPROVED", dec3.next_state, WorkflowState.APPROVED)

    # Reviewer says APPROVE with allowed issues → REVISE_ALLOWED (not approve)
    r_approve_with_issues = {"decision": "APPROVE", "issues": [_make_issue()], "summary": "approve with issues"}
    dec4 = de.process_review(_fresh_record(), CONTRACT, r_approve_with_issues)
    check("APPROVE with allowed issues → REVIEW_REVISE_ALLOWED",
          dec4.next_state, WorkflowState.REVIEW_REVISE_ALLOWED)


# ── Test 5: Spec revision that removes locked token is rejected ────────────────

def test_spec_revision_locked_token_removal_rejected():
    print("\n── Test 5: Spec revision removing locked token is rejected ──")

    prev = (
        "# Pattern Engine Spec\n"
        "## Output Schema\n"
        "output.score and output.summary are required fields.\n"
        "canonical_record.metrics stores all metric values.\n"
        "## Layer Separation\n"
        "Pattern layer and insight layer must remain separate.\n"
        "## Error Handling\n"
        "Return structured error on failure.\n"
    )

    # Clean revision — adds threshold, keeps all locked tokens
    good = (
        "# Pattern Engine Spec\n"
        "## Output Schema\n"
        "output.score and output.summary are required fields.\n"
        "canonical_record.metrics stores all metric values.\n"
        "## Layer Separation\n"
        "Pattern layer and insight layer must remain separate.\n"
        "## Thresholds\n"
        "Trend threshold: delta > 0.1 over 7-day window.\n"
        "## Error Handling\n"
        "Return structured error on failure with error_code field.\n"
    )
    res = cv.validate_spec_revision(prev, good, CONTRACT)
    check("clean revision passes", res.valid, True)

    # Revision removes 'score' token
    missing_score = prev.replace("output.score and ", "")
    res2 = cv.validate_spec_revision(prev, missing_score, CONTRACT)
    check("removal of 'score' token → invalid", res2.valid, False)
    check("error mentions 'score'", any("score" in e for e in res2.errors), True)

    # Revision removes 'canonical_record' token
    missing_canonical = prev.replace("canonical_record.", "cr.")
    res3 = cv.validate_spec_revision(prev, missing_canonical, CONTRACT)
    check("removal of 'canonical_record' token → invalid", res3.valid, False)

    # Revision that is massively shorter
    tiny = "# Spec\nDo the pattern thing."
    res4 = cv.validate_spec_revision(prev, tiny, CONTRACT)
    check("massive length reduction → invalid", res4.valid, False)
    check("length error present", any("shorter" in e for e in res4.errors), True)

    # process_revision on DecisionEngine returns None for clean
    viol = de.process_revision(prev, good, CONTRACT, _fresh_record())
    check("clean revision: process_revision = None", viol, None)

    # process_revision returns BLOCKED for locked token removal
    viol2 = de.process_revision(prev, missing_score, CONTRACT, _fresh_record())
    check("token removal: next_state = BLOCKED", viol2.next_state, WorkflowState.BLOCKED)
    check("token removal: action = REVISION_CONTRACT_VIOLATION",
          viol2.action, "REVISION_CONTRACT_VIOLATION")


# ── Runner ────────────────────────────────────────────────────────────────────

def run_all():
    test_malformed_review_rejected()
    test_forbidden_schema_change_classified()
    test_allowed_missing_definition_classified()
    test_decision_engine_ignores_reviewer_decision()
    test_spec_revision_locked_token_removal_rejected()

    print(f"\n{'═'*52}")
    print(f"  {PASS} passed   {FAIL} failed")
    if FAIL == 0:
        print("  ALL TESTS PASSED ✓")
    else:
        print("  FAILURES — see above")
    return FAIL == 0


if __name__ == "__main__":
    ok = run_all()
    sys.exit(0 if ok else 1)
