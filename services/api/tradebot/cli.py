"""Local administrative operations; credentials are never printed."""
import argparse
import os
from pathlib import Path

from sqlalchemy import select

from tradebot.config import get_settings
from tradebot.db import session_factory
from tradebot.models import User
from tradebot.repository import audit
from tradebot.security import password_hasher


def bootstrap():
    settings = get_settings()
    username, password = settings.bootstrap_username, settings.bootstrap_password
    if not username or not password:
        raise SystemExit("Set BOOTSTRAP_USERNAME and BOOTSTRAP_PASSWORD in the secure local environment")
    if len(password) < 12:
        raise SystemExit("Bootstrap password must contain at least 12 characters")
    with session_factory()() as db:
        if db.scalar(select(User).where(User.username == username)):
            print("Bootstrap operator already exists; credentials unchanged")
            return
        db.add(User(username=username, password_hash=password_hasher.hash(password), role="operator"))
        audit(db, "local-bootstrap", "USER_BOOTSTRAP", "operator")
        db.commit()
    print("Bootstrap operator created; credentials were not printed")


def main():
    parser = argparse.ArgumentParser(description="TradeBot Pro local administration")
    parser.add_argument("command", choices=["init-db", "bootstrap", "export-openapi"])
    args = parser.parse_args()
    if args.command == "bootstrap":
        bootstrap()
    elif args.command == "init-db":
        from alembic import command
        from alembic.config import Config
        configured = os.environ.get("ALEMBIC_CONFIG")
        source_config = Path.cwd() / "services/api/alembic.ini"
        config_path = Path(configured) if configured else source_config if source_config.exists() else Path(__file__).parents[1] / "alembic.ini"
        if not config_path.is_file():
            raise SystemExit("Alembic configuration missing; run from the project root or set ALEMBIC_CONFIG")
        command.upgrade(Config(str(config_path)), "head")
    else:
        import json
        from tradebot.app import app
        destination = Path(__file__).parents[1] / "openapi.json"
        destination.write_text(json.dumps(app.openapi(), indent=2) + "\n")
        print(f"OpenAPI saved: {destination}")


if __name__ == "__main__":
    main()
