"""Silent Windows entry point used by Universal AutoApplier Dashboard.exe."""

from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
LOG_PATH = PROJECT_ROOT / ".uaa_data" / "dashboard_server.log"


def main() -> int:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8", buffering=1) as log_file:
        sys.stdout = log_file
        sys.stderr = log_file
        sys.path.insert(0, str(PROJECT_ROOT / "src"))
        os.environ.setdefault("UAA_HOST", "127.0.0.1")
        os.environ.setdefault("UAA_PORT", "18742")
        try:
            from universal_auto_applier.__main__ import main as run

            return run([])
        except Exception:
            traceback.print_exc(file=log_file)
            return 1


if __name__ == "__main__":
    raise SystemExit(main())
