"""
Eval runner for JD Intelligence pipeline.

Usage (from project root):
    python eval/run_eval.py

    # Run one specific JD:
    python eval/run_eval.py --id jd_02_gen_ai_engineer

    # Skip LLM calls (check structure only):
    python eval/run_eval.py --dry-run

    # Verbose: print full payload per JD
    python eval/run_eval.py --verbose
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

# ── Make sure project root is on sys.path ─────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from agents.extractor import run_extractor
from core.decision import apply_stage2b_decisions
from core.cleaning import clean_jd_text
from core.pipeline import run_full_pipeline

# ── Paths ─────────────────────────────────────────────────────────────────────
EVAL_DIR   = Path(__file__).resolve().parent
JDS_DIR    = EVAL_DIR / "jds"
TRUTH_FILE = EVAL_DIR / "ground_truth.json"


# ── Colours (terminal) ────────────────────────────────────────────────────────
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
RESET  = "\033[0m"
BOLD   = "\033[1m"


def ok(msg: str)   -> str: return f"{GREEN}✓{RESET} {msg}"
def fail(msg: str) -> str: return f"{RED}✗{RESET} {msg}"
def warn(msg: str) -> str: return f"{YELLOW}~{RESET} {msg}"


# ── Individual checks ─────────────────────────────────────────────────────────

def check_recommendation(payload: Any, expected: dict) -> tuple[bool, str]:
    got  = payload.recommendation
    want = expected["expected_recommendation"]
    if got == want:
        return True, ok(f"recommendation = {got}")
    return False, fail(f"recommendation: got {got!r}, want {want!r}")


def check_score(payload: Any, expected: dict) -> tuple[bool, str]:
    score = payload.match_score
    lo, hi = expected["expected_match_score_range"]
    if score is None:
        return False, fail(f"match_score is None")
    if lo <= score <= hi:
        return True, ok(f"match_score = {score}%  (expected {lo}–{hi}%)")
    return False, fail(f"match_score = {score}%  (expected {lo}–{hi}%)")


def _canonicalize_skill(skill: str) -> str:
    """Convert skill to canonical form for comparison."""
    from core.skill_canonicalizer import canonicalize_skill
    canonical = canonicalize_skill(skill)
    return canonical if canonical else skill.lower()


def check_gaps(payload: Any, expected: dict) -> tuple[bool, str]:
    gaps_lower   = {_canonicalize_skill(g) for g in (payload.skill_gaps or [])}
    must_include = expected.get("expected_gaps_include", [])
    missing      = [s for s in must_include if _canonicalize_skill(s) not in gaps_lower]
    if not missing:
        return True, ok(f"gaps contain all expected: {must_include or '(none required)'}")
    return False, fail(f"gaps missing: {missing}  |  got: {list(payload.skill_gaps or [])[:8]}")


def check_matched(payload: Any, expected: dict) -> tuple[bool, str]:
    matched_lower = {_canonicalize_skill(m) for m in (payload.matched_skills or [])}
    must_include  = expected.get("expected_matched_include", [])
    missing       = [s for s in must_include if _canonicalize_skill(s) not in matched_lower]
    if not missing:
        return True, ok(f"matched contains all expected: {must_include or '(none required)'}")
    return False, fail(f"matched missing: {missing}  |  got: {list(payload.matched_skills or [])[:8]}")


def check_extraction(payload: Any, expected: dict) -> tuple[bool, str]:
    """Light structural checks — role extracted, not totally empty."""
    issues = []
    if not payload.role:
        issues.append("role is empty")
    if not payload.required_skills and not payload.required_one_of:
        issues.append("both required_skills and required_one_of are empty")
    if issues:
        return False, fail(f"extraction: {'; '.join(issues)}")
    return True, ok(f"extraction ok — role={payload.role!r}  req={len(payload.required_skills)}  pools={len(payload.required_one_of)}")


# ── Per-JD runner ─────────────────────────────────────────────────────────────

def run_case(case: dict, verbose: bool = False, dry_run: bool = False) -> dict:
    jd_file = JDS_DIR / case["file"]

    if not jd_file.exists():
        return {
            "id": case["id"],
            "file": case["file"],
            "status": "SKIP",
            "reason": f"file not found: {jd_file}",
            "checks": [],
            "passed": 0,
            "total": 0,
        }

    jd_raw = jd_file.read_text(encoding="utf-8")

    try:
        jd_text = clean_jd_text(jd_raw)
    except ValueError as e:
        return {
            "id": case["id"],
            "file": case["file"],
            "status": "ERROR",
            "reason": f"cleaning failed: {e}",
            "checks": [],
            "passed": 0,
            "total": 0,
        }

    if dry_run:
        return {
            "id": case["id"],
            "file": case["file"],
            "status": "DRY_RUN",
            "reason": "skipped LLM calls",
            "checks": [],
            "passed": 0,
            "total": 0,
        }

    # Run full pipeline (extractor → canonicalization → decision → verifier → retry)
    t0 = time.time()
    try:
        result = run_full_pipeline(
            jd_text=jd_text,
            user_skills=case.get("resume_skills", []),
            request_id=f"eval_{case['id']}",
            max_extractor_attempts=1,  # Eval uses single attempt for speed
            apply_verifier=True,
            apply_smart_retry=True,
        )
        payload = result.payload
        elapsed_extract = round(time.time() - t0, 1)
    except Exception as e:
        return {
            "id": case["id"],
            "file": case["file"],
            "status": "ERROR",
            "reason": f"pipeline crashed: {e}",
            "checks": [],
            "passed": 0,
            "total": 0,
        }

    elapsed_total = round(time.time() - t0, 1)

    # Run checks
    checks = [
        check_extraction(payload, case),
        check_recommendation(payload, case),
        check_score(payload, case),
        check_gaps(payload, case),
        check_matched(payload, case),
    ]

    passed = sum(1 for ok_flag, _ in checks if ok_flag)
    total  = len(checks)

    if verbose:
        print(f"\n  {CYAN}Payload:{RESET}")
        print(f"    role            = {payload.role}")
        print(f"    required_skills = {payload.required_skills}")
        print(f"    required_one_of = {payload.required_one_of}")
        print(f"    matched_skills  = {payload.matched_skills}")
        print(f"    skill_gaps      = {payload.skill_gaps}")
        print(f"    match_score     = {payload.match_score}")
        print(f"    recommendation  = {payload.recommendation}")
        print(f"    confidence      = {payload.confidence}")
        print(f"    experience_req  = {payload.experience_required}")
        print(f"    work_mode       = {payload.work_mode}")

    return {
        "id": case["id"],
        "file": case["file"],
        "status": "PASS" if passed == total else "FAIL",
        "elapsed_s": elapsed_total,
        "checks": checks,
        "passed": passed,
        "total": total,
        "payload_summary": {
            "role": payload.role,
            "required_skills": payload.required_skills,
            "required_one_of": payload.required_one_of,
            "match_score": payload.match_score,
            "recommendation": payload.recommendation,
            "confidence": payload.confidence,
        }
    }


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="JD Intelligence eval runner")
    parser.add_argument("--id",      help="Run only this case ID")
    parser.add_argument("--verbose", action="store_true", help="Print full payload")
    parser.add_argument("--dry-run", action="store_true", help="Skip LLM calls")
    args = parser.parse_args()

    cases = json.loads(TRUTH_FILE.read_text())

    if args.id:
        cases = [c for c in cases if c["id"] == args.id]
        if not cases:
            print(f"{RED}No case found with id={args.id!r}{RESET}")
            sys.exit(1)

    print(f"\n{BOLD}JD Intelligence Eval — {len(cases)} cases{RESET}")
    print(f"JDs dir:     {JDS_DIR}")
    print(f"Ground truth: {TRUTH_FILE}\n")
    print("─" * 60)

    results = []
    for case in cases:
        print(f"\n{BOLD}[{case['id']}]{RESET}  {case['file']}")
        print(f"  expected → {case['expected_recommendation']}  score {case['expected_match_score_range']}")

        result = run_case(case, verbose=args.verbose, dry_run=args.dry_run)
        results.append(result)

        if result["status"] in ("ERROR", "SKIP", "DRY_RUN"):
            print(f"  {YELLOW}{result['status']}{RESET}: {result['reason']}")
            continue

        for ok_flag, msg in result["checks"]:
            print(f"  {msg}")

        color  = GREEN if result["status"] == "PASS" else RED
        timing = f"  ({result.get('elapsed_s', '?')}s)"
        print(f"  {color}{BOLD}{result['status']}{RESET}  {result['passed']}/{result['total']} checks{timing}")

    # Summary
    print("\n" + "─" * 60)
    ran    = [r for r in results if r["status"] in ("PASS", "FAIL")]
    passed = [r for r in ran if r["status"] == "PASS"]
    failed = [r for r in ran if r["status"] == "FAIL"]
    skipped = [r for r in results if r["status"] in ("ERROR", "SKIP", "DRY_RUN")]

    print(f"\n{BOLD}Results: {len(passed)}/{len(ran)} passed{RESET}")
    if passed:
        print(f"  {GREEN}Passed:{RESET} {', '.join(r['id'] for r in passed)}")
    if failed:
        print(f"  {RED}Failed:{RESET} {', '.join(r['id'] for r in failed)}")
    if skipped:
        print(f"  {YELLOW}Skipped:{RESET} {', '.join(r['id'] for r in skipped)}")

    # Final PASS/FAIL banner
    print("\n" + "=" * 60)
    if not failed:
        print(f"{GREEN}{BOLD}  ALL TESTS PASSED ✓{RESET}")
    else:
        print(f"{RED}{BOLD}  TEST RUN FAILED ✗ ({len(failed)} failure(s)){RESET}")
    print("=" * 60 + "\n")

    # Save results to file
    out_file = EVAL_DIR / "last_run.json"
    out_file.write_text(json.dumps(results, indent=2, default=str))
    print(f"  Full results saved → {out_file}\n")

    sys.exit(0 if not failed else 1)


if __name__ == "__main__":
    main()
