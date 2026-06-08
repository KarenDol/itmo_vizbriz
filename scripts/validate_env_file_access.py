import os
import sys
from pathlib import Path

from dotenv import load_dotenv


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    env_path = repo_root / "env" / "app.env"

    # Ensure imports like `from flask_app...` work regardless of current working directory.
    sys.path.insert(0, str(repo_root))

    print(f"Loading env file: {env_path}")
    if not env_path.exists():
        print("ERROR: env/app.env not found")
        return 1

    # load_dotenv does not override existing env vars by default.
    load_dotenv(dotenv_path=env_path)

    print("\nRaw env vars:")
    for key in [
        "DB_USERNAME",
        "DB_PASSWORD",
        "DB_HOST",
        "DB_PORT",
        "DB_NAME",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_REGION",
        "S3_BUCKET_NAME",
    ]:
        val = os.getenv(key)
        if val is None or val == "":
            print(f"{key}: (empty)")
        elif any(s in key for s in ["PASSWORD", "SECRET", "KEY"]):
            print(f"{key}: {val[:4]}... (truncated)")
        else:
            print(f"{key}: {val}")

    # Validate how the app config consumes these vars (without importing flask_app/__init__.py)
    from flask_app.config import Config

    print("\nDerived values from flask_app/config.py:")
    print(f"SQLALCHEMY_DATABASE_URI: {Config.SQLALCHEMY_DATABASE_URI}")
    print(f"S3_BUCKET_NAME: {Config.S3_BUCKET_NAME}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

