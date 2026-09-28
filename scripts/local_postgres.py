"""Start project-local PostgreSQL with SCRAM auth, bound to loopback only."""

import os
import secrets
import subprocess
from pathlib import Path

import psycopg
from psycopg import sql

ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / ".local"
BIN = LOCAL / "postgres" / "Library" / "bin"
DATA = LOCAL / "pgdata"
PORT = 55432


def main():
    if not (BIN / "initdb.exe").exists():
        raise SystemExit(
            "先运行 conda create --prefix .local/postgres -c conda-forge postgresql=17 -y"
        )
    LOCAL.mkdir(exist_ok=True)
    password_file = LOCAL / "pg-admin-password"
    if not password_file.exists():
        password_file.write_text(secrets.token_urlsafe(32), encoding="utf-8")
    admin_password = password_file.read_text(encoding="utf-8").strip()
    child_env = dict(os.environ, PATH=str(BIN) + os.pathsep + os.environ["PATH"])

    def pg(command, *args, check=True):
        log_path = LOCAL / "pg-command.log"
        with log_path.open("w", encoding="utf-8") as log:
            result = subprocess.run(
                [str(BIN / (command + ".exe")), *map(str, args)],
                env=child_env,
                check=check,
                stdout=log,
                stderr=log,
                stdin=subprocess.DEVNULL,
                timeout=120,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        result.stdout = log_path.read_text(encoding="utf-8", errors="replace")
        result.stderr = result.stdout
        return result

    if not (DATA / "PG_VERSION").exists():
        result = pg(
            "initdb",
            "-D",
            DATA,
            "-U",
            "policy_dev_root",
            "--auth=scram-sha-256",
            "--pwfile",
            password_file,
            "--encoding=UTF8",
            "--locale=C",
            check=False,
        )
        if result.returncode:
            raise SystemExit(result.stderr or result.stdout)
    status = pg("pg_ctl", "status", "-D", DATA, check=False)
    if status.returncode:
        started = pg(
            "pg_ctl",
            "start",
            "-D",
            DATA,
            "-l",
            LOCAL / "postgres.log",
            "-o",
            f"-p {PORT} -h 127.0.0.1",
            "-w",
            check=False,
        )
        if started.returncode:
            raise SystemExit(started.stderr or started.stdout)

    env_path = ROOT / ".env"
    if env_path.exists():
        print("Project PostgreSQL is running at 127.0.0.1:55432; existing .env preserved.")
        return
    app_password = secrets.token_urlsafe(32)
    with psycopg.connect(
        host="127.0.0.1",
        port=PORT,
        user="policy_dev_root",
        password=admin_password,
        dbname="postgres",
        autocommit=True,
    ) as conn:
        if not conn.execute("SELECT 1 FROM pg_roles WHERE rolname = 'policy_app'").fetchone():
            conn.execute(
                sql.SQL("CREATE ROLE policy_app LOGIN CREATEDB PASSWORD {}").format(
                    sql.Literal(app_password)
                )
            )
        else:
            raise SystemExit(
                "policy_app already exists; restore the matching .env instead of resetting credentials."
            )
        conn.execute("CREATE DATABASE policy_app OWNER policy_app")
    db_url = f"postgresql://policy_app:{app_password}@127.0.0.1:{PORT}/policy_app"
    with psycopg.connect(db_url, autocommit=True) as conn:
        conn.execute("CREATE SCHEMA IF NOT EXISTS langgraph AUTHORIZATION policy_app")
    env_path.write_text(
        f"DEBUG=true\nDJANGO_SECRET_KEY={secrets.token_urlsafe(48)}\nDATABASE_URL={db_url}\n"
        f"CHECKPOINT_DATABASE_URL={db_url}?options=-csearch_path%3Dlanggraph\n"
        "DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1\n"
        "CSRF_TRUSTED_ORIGINS=http://127.0.0.1:3000,http://localhost:3000\n"
        "LOCAL_WORKER=true\nAI_BASE_URL=\nAI_API_KEY=\nAI_MODEL=\n",
        encoding="utf-8",
    )
    print(
        "Project PostgreSQL initialized at 127.0.0.1:55432; random credentials saved in ignored .env."
    )


if __name__ == "__main__":
    main()
