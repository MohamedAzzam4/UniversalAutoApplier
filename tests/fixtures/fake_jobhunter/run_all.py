"""Fake full-workflow JobHunter producer for WQ-6 tests.

This script mimics the real ``run_all.py`` full workflow:
- Phase 1: SCAN — prints phase evidence, simulates scanning.
- Phase 2: EVALUATE/TAILOR — prints phase evidence, simulates evaluation.
- Phase 2.5: EXPORT — writes the queue file atomically (temp + os.replace).
- Phase 3: SUMMARY — prints completion summary.

It does NOT accept ``--output`` (matching run_all.py's contract). The queue
is written to ``data/application_queue.jsonl`` relative to the current
working directory (which the orchestrator sets to the JobHunter repo root).

Test control args (all optional):
- ``--delay <seconds>`` — sleep before each phase (simulates long workflow).
- ``--fail`` — exit with code 1 during the evaluate phase.
- ``--jobs <n>`` — number of fake jobs to write (default 1).
- ``--secret-leak`` — print a fake secret line to stdout (tests the filter).
- ``--volume <bytes>`` — write this many bytes to stdout and stderr (tests
  high-volume output / pipe deadlock prevention).
- ``--timeout-test`` — sleep for 60s (tests timeout cleanup).

Usage by tests:
    python tests/fixtures/fake_jobhunter/run_all.py --jobs 2
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import time
from pathlib import Path


def _make_job(index: int, output_dir: Path) -> dict[str, object]:
    """Build one fake ApplicationJob row matching UAA's JSONL contract."""
    external_id = f"fake-jh-job-{index}"
    platform = "greenhouse"
    url = f"https://boards.greenhouse.io/example/jobs/{external_id}"
    application_id = hashlib.sha256(f"{platform}:{external_id}".encode()).hexdigest()
    # Create dummy PDF files so the importer's existence check passes.
    cv_path = output_dir / f"cv-{index}.pdf"
    cover_path = output_dir / f"cover-{index}.pdf"
    cv_path.write_bytes(b"%PDF fake cv")
    cover_path.write_bytes(b"%PDF fake cover")
    return {
        "application_id": application_id,
        "platform": platform,
        "external_job_id": external_id,
        "source": "fake_jobhunter",
        "company": f"Fake Corp {index}",
        "title": f"Software Engineer {index}",
        "url": url,
        "verdict": "apply",
        "status": "ready_to_apply",
        "score": 4.0 + (index * 0.1),
        "cv_pdf": str(cv_path),
        "cover_letter_pdf": str(cover_path),
        "metadata": {
            "candidate_profile": {
                "first_name": "Test",
                "last_name": "User",
                "full_name": "Test User",
                "email": "test@example.com",
                "phone": "+49 123",
                "requires_sponsorship": False,
            },
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Fake JobHunter full workflow for WQ-6 tests")
    parser.add_argument("--delay", type=float, default=0.0, help="Sleep before each phase")
    parser.add_argument("--fail", action="store_true", help="Exit 1 during evaluate phase")
    parser.add_argument("--jobs", type=int, default=1, help="Number of fake jobs")
    parser.add_argument("--secret-leak", action="store_true", help="Print a fake secret")
    parser.add_argument(
        "--volume", type=int, default=0, help="Bytes of output to write to stdout and stderr"
    )
    parser.add_argument(
        "--timeout-test", action="store_true", help="Sleep 60s to test timeout cleanup"
    )
    # Accept (and ignore) args that the real run_all.py accepts, for compat.
    parser.add_argument("--dry-run", action="store_true", help="Ignored (compat)")
    parser.add_argument("--scan-only", action="store_true", help="Ignored (compat)")
    parser.add_argument("--threshold", type=float, default=None, help="Ignored (compat)")
    parser.add_argument("--german-policy", type=str, default=None, help="Ignored (compat)")
    args = parser.parse_args()

    if args.secret_leak:
        print("OPENROUTER_API_KEY=sk-or-v1-fake-secret-do-not-leak")

    # --- Phase 1: SCAN ---
    if args.delay > 0:
        time.sleep(args.delay)
    print("[SCAN] PHASE 1: Scanning for new jobs...", flush=True)
    print(f"[SCAN] Found {args.jobs} new job(s)", flush=True)

    if args.timeout_test:
        print("[SCAN] Entering 60s sleep for timeout test...", flush=True)
        time.sleep(60)
        # If we get here, the timeout didn't fire. Exit normally.
        return 0

    # --- Phase 2: EVALUATE / TAILOR ---
    if args.delay > 0:
        time.sleep(args.delay)
    print("[EVAL] PHASE 2: Evaluating jobs...", flush=True)
    if args.fail:
        print("[EVAL] FAKE FAILURE: --fail flag set", file=sys.stderr, flush=True)
        return 1
    print(f"[EVAL] Evaluated {args.jobs} job(s); tailoring CVs...", flush=True)
    print(f"[EVAL] Generated {args.jobs} tailored CV(s) and cover letter(s)", flush=True)

    # --- Phase 2.5: EXPORT (atomic) ---
    if args.delay > 0:
        time.sleep(args.delay)
    print("[EXPORT] PHASE 2.5: Publishing application_queue.jsonl...", flush=True)

    # Write to data/application_queue.jsonl (relative to CWD = JH repo root).
    # This matches run_all.py's default output path.
    output = Path("data/application_queue.jsonl")
    output.parent.mkdir(parents=True, exist_ok=True)

    # Resolve to absolute for the PDF paths so the UAA importer accepts them.
    output_dir = output.resolve().parent
    jobs = [_make_job(i, output_dir) for i in range(args.jobs)]
    lines = [json.dumps(job, separators=(",", ":")) for job in jobs]
    content = "\n".join(lines) + ("\n" if lines else "")

    # Atomic write: temp file in the same directory, then os.replace.
    fd, tmp_path = tempfile.mkstemp(dir=str(output.parent), prefix=".uaa_fake_jh_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.replace(tmp_path, output)
    except OSError:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    print(f"[EXPORT] Published {len(jobs)} job(s) to {output}", flush=True)

    # --- High-volume output test ---
    if args.volume > 0:
        chunk = "A" * 4096 + "\n"
        written = 0
        while written < args.volume:
            to_write = min(len(chunk), args.volume - written)
            sys.stdout.write(chunk[:to_write])
            sys.stdout.flush()
            sys.stderr.write(chunk[:to_write])
            sys.stderr.flush()
            written += to_write

    # --- Phase 3: SUMMARY ---
    print(f"[SUMMARY] Pipeline complete: {len(jobs)} job(s) exported", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
