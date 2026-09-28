"""Back up and restore the Docker Compose database and optional policy originals.

Only the explicitly requested ``restore --confirm`` command changes the active
database. Restores are built in a staging database before the name swap.
"""

import argparse
import json
import shutil
import subprocess
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKUP_ROOT = ROOT / ".local" / "backups" / "docker"
ORIGINALS = ROOT / ".local" / "originals"
ENV_FILE = ROOT / ".env.compose"
COMPOSE = ["docker", "compose", "--env-file", str(ENV_FILE), "-f", str(ROOT / "compose.yaml")]
APP_SERVICES = ("api", "worker", "beat")
RESTORE_SERVICES = (*APP_SERVICES, "backup")


def run(*arguments, stdout=None, stdin=None, capture=False):
    return subprocess.run(
        [*COMPOSE, *arguments],
        cwd=ROOT,
        stdin=stdin,
        stdout=subprocess.PIPE if capture else stdout,
        check=True,
        text=capture,
    )


def timestamp():
    return datetime.now().strftime("%Y%m%d-%H%M%S-%f")


def running_services(services):
    result = run("ps", "--services", "--status", "running", capture=True)
    running = set(result.stdout.splitlines())
    return [service for service in services if service in running]


def dump_database(destination):
    with destination.open("wb") as output:
        run(
            "exec", "-T", "postgres", "pg_dump", "-U", "policy_app", "-d", "policy_app",
            "--format=custom", "--no-owner", "--no-acl", stdout=output,
        )
    if not destination.stat().st_size:
        raise RuntimeError("Database dump is empty")


def backup(include_originals=False):
    BACKUP_ROOT.mkdir(parents=True, exist_ok=True)
    name = timestamp()
    temporary = BACKUP_ROOT / f".incomplete-{name}"
    destination = BACKUP_ROOT / name
    temporary.mkdir()
    stopped = []
    try:
        if include_originals:
            stopped = running_services(APP_SERVICES)
            if stopped:
                print("Stopping writers for a consistent database and originals snapshot...", flush=True)
                run("stop", *stopped)
        print("Backing up PostgreSQL...", flush=True)
        dump_database(temporary / "database.dump")
        if include_originals:
            print("Copying policy originals; this can take some time...", flush=True)
            if ORIGINALS.exists():
                shutil.copytree(ORIGINALS, temporary / "originals")
            else:
                (temporary / "originals").mkdir()
        (temporary / "manifest.json").write_text(
            json.dumps({"format": 1, "includes_originals": include_originals}, indent=2),
            encoding="utf-8",
        )
        temporary.rename(destination)
        print(f"Backup ready: {destination}", flush=True)
        return destination
    finally:
        if stopped:
            run("start", *stopped)


def sql(statement):
    run("exec", "-T", "postgres", "psql", "-U", "policy_app", "-d", "postgres",
        "-v", "ON_ERROR_STOP=1", "-c", statement)


def backup_contents(source):
    source = source.resolve()
    if source.is_file() and source.suffix == ".dump":
        dump = source
        has_originals = False
    else:
        dump = source / "database.dump"
        manifest_file = source / "manifest.json"
        if not dump.is_file() or not manifest_file.is_file():
            raise ValueError("Backup folder must contain database.dump and manifest.json")
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        if manifest.get("format") != 1:
            raise ValueError("Unsupported backup format")
        has_originals = bool(manifest.get("includes_originals"))
        if has_originals and not (source / "originals").is_dir():
            raise ValueError("This backup is missing its originals folder")
    return dump, has_originals, source


def verify(source):
    dump, _, _ = backup_contents(source)
    staging = f"policy_verify_{timestamp().replace('-', '_')}"
    run("exec", "-T", "postgres", "createdb", "-U", "policy_app", "-T", "template0", staging)
    try:
        with dump.open("rb") as input_file:
            run("exec", "-T", "postgres", "pg_restore", "-U", "policy_app", "-d", staging,
                "--exit-on-error", "--no-owner", "--no-acl", stdin=input_file)
        result = run("exec", "-T", "postgres", "psql", "-U", "policy_app", "-d", staging,
                     "-tAc", "SELECT count(*) FROM policies_policy", capture=True)
        print(f"Backup restore verified in a temporary database: {result.stdout.strip()} policies")
    finally:
        run("exec", "-T", "postgres", "dropdb", "-U", "policy_app", "--if-exists", staging)


def restore(source):
    dump, has_originals, source = backup_contents(source)

    suffix = timestamp().replace("-", "_")
    staging = f"policy_restore_{suffix}"
    previous = f"policy_app_before_restore_{suffix}"
    print("Saving a fresh database rollback dump...", flush=True)
    rollback = backup()
    print("Restoring into a separate database...", flush=True)
    run("exec", "-T", "postgres", "createdb", "-U", "policy_app", "-T", "template0", staging)
    stopped = []
    old_renamed = False
    complete = False
    try:
        with dump.open("rb") as input_file:
            run("exec", "-T", "postgres", "pg_restore", "-U", "policy_app", "-d", staging,
                "--exit-on-error", "--no-owner", "--no-acl", stdin=input_file)
        stopped = running_services(RESTORE_SERVICES)
        if stopped:
            run("stop", *stopped)
        if has_originals:
            print("Restoring content-addressed policy originals...", flush=True)
            ORIGINALS.mkdir(parents=True, exist_ok=True)
            shutil.copytree(source / "originals", ORIGINALS, dirs_exist_ok=True)
        sql("SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = 'policy_app' AND pid <> pg_backend_pid()")
        sql(f"ALTER DATABASE policy_app RENAME TO {previous}")
        old_renamed = True
        sql(f"ALTER DATABASE {staging} RENAME TO policy_app")
        complete = True
        print(f"Restore complete. Previous database kept as {previous}.", flush=True)
        print(f"Rollback dump: {rollback}", flush=True)
    finally:
        if old_renamed and not complete:
            sql(f"ALTER DATABASE {previous} RENAME TO policy_app")
        if not complete:
            run("exec", "-T", "postgres", "dropdb", "-U", "policy_app", "--if-exists", staging)
        if stopped:
            run("start", *stopped)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    backup_command = commands.add_parser("backup", help="Back up Docker PostgreSQL")
    backup_command.add_argument("--include-originals", action="store_true",
                                help="Also copy the policy originals (several GB)")
    verify_command = commands.add_parser("verify", help="Test restore into a temporary database")
    verify_command.add_argument("backup_path", type=Path)
    restore_command = commands.add_parser("restore", help="Restore from a backup folder")
    restore_command.add_argument("backup_folder", type=Path)
    restore_command.add_argument("--confirm", action="store_true", help="Required to replace active data")
    args = parser.parse_args()
    if not ENV_FILE.is_file():
        parser.error(".env.compose is missing; run python scripts/prepare_compose.py first")
    if args.command == "backup":
        backup(args.include_originals)
    elif args.command == "verify":
        verify(args.backup_path)
    elif not args.confirm:
        parser.error("restore needs --confirm because it replaces the active database")
    else:
        restore(args.backup_folder)


if __name__ == "__main__":
    main()
