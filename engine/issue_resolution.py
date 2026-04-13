"""
issue_resolution.py — Deterministic issue resolution tracking for Slice B

Classifies each reviewer issue from a prior run against the current run's
patches and review findings.

Fix 4 — Matching strategy (two-pass):
  Pass 1 — issue_id exact match (primary, fast).
  Pass 2 — type + target fuzzy match for issues whose IDs changed between runs
            (normalised lowercase, strips whitespace before comparison).

Regression detection does NOT rely on prior.issue_resolutions (a derived field
that is never persisted). Instead it uses a set of issue fingerprints from the
prior review that were absent from that review's patch list — meaning they were
never patched before and now reappear with a *different* id/signature. This is
conservative: we only call it a regression if the same type+target pair that
was fully absent in the prior run now appears in the current run while also
having been absent from prior issues entirely.

Resolution statuses:
  resolved          patch exists AND issue gone from current review (by id or type+target)
  claimed_addressed patch exists AND issue still present in current review
  unaddressed       no patch references this issue by id or type+target
  regressed         issue absent in prior review, present in current, previously patched
"""

from __future__ import annotations

from .run_record import (
    RunRecord,
    ReviewIssue,
    IssueResolutionRecord,
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _fingerprint(iss: ReviewIssue) -> str:
    """Stable type+target fingerprint used as fallback match key."""
    t      = (iss.type   or "unknown").strip().lower()
    target = (iss.target or "spec"   ).strip().lower()
    return f"{t}::{target}"


def _build_id_index(issues: list[ReviewIssue]) -> dict[str, ReviewIssue]:
    return {iss.id: iss for iss in issues}


def _build_fp_index(issues: list[ReviewIssue]) -> dict[str, ReviewIssue]:
    """First issue wins on fingerprint collision (deterministic)."""
    idx: dict[str, ReviewIssue] = {}
    for iss in issues:
        fp = _fingerprint(iss)
        if fp not in idx:
            idx[fp] = iss
    return idx


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def compute_resolutions(
    current: RunRecord,
    prior: RunRecord,
) -> list[IssueResolutionRecord]:
    """
    Compare current run against prior run and classify every prior issue.

    Two-pass matching (Fix 4):
      1. Exact issue_id match.
      2. type+target fingerprint match for issues whose ID changed.

    Regression detection (Fix 4):
      A current issue is a regression if:
        - It is not present in prior issues (by id OR fingerprint), AND
        - Its type+target fingerprint matches a patch in the current run
          (meaning someone tried to fix something like it before), AND
        - It appeared with a different id/fingerprint than all prior issues.
      This avoids depending on a prior derived resolutions list.
    """
    prior_issues   = prior.review_result.issues
    current_issues = current.review_result.issues

    prior_id_idx  = _build_id_index(prior_issues)
    prior_fp_idx  = _build_fp_index(prior_issues)
    curr_id_idx   = _build_id_index(current_issues)
    curr_fp_idx   = _build_fp_index(current_issues)

    # Patch lookup: id and fingerprint of patched issues
    patch_ids: set[str] = {p.issue_id for p in current.revision_patches}
    # Fingerprint for each patch id — look up the issue by id in prior or current
    patch_fps: set[str] = set()
    for p_id in patch_ids:
        iss = prior_id_idx.get(p_id) or curr_id_idx.get(p_id)
        if iss:
            patch_fps.add(_fingerprint(iss))

    records: list[IssueResolutionRecord] = []
    matched_current_ids: set[str] = set()  # track which current issues were matched

    # --- Pass 1 & 2: classify every prior issue ---
    for prior_iss in prior_issues:
        iid = prior_iss.id
        fp  = _fingerprint(prior_iss)

        # Determine if a patch covers this issue (by id or fingerprint)
        patched = (iid in patch_ids) or (fp in patch_fps)

        # Determine if issue still present in current (by id or fingerprint)
        curr_by_id = curr_id_idx.get(iid)
        curr_by_fp = curr_fp_idx.get(fp)
        still_present = (curr_by_id is not None) or (curr_by_fp is not None)
        match_method  = "id" if curr_by_id else ("type_target" if curr_by_fp else "id")

        # Track which current issues we've matched so regression scan is cleaner
        if curr_by_id:
            matched_current_ids.add(curr_by_id.id)
        if curr_by_fp:
            matched_current_ids.add(curr_by_fp.id)

        if patched and not still_present:
            status = "resolved"
            notes  = (
                f"Patch found (match: {match_method}); "
                "issue no longer present in current review."
            )
        elif patched and still_present:
            status = "claimed_addressed"
            notes  = (
                f"Patch found (match: {match_method}); "
                "issue still present in current review."
            )
        else:
            status = "unaddressed"
            notes  = f"No patch references this issue (match attempted: {match_method})."

        records.append(IssueResolutionRecord(
            issue_id=iid,
            match_method=match_method,
            status=status,                   # type: ignore[arg-type]
            prior_run_id=prior.run_id,
            current_run_id=current.run_id,
            notes=notes,
        ))

    # --- Regression scan: current issues not seen in prior at all ---
    prior_all_fps = {_fingerprint(i) for i in prior_issues}
    prior_all_ids = {i.id for i in prior_issues}

    for curr_iss in current_issues:
        if curr_iss.id in matched_current_ids:
            continue  # already accounted for above

        fp = _fingerprint(curr_iss)

        # Only flag as regression if:
        #   - truly absent from prior (by both id and fingerprint)
        #   - AND its fingerprint was previously patched (someone tried to fix it)
        absent_from_prior = (
            curr_iss.id not in prior_all_ids
            and fp not in prior_all_fps
        )
        previously_patched = fp in patch_fps

        if absent_from_prior and previously_patched:
            records.append(IssueResolutionRecord(
                issue_id=curr_iss.id,
                match_method="new",
                status="regressed",           # type: ignore[arg-type]
                prior_run_id=prior.run_id,
                current_run_id=current.run_id,
                notes=(
                    "Issue type+target was previously patched but reappears "
                    "under a new id in the current review."
                ),
            ))

    return records


# ---------------------------------------------------------------------------
# Convenience accessor
# ---------------------------------------------------------------------------

def resolution_counts(resolutions: list[IssueResolutionRecord]) -> dict[str, int]:
    """Return status → count for quick summary display."""
    counts: dict[str, int] = {
        "unaddressed": 0,
        "claimed_addressed": 0,
        "resolved": 0,
        "regressed": 0,
    }
    for r in resolutions:
        counts[r.status] = counts.get(r.status, 0) + 1
    return counts
