"""Run the same application tests against PostgreSQL, plus checkpoint persistence."""

import os
import subprocess
import sys
from pathlib import Path

import environ

root = Path(__file__).resolve().parents[1]
base_temp = root.parent / ".policy-platform-pytest"
environ.Env.read_env(root / ".env")
db_url = os.environ.get("DATABASE_URL", "")
if not db_url.startswith("postgres"):
    raise SystemExit("Configure a dedicated development PostgreSQL DATABASE_URL first.")
env = dict(os.environ, TEST_DATABASE_URL=db_url, RUN_POSTGRES_TESTS="1", PYTHONUTF8="1")
raise SystemExit(
    subprocess.call(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            f"--basetemp={base_temp}",
        ],
        cwd=root,
        env=env,
    )
)
