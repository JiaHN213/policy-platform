"""Prepare private SearXNG configuration without printing secrets or overwriting edits."""
import argparse
import re
import secrets
import shutil
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", default=".env.compose")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    env_path = (root / args.env_file).resolve()
    if not env_path.is_relative_to(root) or not env_path.is_file():
        raise SystemExit("请先在项目内准备环境文件，例如 .env.compose。")
    original = env_path.read_text(encoding="utf-8-sig")
    pattern = r"(?m)^SEARXNG_SECRET=[^\r\n]*$"
    match = re.search(pattern, original)
    if not match or not match.group().partition("=")[2].strip().strip('\"\''):
        replacement = "SEARXNG_SECRET=" + secrets.token_hex(32)
        updated = re.sub(pattern, lambda _: replacement, original) if match else original.rstrip() + "\n" + replacement + "\n"
        env_path.write_text(updated, encoding="utf-8")
    target = root / ".local/searxng/settings.yml"
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        shutil.copyfile(root / "infra/searxng.settings.yml", target)
    print("SearXNG 配置已准备；保留已有设置，密钥未显示。")


if __name__ == "__main__":
    main()
