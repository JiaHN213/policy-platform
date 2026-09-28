"""Create clearly marked local preview fixtures; never usable in production settings."""

import json
import os
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django  # noqa: E402

django.setup()
from django.conf import settings  # noqa: E402
from django.contrib.auth import get_user_model  # noqa: E402
from django.core.management import call_command  # noqa: E402

if not settings.DEBUG:
    raise SystemExit("Local preview is only allowed with DEBUG=true.")
credentials_path = ROOT / ".local" / "preview-login.json"
if not credentials_path.exists():
    username = "preview_admin"
    if get_user_model().objects.filter(username=username).exists():
        raise SystemExit(
            "Preview account already exists; existing credentials will not be changed."
        )
    password = secrets.token_urlsafe(18)
    get_user_model().objects.create_superuser(username, password=password)
    credentials_path.write_text(
        json.dumps({"username": username, "password": password}), encoding="utf-8"
    )
    (ROOT / ".local" / "登录说明.md").write_text(
        f"# 本地开发预览\n\n仅适用于本机开发数据库。\n\n网址：http://127.0.0.1:3000\n\n用户名：{username}\n\n密码：{password}\n\n此账户包含审核权限。生产环境须重新创建账户。\n",
        encoding="utf-8",
    )
call_command("bootstrap")
call_command("seed_demo")
print("Local preview ready. Credentials are in .local/登录说明.md (gitignored).")
