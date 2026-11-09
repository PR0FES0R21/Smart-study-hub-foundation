import argparse
import asyncio
import getpass

from pydantic import EmailStr, TypeAdapter
from sqlalchemy import select

from studyhub.core.config import get_settings
from studyhub.core.security import password_hasher
from studyhub.infrastructure.database import create_database
from studyhub.models import User


async def create_admin(email: str, name: str):
    email = str(TypeAdapter(EmailStr).validate_python(email)).lower()
    password = getpass.getpass("Admin password (12–128 characters): ")
    if not 12 <= len(password) <= 128 or password != getpass.getpass("Confirm password: "):
        raise SystemExit("Invalid password length or confirmation")
    engine, sessions = create_database(get_settings())
    try:
        async with sessions.begin() as db:
            if await db.scalar(select(User.id).where(User.email == email)):
                raise SystemExit("Account already exists; no changes made")
            db.add(
                User(
                    email=email,
                    full_name=name,
                    is_platform_admin=True,
                    password_hash=password_hasher.hash(password),
                )
            )
        print("Administrator created")
    finally:
        await engine.dispose()


def main():
    parser = argparse.ArgumentParser(description="StudyHub administration (no default accounts)")
    parser.add_argument("command", choices=["create-admin"])
    parser.add_argument("--email", required=True)
    parser.add_argument("--name", required=True)
    args = parser.parse_args()
    asyncio.run(create_admin(args.email, args.name))


if __name__ == "__main__":
    main()
