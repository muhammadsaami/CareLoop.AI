"""
CareLoop AI — Operator Access Management CLI

Account and grant management is deliberately NOT reachable over HTTP.  A route
that could mint a grant would let any authenticated caller escalate to reading
every patient in the system, so it lives here, behind the database, where it is
only as accessible as the deployment's own operator access.

`tests/test_security_routes.py::test_grants_can_only_be_created_by_the_patient_creation_route`
fails if an HTTP route for any of this is ever added.

COMMANDS
--------
  create-user    create an account (optionally an operator)
  grant          grant a user access to a patient
  revoke         withdraw a grant
  list-users     show accounts and, with --patient, who may act for a patient
  mint-token     print a token, for handing to a user or a test harness

USAGE
-----
  python -m app.cli.manage_access create-user --email nora@example.com
  python -m app.cli.manage_access create-user --email ops@example.com --system
  python -m app.cli.manage_access grant --email carol@example.com \
      --patient-id <uuid> --relationship caregiver
  python -m app.cli.manage_access revoke --email carol@example.com --patient-id <uuid>
  python -m app.cli.manage_access list-users --patient-id <uuid>
  python -m app.cli.manage_access mint-token --email nora@example.com

PASSWORDS
---------
A password may be supplied with `--password`, or read from stdin when
`--password` is omitted and stdin is not a TTY (so it can come from a secret
manager without ever appearing in the process list or the shell history).
"""
from __future__ import annotations

import argparse
import getpass
import sys
import uuid
from typing import Optional, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.core.logging import get_logger
from app.core.security import create_access_token, hash_password
from app.models.user import AccessRelationship, AppUser, PatientAccess
from app.services.access_control import AccessControlService

logger = get_logger(__name__)

EXIT_OK = 0
EXIT_ERROR = 1


# ── Presentation helpers ────────────────────────────────────────────────────


def _ok(message: str) -> None:
    print(f"  {message}")


def _fail(message: str) -> int:
    print(f"ERROR: {message}", file=sys.stderr)
    return EXIT_ERROR


def _resolve_user(db: Session, email: str) -> Optional[AppUser]:
    """Look a user up by email, case-insensitively.

    `app_users.email` is UNIQUE but case-SENSITIVE at the database level, so
    `--email NORA@Example.com` must not report "no such user" for an account
    created as `nora@example.com`.  Every write path lowercases on the way in;
    this is the matching read.
    """
    return db.execute(
        select(AppUser).where(AppUser.email == email.strip().lower())
    ).scalar_one_or_none()


def _read_password(password: Optional[str]) -> str:
    if password is not None:
        return password
    if sys.stdin.isatty():
        return getpass.getpass("Password: ")
    return sys.stdin.readline().rstrip("\n")


# ── Commands ────────────────────────────────────────────────────────────────


def cmd_create_user(args: argparse.Namespace, db: Session) -> int:
    email = args.email.strip().lower()
    if _resolve_user(db, email) is not None:
        return _fail(f"an account already exists for {email}")

    try:
        password_hash = hash_password(_read_password(args.password))
    except ValueError as exc:
        # Raised by the shared password policy, so the CLI cannot create an
        # account the API would then refuse to authenticate.
        return _fail(str(exc))

    user = AppUser(
        email=email,
        password_hash=password_hash,
        is_active=not args.disabled,
        system_access=args.system,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    _ok(f"created {user.email} ({user.id})")
    _ok(f"  system_access = {user.system_access}  is_active = {user.is_active}")
    if not args.system:
        _ok(
            "  this account has no operator rights and cannot reach "
            "/notifications/dispatch or /retry"
        )
    _ok("  no patient grants yet - use `grant` to give it access")
    return EXIT_OK


def cmd_grant(args: argparse.Namespace, db: Session) -> int:
    user = _resolve_user(db, args.email)
    if user is None:
        return _fail(f"no account for {args.email}")

    try:
        patient_id = uuid.UUID(str(args.patient_id))
    except (ValueError, AttributeError, TypeError):
        return _fail(f"{args.patient_id!r} is not a valid patient UUID")

    try:
        grant = AccessControlService(db).grant_access(
            user, patient_id, args.relationship
        )
    except ValueError as exc:
        return _fail(str(exc))
    db.commit()

    if grant.revoked_at is not None:
        _ok(
            f"granted {user.email} -> {patient_id} ({args.relationship}); "
            "this REPLACED a revoked grant, so the history is preserved"
        )
    else:
        _ok(f"granted {user.email} -> {patient_id} ({args.relationship})")
    return EXIT_OK


def cmd_revoke(args: argparse.Namespace, db: Session) -> int:
    user = _resolve_user(db, args.email)
    if user is None:
        return _fail(f"no account for {args.email}")
    try:
        patient_id = uuid.UUID(str(args.patient_id))
    except (ValueError, AttributeError, TypeError):
        return _fail(f"{args.patient_id!r} is not a valid patient UUID")

    revoked = AccessControlService(db).revoke_access(user, patient_id)
    db.commit()
    if not revoked:
        # Not an error: revoking something that was never granted is the
        # desired end state, and making it exit non-zero would break
        # idempotent deprovisioning scripts.
        _ok(f"no active grant for {user.email} -> {patient_id}; nothing to do")
    else:
        _ok(f"revoked {user.email} -> {patient_id}")
        _ok("  effective immediately - the row is retained for audit")
    return EXIT_OK


def cmd_list_users(args: argparse.Namespace, db: Session) -> int:
    if args.patient_id:
        try:
            patient_id = uuid.UUID(str(args.patient_id))
        except (ValueError, AttributeError, TypeError):
            return _fail(f"{args.patient_id!r} is not a valid patient UUID")
        statement = (
            select(PatientAccess)
            .where(PatientAccess.patient_id == patient_id)
            .order_by(PatientAccess.granted_at)
        )
        grants = db.execute(statement).scalars().all()
        if not grants:
            _ok(f"no grants for patient {patient_id}")
            return EXIT_OK
        _ok(f"grants for patient {patient_id}:")
        for grant in grants:
            user = db.get(AppUser, grant.user_id)
            state = "REVOKED" if grant.revoked_at else "active"
            email = user.email if user else f"<user {grant.user_id} deleted>"
            _ok(
                f"  {email:38} {grant.relationship:10} {state:8} "
                f"granted {grant.granted_at:%Y-%m-%d %H:%M}"
            )
        return EXIT_OK

    users = db.execute(select(AppUser).order_by(AppUser.created_at)).scalars().all()
    if not users:
        _ok("no accounts exist yet - use `create-user`")
        return EXIT_OK
    _ok(f"{len(users)} account(s):")
    for user in users:
        live = (
            db.execute(
                select(PatientAccess.id).where(
                    PatientAccess.user_id == user.id,
                    PatientAccess.revoked_at.is_(None),
                )
            )
            .scalars()
            .all()
        )
        flags = []
        if user.system_access:
            flags.append("operator")
        if not user.is_active:
            flags.append("DISABLED")
        suffix = f"  [{', '.join(flags)}]" if flags else ""
        _ok(
            f"  {user.email:38} {len(live):3} grant(s)  "
            f"{'active' if user.is_active else 'inactive'}{suffix}"
        )
    return EXIT_OK


def cmd_mint_token(args: argparse.Namespace, db: Session) -> int:
    """
    Print a signed token for an existing account.

    There is no login endpoint, so this is how a non-interactive caller (a test
    harness, a service account, a `curl` session) obtains a token.  It is
    deliberately a CLI: adding `POST /login` would mean putting a password on
    the wire and choosing a session strategy, which is a larger decision than
    this change should make on its own.
    """
    user = _resolve_user(db, args.email)
    if user is None:
        return _fail(f"no account for {args.email}")
    if not user.is_active and not args.allow_inactive:
        return _fail(
            f"{user.email} is disabled; its tokens are refused immediately. "
            "Re-enable it, or pass --allow-inactive to mint one anyway."
        )
    print(create_access_token(str(user.id)))
    return EXIT_OK


# ── Parser ──────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli.manage_access",
        description=(
            "Operator account and patient-access management. These operations "
            "are not available over HTTP by design."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_create = sub.add_parser("create-user", help="create an account")
    p_create.add_argument("--email", required=True, help="stored lowercased")
    p_create.add_argument(
        "--password",
        default=None,
        help="omit to be prompted, or to read one line from stdin",
    )
    p_create.add_argument(
        "--system",
        action="store_true",
        help="grant operator rights (notifications dispatch/retry)",
    )
    p_create.add_argument(
        "--disabled",
        action="store_true",
        help="create inactive; tokens are refused immediately",
    )

    p_grant = sub.add_parser("grant", help="grant a user access to a patient")
    p_grant.add_argument("--email", required=True)
    p_grant.add_argument("--patient-id", required=True)
    p_grant.add_argument(
        "--relationship",
        default=AccessRelationship.CAREGIVER,
        choices=sorted(AccessRelationship.ALL),
        help=(
            "recorded for audit; all three grant the same access. There is no "
            "global patient role."
        ),
    )

    p_revoke = sub.add_parser("revoke", help="withdraw a grant")
    p_revoke.add_argument("--email", required=True)
    p_revoke.add_argument("--patient-id", required=True)

    p_list = sub.add_parser("list-users", help="show accounts, or a patient's grants")
    p_list.add_argument(
        "--patient-id",
        default=None,
        help="with this, show who may act for that patient",
    )

    p_token = sub.add_parser("mint-token", help="print a token for an account")
    p_token.add_argument("--email", required=True)
    p_token.add_argument(
        "--allow-inactive",
        action="store_true",
        help="mint for a disabled account (the API will still refuse it)",
    )

    return parser


COMMANDS = {
    "create-user": cmd_create_user,
    "grant": cmd_grant,
    "revoke": cmd_revoke,
    "list-users": cmd_list_users,
    "mint-token": cmd_mint_token,
}


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    db = SessionLocal()
    try:
        return COMMANDS[args.command](args, db)
    except Exception as exc:  # pragma: no cover - operator-facing safety net
        db.rollback()
        # Log the detail, show the operator the type only.  A traceback here can
        # contain a connection string.
        logger.exception("access_cli_failed command=%s", args.command)
        return _fail(f"{type(exc).__name__}: {exc}")
    finally:
        db.close()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
