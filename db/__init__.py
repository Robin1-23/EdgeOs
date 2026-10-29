import os
from pathlib import Path

ENV_FILE = Path(__file__).parents[1] / ".env"


def load_env(path: Path = ENV_FILE) -> None:
    """Load KEY=VALUE lines from the repo-root .env (gitignored) into os.environ, never overriding variables
    that are already set. Values are never logged."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.split(" #", 1)[0].strip().strip("'\"")
        os.environ.setdefault(key.strip(), value)


load_env()
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://edgeos:edgeos@localhost:5433/edgeos")
