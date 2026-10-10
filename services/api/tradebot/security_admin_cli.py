"""Trusted-host password recovery; interactive secrets never enter shell history."""
import argparse
import getpass
import os

from dotenv import load_dotenv
from sqlalchemy import select

from tradebot.db import session_factory
from tradebot.models import User
from tradebot.repository import audit
from tradebot.security import password_hasher
from tradebot.security_admin import _delete_sessions


def main() -> None:
    parser = argparse.ArgumentParser(description="Recover a user's password from an authorized deployment terminal")
    parser.add_argument("command", choices=["reset-password"])
    parser.add_argument("username")
    args = parser.parse_args()
    if not os.isatty(0):
        parser.exit(1, "Password recovery requires an interactive trusted terminal\n")
    load_dotenv(override=False)
    password = getpass.getpass("New password (at least 12 characters): ")
    confirm = getpass.getpass("Confirm new password: ")
    if len(password) < 12 or len(password) > 256 or password != confirm:
        parser.exit(1, "Passwords must match and contain 12–256 characters\n")
    with session_factory()() as db:
        user = db.scalar(select(User).where(User.username == args.username))
        if not user:
            parser.exit(1, "User not found\n")
        user.password_hash = password_hasher.hash(password)
        revoked = _delete_sessions(db, user.id)
        audit(db, "trusted-host-admin", "PASSWORD_RECOVERED_LOCALLY", "user:" + str(user.id), {"sessions_revoked": revoked})
        db.commit()
    print("Password changed and existing sessions revoked. MFA remains enabled if previously configured.")


if __name__ == "__main__":
    main()
