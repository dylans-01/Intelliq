"""Command-line entry point: ``intelliq <command>``."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
from pathlib import Path

from .config import Settings


def _store(settings: Settings):
    from .platform.store import Store

    settings.data_dir.mkdir(parents=True, exist_ok=True)
    return Store(settings.metadata_path)


def cmd_init(args: argparse.Namespace) -> int:
    from .platform.bootstrap import ensure_admin

    password = args.admin_password or os.environ.get("INTELLIQ_ADMIN_PASSWORD")
    if not password:
        print("Provide --admin-password or INTELLIQ_ADMIN_PASSWORD", file=sys.stderr)
        return 2
    user = ensure_admin(_store(Settings()), args.admin_email, password)
    print(f"Administrator ready: {user['email']}")
    return 0


def cmd_seed_sample(_args: argparse.Namespace) -> int:
    from .platform.bootstrap import seed_sample

    settings = Settings()
    did = seed_sample(_store(settings), settings.warehouse_dir, None)
    print("Sample content created." if did else "Sample content already exists.")
    return 0


def cmd_dev(args: argparse.Namespace) -> int:
    """Development server: bootstrap an admin, seed sample content, serve."""

    from .platform.bootstrap import ensure_admin, seed_sample

    os.environ.setdefault("INTELLIQ_DEV", "1")
    settings = Settings()
    store = _store(settings)
    email = os.environ.get("INTELLIQ_DEV_ADMIN_EMAIL", "admin@intelliq.local")
    password = os.environ.get("INTELLIQ_DEV_ADMIN_PASSWORD")
    if store.user_by_email(email) is None:
        password = password or secrets.token_urlsafe(12)
        ensure_admin(store, email, password)
        print(f"\n  Development administrator created\n    email:    {email}\n    password: {password}\n")
    seed_sample(store, settings.warehouse_dir, None)
    return cmd_serve(args)


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from .api.app import create_app

    uvicorn.run(create_app(), host=args.host, port=args.port, log_level="info")
    return 0


def cmd_schema(args: argparse.Namespace) -> int:
    from .spec.dashboard import json_schema

    Path(args.output).write_text(json.dumps(json_schema(), indent=2) + "\n")
    print(f"Wrote {args.output}")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    from .validation.runner import main as validate_main

    return validate_main(args.case, args.report)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="intelliq")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="Create the default workspace and an administrator")
    p.add_argument("--admin-email", required=True)
    p.add_argument("--admin-password")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("seed-sample", help="Create the synthetic sample warehouse, dataset and dashboard")
    p.set_defaults(func=cmd_seed_sample)

    for name, func, helptext in (("serve", cmd_serve, "Run the API server"), ("dev", cmd_dev, "Bootstrap and run a development server")):
        p = sub.add_parser(name, help=helptext)
        p.add_argument("--host", default="127.0.0.1")
        p.add_argument("--port", type=int, default=8000)
        p.set_defaults(func=func)

    p = sub.add_parser("export-schema", help="Write the dashboard JSON Schema")
    p.add_argument("output")
    p.set_defaults(func=cmd_schema)

    p = sub.add_parser("validate", help="Run a parity validation case against reference outputs")
    p.add_argument("case", help="Path to a validation case JSON file")
    p.add_argument("--report", help="Write a JSON report to this path")
    p.set_defaults(func=cmd_validate)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
