"""Generate isolated local Compose settings without overwriting Windows settings."""

import secrets
from pathlib import Path

root = Path(__file__).resolve().parents[1]
target = root / ".env.compose"
if target.exists():
    raise SystemExit(".env.compose already exists; existing credentials were preserved.")
content = (root / ".env.example").read_text(encoding="utf-8")
content = content.replace("replace-with-a-random-password", secrets.token_urlsafe(32))
content = content.replace("replace-with-a-random-secret", secrets.token_urlsafe(48))
target.write_text(content, encoding="utf-8")
print("Created .env.compose. Use: docker compose --env-file .env.compose up --build -d")
