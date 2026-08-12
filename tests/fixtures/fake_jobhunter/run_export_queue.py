"""Fake JobHunter entry point for WQ-6 orchestration tests.

This script mimics the real ``run_export_queue.py`` contract:
- Accepts ``--output <path>`` (and optional extra args for test control).
- Writes the queue file atomically (temp file + os.replace).
- Exits 0 on success, non-zero on failure.
- Never makes network calls, never accesses real ATS sites.

Test control args (all optional):
- ``--delay <seconds>`` — sleep before writing (simulates long scan).
- ``--fail`` — exit with code 1 without writing the queue.
- ``--jobs <n>`` — number of fake jobs to write (default 1).
- ``--secret-leak`` — print a fake secret line to stdout (tests the filter).

Usage by tests:
    python tests/fixtures/fake_jobhunter/run_export_queue.py \
        --output /tmp/queue.jsonl --jobs 2
"""

from __future__ import annotations

import argparse
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
    # Compute the deterministic application_id the same way UAA does:
    # sha256(platform:external_id) when external_id is set.
    import hashlib

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
    parser = argparse.ArgumentParser(description="Fake JobHunter for WQ-6 tests")
    parser.add_argument("--output", type=str, required=True, help="Output JSONL path")
    parser.add_argument("--delay", type=float, default=0.0, help="Sleep before writing")
    parser.add_argument("--fail", action="store_true", help="Exit 1 without writing")
    parser.add_argument("--jobs", type=int, default=1, help="Number of fake jobs")
    parser.add_argument("--secret-leak", action="store_true", help="Print a fake secret")
    parser.add_argument(
        "--volume", type=int, default=0, help="Bytes of output to write to stdout and stderr"
    )
    parser.add_argument(
        "--timeout-test", action="store_true", help="Sleep 60s to test timeout cleanup"
    )
    parser.add_argument(
        "--evaluations", type=str, default=None, help="Ignored (compat with real entry point)"
    )
    parser.add_argument(
        "--pipeline", type=str, default=None, help="Ignored (compat with real entry point)"
    )
    parser.add_argument(
        "--profile", type=str, default=None, help="Ignored (compat with real entry point)"
    )
    parser.add_argument(
        "--threshold", type=float, default=None, help="Ignored (compat with real entry point)"
    )
    args = parser.parse_args()

    if args.secret_leak:
        print("OPENROUTER_API_KEY=sk-or-v1-fake-secret-do-not-leak")

    if args.timeout_test:
        print("FAKE JobHunter: entering 60s sleep for timeout test...", flush=True)
        time.sleep(60)
        return 0

    if args.delay > 0:
        time.sleep(args.delay)

    if args.fail:
        print("FAKE JobHunter: --fail flag set, exiting 1", file=sys.stderr)
        return 1

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)

    # Write atomically: temp file in the same directory, then os.replace.
    jobs = [_make_job(i, output.parent) for i in range(args.jobs)]
    lines = [json.dumps(job, separators=(",", ":")) for job in jobs]
    content = "\n".join(lines) + ("\n" if lines else "")

    fd, tmp_path = tempfile.mkstemp(dir=str(output.parent), prefix=".uaa_fake_jh_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.replace(tmp_path, output)
    except OSError:
        # Clean up the temp file if replace failed.
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    print(f"FAKE JobHunter: wrote {len(jobs)} job(s) to {output}")

    # High-volume output test: write enough to fill the OS pipe buffer
    # (typically 64KB on Linux) to test concurrent draining.
    if args.volume > 0:
        chunk = "B" * 4096 + "\n"
        written = 0
        while written < args.volume:
            to_write = min(len(chunk), args.volume - written)
            sys.stdout.write(chunk[:to_write])
            sys.stdout.flush()
            sys.stderr.write(chunk[:to_write])
            sys.stderr.flush()
            written += to_write

    return 0


if __name__ == "__main__":
    sys.exit(main())
