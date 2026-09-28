"""
Operator access-management CLI.

These commands are the only way to create an account or move a grant, so the
tests exercise them as commands - argument parsing, output, and the database
effect together.  A CLI tested only at the function level misses the wiring
between the parser and the service, which is where operator tooling usually
breaks.

The database is real and the migrations are real; only the input is
synthetic.  Grant management is the highest-value operation in the system, so
its behaviour is pinned rather than assumed - particularly the idempotency and
revocation-history guarantees that deprovisioning scripts depend on.
"""
from __future__ import annotations

import uuid

import pytest

from app.cli.manage_access import EXIT_ERROR, EXIT_OK, main
from app.core.security import decode_access_token
from app.models.user import AccessRelationship, AppUser, PatientAccess

PASSWORD = "a-sufficiently-long-password"


@pytest.fixture
def run(db_session, monkeypatch):
    """
    Run the CLI against the test database.

    A password is injected for `create-user` only.  Supplying it here rather
    than per test means each test reads as the command an operator would type,
    and it keeps pytest's captured stdin out of the password path entirely.
    Tests that exercise password handling call `main(...)` directly.
    """
    from app.cli import manage_access

    # `main()` closes its session in a `finally`, which would detach the shared
    # `db_session` and break every assertion after the first command. Rather
    # than weakening the CLI's own cleanup, the shared session is handed out
    # behind a wrapper whose `close()` is a no-op: the CLI behaves exactly as it
    # does for an operator, and the test keeps its session.
    class _SharedSession:
        def __init__(self, session):
            self._session = session

        def __getattr__(self, name):
            return getattr(self._session, name)

        def close(self):
            pass

    monkeypatch.setattr(manage_access, "SessionLocal", lambda: _SharedSession(db_session))

    def _run(*argv: str) -> int:
        args = list(argv)
        if args and args[0] == "create-user" and "--password" not in args:
            args += ["--password", PASSWORD]
        return main(args)

    return _run


@pytest.fixture
def patient(db_session):
    from app.models.patient import Patient

    record = Patient(name="CLI Patient", contact_number="+15550001111")
    db_session.add(record)
    db_session.commit()
    db_session.refresh(record)
    return record


# ── create-user ─────────────────────────────────────────────────────────────


class TestCreateUser:
    def test_creates_an_account(self, run, db_session, capsys):
        assert run("create-user", "--email", "nora@example.com") == EXIT_OK
        user = db_session.query(AppUser).filter_by(email="nora@example.com").one()
        assert user.is_active is True
        assert user.system_access is False
        assert "nora@example.com" in capsys.readouterr().out

    def test_lowercases_the_email(self, run, db_session):
        """
        `app_users.email` is UNIQUE but case-SENSITIVE.

        Without normalizing on write, `Nora@Example.com` and `nora@example.com`
        would be two accounts, and the second would be a way to impersonate the
        first. Normalization belongs at the writer, so it is asserted here.
        """
        assert run("create-user", "--email", "  MixedCase@Example.COM ") == EXIT_OK
        user = db_session.query(AppUser).filter_by(email="mixedcase@example.com").one()
        assert user.id is not None

    def test_rejects_a_duplicate_case_insensitively(self, run, capsys):
        run("create-user", "--email", "dup@example.com")
        assert run("create-user", "--email", "DUP@EXAMPLE.COM") == EXIT_ERROR
        assert "already exists" in capsys.readouterr().err

    def test_operator_rights_are_opt_in(self, run, db_session):
        """
        `system_access` defaults off and is only set by an explicit flag.

        It is the one global privilege, so it must never be implied by creating
        an account - otherwise every operator script that provisions a caregiver
        would also provision an operator.
        """
        run("create-user", "--email", "carer@example.com")
        assert (
            db_session.query(AppUser).filter_by(email="carer@example.com").one()
            .system_access
            is False
        )

        run("create-user", "--email", "ops@example.com", "--system")
        assert (
            db_session.query(AppUser).filter_by(email="ops@example.com").one()
            .system_access
            is True
        )

    def test_never_echoes_the_password(self, run, capsys):
        run("create-user", "--email", "quiet@example.com")
        assert PASSWORD not in capsys.readouterr().out

    def test_rejects_a_password_the_api_would_refuse(self, run, capsys):
        """
        The CLI shares the API's password policy.

        A CLI that accepted a short password would let an operator create an
        account that is weaker than the application can authenticate, which is
        the kind of divergence that is only discovered during an incident.
        """
        code = main(["create-user", "--email", "weak@example.com", "--password", "short"])
        assert code == EXIT_ERROR
        assert "password" in capsys.readouterr().err.lower()

    def test_can_create_a_disabled_account(self, run, db_session):
        run("create-user", "--email", "left@example.com", "--disabled")
        user = db_session.query(AppUser).filter_by(email="left@example.com").one()
        assert user.is_active is False

    def test_reads_a_password_from_stdin_when_not_a_tty(self, run, db_session, monkeypatch):
        """A secret can be piped in without appearing in argv or shell history."""
        monkeypatch.setattr("sys.stdin.isatty", lambda: False)
        monkeypatch.setattr("sys.stdin.readline", lambda: PASSWORD + "\n")
        assert main(["create-user", "--email", "piped@example.com"]) == EXIT_OK
        assert db_session.query(AppUser).filter_by(email="piped@example.com").count() == 1


# ── grant / revoke ──────────────────────────────────────────────────────────


class TestGrantAndRevoke:
    def test_grant_creates_an_active_row(self, run, db_session, patient):
        run("create-user", "--email", "carer@example.com")
        assert (
            run(
                "grant",
                "--email",
                "carer@example.com",
                "--patient-id",
                str(patient.id),
                "--relationship",
                AccessRelationship.CAREGIVER,
            )
            == EXIT_OK
        )
        grant = (
            db_session.query(PatientAccess)
            .filter_by(relationship=AccessRelationship.CAREGIVER)
            .one()
        )
        assert str(grant.patient_id) == str(patient.id)
        assert grant.revoked_at is None

    def test_grant_is_idempotent_while_active(self, run, db_session, patient):
        """
        Running `grant` twice does not create two live rows.

        Deprovisioning and provisioning scripts get re-run - by a human after a
        mistake, or by a retried job. A second live row would break the partial
        unique index and, worse, make "revoke this access" ambiguous.
        """
        run("create-user", "--email", "twice@example.com")
        for _ in range(3):
            run("grant", "--email", "twice@example.com", "--patient-id", str(patient.id))
        assert db_session.query(PatientAccess).count() == 1

    def test_regnant_after_revoke_preserves_history(self, run, db_session, patient):
        """
        Re-granting after a revoke writes a NEW row and leaves the old revoked.

        This is what an audit reads, and it is what a stale-grant bug would
        silently destroy: if the revoked row were reused, the record that access
        was withdrawn would vanish.
        """
        run("create-user", "--email", "again@example.com")
        run("grant", "--email", "again@example.com", "--patient-id", str(patient.id))
        run("revoke", "--email", "again@example.com", "--patient-id", str(patient.id))
        run("grant", "--email", "again@example.com", "--patient-id", str(patient.id))

        rows = db_session.query(PatientAccess).all()
        assert len(rows) == 2
        assert sum(1 for row in rows if row.revoked_at is None) == 1

    def test_revoke_twice_is_not_an_error(self, run, capsys, patient):
        """
        Revoking an absent grant exits 0.

        Deprovisioning should be safe to re-run: a script that removed the
        mapping from its own records and then re-ran must not fail, or operators
        learn to ignore the exit code - which would hide real failures.
        """
        run("create-user", "--email", "idem@example.com")
        run("grant", "--email", "idem@example.com", "--patient-id", str(patient.id))
        assert run("revoke", "--email", "idem@example.com", "--patient-id", str(patient.id)) == EXIT_OK
        assert run("revoke", "--email", "idem@example.com", "--patient-id", str(patient.id)) == EXIT_OK
        assert "nothing to do" in capsys.readouterr().out

    def test_rejects_an_unknown_patient_id(self, run, capsys):
        run("create-user", "--email", "typo@example.com")
        code = run("grant", "--email", "typo@example.com", "--patient-id", "not-a-uuid")
        assert code == EXIT_ERROR
        assert "not a valid patient UUID" in capsys.readouterr().err

    def test_rejects_an_unknown_account(self, run, capsys, patient):
        code = run(
            "grant", "--email", "ghost@example.com", "--patient-id", str(patient.id)
        )
        assert code == EXIT_ERROR
        assert "no account" in capsys.readouterr().err

    def test_rejects_an_unknown_relationship(self, run, patient):
        """
        `admin` is not a relationship, and argparse refuses it before any SQL.

        Worth pinning because it looks like a role system: someone reading
        `--relationship self|caregiver|care_team` might reasonably try `admin`.
        The refusal is argparse's own (exit 2), so there is no path by which an
        unrecognised value reaches the database.
        """
        run("create-user", "--email", "rel@example.com")
        with pytest.raises(SystemExit) as excinfo:
            main(
                [
                    "grant",
                    "--email",
                    "rel@example.com",
                    "--patient-id",
                    str(patient.id),
                    "--relationship",
                    "admin",
                ]
            )
        assert excinfo.value.code != EXIT_OK


# ── listing ─────────────────────────────────────────────────────────────────


class TestListing:
    def test_lists_accounts_with_grant_counts(self, run, capsys, patient):
        run("create-user", "--email", "one@example.com")
        run("create-user", "--email", "ops@example.com", "--system")
        run("grant", "--email", "one@example.com", "--patient-id", str(patient.id))
        capsys.readouterr()

        run("list-users")
        out = capsys.readouterr().out
        assert "one@example.com" in out
        assert "ops@example.com" in out
        assert "1 grant(s)" in out
        assert "[operator]" in out

    def test_shows_a_disabled_account_as_disabled(self, run, capsys):
        run("create-user", "--email", "off@example.com", "--disabled")
        capsys.readouterr()
        run("list-users")
        assert "DISABLED" in capsys.readouterr().out

    def test_lists_a_patients_grants_with_state(self, run, capsys, patient):
        run("create-user", "--email", "here@example.com")
        run("create-user", "--email", "gone@example.com")
        run("grant", "--email", "here@example.com", "--patient-id", str(patient.id))
        run("revoke", "--email", "gone@example.com", "--patient-id", str(patient.id))
        capsys.readouterr()
        run("list-users", "--patient-id", str(patient.id))
        out = capsys.readouterr().out
        assert "here@example.com" in out
        assert "active" in out

    def test_empty_database_is_not_an_error(self, run, capsys):
        assert run("list-users") == EXIT_OK
        assert "no accounts" in capsys.readouterr().out


# ── mint-token ──────────────────────────────────────────────────────────────


class TestMintToken:
    def test_mints_a_token_the_api_accepts(self, run, db_session, capsys):
        run("create-user", "--email", "tok@example.com")
        capsys.readouterr()
        assert run("mint-token", "--email", "tok@example.com") == EXIT_OK
        token = capsys.readouterr().out.strip()

        claims = decode_access_token(token)
        user = db_session.query(AppUser).filter_by(email="tok@example.com").one()
        assert claims["sub"] == str(user.id)

    def test_refuses_a_disabled_account_by_default(self, run, capsys):
        run("create-user", "--email", "dead@example.com", "--disabled")
        capsys.readouterr()
        assert run("mint-token", "--email", "dead@example.com") == EXIT_ERROR
        assert "disabled" in capsys.readouterr().err

    def test_can_override_for_a_disabled_account(self, run, capsys):
        """
        `--allow-inactive` mints a token the API will still refuse.

        Offered for recovery flows - confirming an account is genuinely the
        problem before re-enabling it - and the output says so, so an operator
        does not mistake a working mint for working authentication.
        """
        run("create-user", "--email", "zombie@example.com", "--disabled")
        capsys.readouterr()
        assert (
            run("mint-token", "--email", "zombie@example.com", "--allow-inactive")
            == EXIT_OK
        )
        assert capsys.readouterr().out.strip() != ""

    def test_rejects_an_unknown_account(self, run, capsys):
        assert run("mint-token", "--email", "nobody@example.com") == EXIT_ERROR
        assert "no account" in capsys.readouterr().err
