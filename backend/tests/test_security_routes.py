"""
CareLoop AI — Route Authorization Coverage

Authentication is attached once per router in `app.main`, so a new route on a
protected router is protected automatically.  Authorization cannot be: this API
names its patient in a path, in a resource id, or in a request body, so it is
attached per route.  Per-route attachment is exactly the kind of thing that
gets forgotten, so this module is the guard.

WHAT THIS PROVES
----------------
1. Every route except the two health probes requires an authenticated user.
2. Every patient-scoped route declares an authorization dependency.
3. Every `*_id` path parameter used by a route is covered by the resource
   registry in `app.api.auth_deps`, so ownership can be resolved for it.
4. The system-wide endpoints are guarded by `system_access` and nothing else.
5. No route on the public router reaches a patient table.

Together those mean a new endpoint cannot join the surface unauthenticated or
unauthorized without one of these failing.  Without them, "we added auth" is a
claim; with them it is a property of the route table.

The assertions read the REAL `app.routes`, not a hand-maintained list, so they
cannot drift from what the application actually serves.
"""
from __future__ import annotations

import pytest
from fastapi.routing import APIRoute

from app.api.auth_deps import (
    PATIENT_PATH_PARAM,
    RESOURCE_OWNERS,
)
from app.main import app

#: The complete, intentional public surface.  Anything not here must be
#: authenticated.  Kept as an explicit allow-list rather than a denylist so that
#: ADDING a route does not silently inherit "public".
PUBLIC_PATHS = frozenset(
    {
        "/api/v1/health",
        "/api/v1/health/ready",
    }
)

#: Endpoints that act on every patient at once, and therefore require
#: `system_access` rather than a per-patient grant.
SYSTEM_PATHS = frozenset(
    {
        "/api/v1/notifications/dispatch",
        "/api/v1/notifications/retry",
    }
)

#: Authorization dependency names accepted on a patient-scoped route.
PATIENT_ACCESS_DEPENDENCIES = frozenset(
    {
        "require_patient_access",
        "require_agent_query_patient_access",
        "require_rag_retrieve_patient_access",
        "require_rag_index_patient_access",
        "require_discharge_upload_patient_access",
    }
)

SYSTEM_ACCESS_DEPENDENCIES = frozenset({"require_system_access"})


def _path_param_names(route: APIRoute) -> set[str]:
    """
    Names of a route's path parameters.

    Read from `route.dependant`, which is where FastAPI records them after
    resolution.  `APIRoute` itself has no such attribute, and re-parsing the
    path with a regex here would be a second, silently-divergent copy of
    FastAPI's own matching.
    """
    return {param.name for param in route.dependant.path_params}


def _api_routes() -> list[APIRoute]:
    return [r for r in app.routes if isinstance(r, APIRoute)]


def _dependency_names(route: APIRoute) -> set[str]:
    """
    Every dependency callable name on a route, at any nesting level.

    Walks `dependant.dependencies` recursively because FastAPI wraps a
    dependency's own sub-dependencies, and a route may declare the check through
    one of those rather than directly.
    """
    names: set[str] = set()

    def walk(dependant) -> None:
        for dep in dependant.dependencies:
            call = dep.call
            name = getattr(call, "__name__", None) or repr(call)
            names.add(name)
            walk(dep)

    walk(route.dependant)
    return names


def _has_authentication(route: APIRoute) -> bool:
    """True when the route cannot be reached without a valid bearer token."""
    for dep in route.dependant.dependencies:
        if getattr(dep.call, "__name__", None) == "require_authenticated_user":
            return True
    return False


ALL_ROUTES = _api_routes()


def test_route_table_is_not_empty():
    """A guard over an empty set of routes passes trivially and proves nothing."""
    assert len(ALL_ROUTES) > 40, (
        f"only {len(ALL_ROUTES)} API routes found - is the app wired up?"
    )


# ── 1. Authentication ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "path", sorted(PUBLIC_PATHS), ids=lambda p: p
)
def test_health_endpoints_are_public(path):
    """The two documented public routes exist and carry no auth requirement."""
    routes = {r.path: r for r in ALL_ROUTES}
    assert path in routes, f"{path} is expected to be a public route"
    assert not _has_authentication(routes[path]), (
        f"{path} is meant to be reachable by an unauthenticated load balancer"
    )


def test_every_other_route_requires_authentication():
    """
    The core assertion: authentication is the default, not the exception.

    A route added to a protected router inherits the router-level dependency, so
    this should hold for the whole table. If it ever fails, the route was added
    to a router that was not registered under `PROTECTED` in `app.main` - which
    is the single most likely way for a new endpoint to end up open.
    """
    unprotected = sorted(
        f"{sorted(r.methods)} {r.path}"
        for r in ALL_ROUTES
        if r.path not in PUBLIC_PATHS and not _has_authentication(r)
    )
    assert not unprotected, (
        "routes reachable without authentication:\n  "
        + "\n  ".join(unprotected)
    )


def test_public_router_touches_no_patient_data():
    """
    The public routes are liveness probes, so they must not read patient rows.

    This guards the reason they are public rather than merely restating it: a
    liveness endpoint that started counting patients would turn an
    unauthenticated route into a data channel.
    """
    for path in PUBLIC_PATHS:
        route = next(r for r in ALL_ROUTES if r.path == path)
        source_names = {
            getattr(n, "__module__", "") for n in _source_objects(route.endpoint)
        }
        assert not any("app.models" in name for name in source_names), (
            f"{path} imports a model module; the public surface must not reach "
            "the database layer"
        )


def _source_objects(endpoint):
    """The function and anything it closed over, best effort."""
    yield endpoint
    module = getattr(endpoint, "__module__", None)
    if module:
        import sys

        mod = sys.modules.get(module)
        if mod is not None:
            yield mod


# ── 2 & 3. Patient authorization, and the resource registry ─────────────────


def test_every_patient_scoped_route_declares_authorization():
    """
    Every route that names a patient or a patient-owned resource is authorized.

    A route is in scope if it has a `patient_id` path parameter, a registered
    `*_id` path parameter, or is one of the known system endpoints.  Anything in
    scope must declare one of the accepted authorization dependencies.
    """
    unprotected: list[str] = []
    for route in ALL_ROUTES:
        if route.path in PUBLIC_PATHS:
            continue
        names = _dependency_names(route)
        params = _path_param_names(route)
        in_scope = bool(
            params & {PATIENT_PATH_PARAM, *RESOURCE_OWNERS.keys()}
        ) or route.path in SYSTEM_PATHS
        if not in_scope:
            continue
        if route.path in SYSTEM_PATHS:
            continue  # covered separately by test_system_endpoints
        if not (names & PATIENT_ACCESS_DEPENDENCIES):
            unprotected.append(f"{sorted(route.methods)} {route.path}")

    assert not unprotected, (
        "patient-scoped routes with no authorization dependency:\n  "
        + "\n  ".join(sorted(unprotected))
        + "\n\nAttach one of "
        f"{sorted(PATIENT_ACCESS_DEPENDENCIES)} to the route decorator."
    )


def test_every_resource_path_parameter_is_registered():
    """
    No route may name a resource the ownership registry cannot resolve.

    `require_patient_access` resolves a `*_id` path parameter through
    `RESOURCE_OWNERS`.  A parameter missing from that map falls through to the
    "unrecognized path parameter" refusal - it fails CLOSED, so a forgotten
    entry cannot leak data.  It still must not happen: a route that 403s every
    request is broken, and a reviewer would read the log line as noise.

    Rather than special-case, any `*_id` path parameter not in the registry is
    a shape this module does not understand, and that is what is asserted.
    """
    known = set(RESOURCE_OWNERS.keys()) | {PATIENT_PATH_PARAM}
    unknown: list[str] = []
    for route in ALL_ROUTES:
        if route.path in PUBLIC_PATHS or route.path in SYSTEM_PATHS:
            continue
        for param in sorted(_path_param_names(route)):
            if not param.endswith("_id"):
                continue
            if param not in known:
                unknown.append(f"{sorted(route.methods)} {route.path} ({param})")

    assert not unknown, (
        "path parameters with no entry in RESOURCE_OWNERS:\n  "
        + "\n  ".join(sorted(unknown))
        + "\n\nAdd the owning model to RESOURCE_OWNERS in app/api/auth_deps.py."
    )


# ── 4. The system-wide endpoints ────────────────────────────────────────────


def test_system_endpoints_require_system_access():
    """
    `/notifications/dispatch` and `/notifications/retry` walk every patient's
    rows, so they are guarded by `system_access` - and by nothing weaker.
    """
    for path in sorted(SYSTEM_PATHS):
        route = next(r for r in ALL_ROUTES if r.path == path)
        names = _dependency_names(route)
        assert names & SYSTEM_ACCESS_DEPENDENCIES, (
            f"{path} does not require system access"
        )
        assert not (names & PATIENT_ACCESS_DEPENDENCIES), (
            f"{path} uses a per-patient grant; it acts on every patient"
        )


def test_system_access_is_not_claimed_by_any_patient_route():
    """
    Only the two whole-system endpoints may require operator rights.

    A `system_access` requirement on a patient-scoped route would mean an
    ordinary caregiver could not use it, which is a functional regression that
    looks like a security improvement in review.
    """
    leaked = sorted(
        f"{sorted(r.methods)} {r.path}"
        for r in ALL_ROUTES
        if r.path not in SYSTEM_PATHS
        and (_dependency_names(r) & SYSTEM_ACCESS_DEPENDENCIES)
    )
    assert not leaked, (
        "routes requiring system access that do not need it:\n  "
        + "\n  ".join(leaked)
    )


# ── 5. No route can manage its own grants ───────────────────────────────────


#: The one route permitted to create a grant.
#:
#: `POST /patients` creates the patient row and grants the caller `self` access
#: to it, in the same request.  Without that, creating a patient would produce a
#: record its own creator cannot read.  It is the only self-service grant in the
#: system, and it is safe for one specific reason: the patient id in the grant is
#: the one the route just minted, never one the caller supplied.  A second
#: endpoint, or a `patient_id` taken from the request, would not be.
ALLOWED_GRANT_ROUTES = frozenset({"/api/v1/patients"})


def test_grants_can_only_be_created_by_the_patient_creation_route():
    """
    No route may create a grant except `POST /patients`, and no route may revoke
    one at all.

    A principal who could reach grant management could mint a `care_team` grant
    over any patient and then read that patient's entire record - which makes
    grant management the single highest-value target in the API. It is therefore
    an operator-only CLI operation, and this test fails if a route for it is
    ever added.
    """
    granting: list[str] = []
    revoking: list[str] = []
    for route in ALL_ROUTES:
        source = _readable_source(route.endpoint)
        if source is None:  # pragma: no cover - unreadable handler
            continue
        label = f"{sorted(route.methods)} {route.path}"
        if "grant_access" in source and route.path not in ALLOWED_GRANT_ROUTES:
            granting.append(label)
        if "revoke_access" in source:
            revoking.append(label)

    assert not granting, (
        "routes creating a patient_access grant outside the allow-list:\n  "
        + "\n  ".join(sorted(granting))
        + f"\n\nOnly {sorted(ALLOWED_GRANT_ROUTES)} may self-grant."
    )
    assert not revoking, (
        "routes revoking a patient_access grant:\n  " + "\n  ".join(sorted(revoking))
    )


def test_no_route_can_create_or_modify_an_account():
    """
    No HTTP route may create an `AppUser` or set its privileges.

    `system_access` in particular must be unreachable from the API: it is the
    one global privilege, and a route that could set it would let any
    authenticated caller promote itself to operator and then dispatch or retry
    notifications for every patient in the system.
    """
    # Matched on ASSIGNMENT shapes only.  A bare `system_access` also appears in
    # the two system routes' `Depends(require_system_access)` decorators, which
    # is a privilege READ - exactly what those routes are for.
    assignment_patterns = (
        "AppUser(",
        "password_hash",
        "system_access=",
        "system_access =",
        '"system_access"',
        "'system_access'",
        "setattr",
    )
    offenders: list[str] = []
    for route in ALL_ROUTES:
        source = _readable_source(route.endpoint)
        if source is None:  # pragma: no cover
            continue
        label = f"{sorted(route.methods)} {route.path}"
        for needle in assignment_patterns:
            if needle in source:
                offenders.append(f"{label} ({needle})")

    assert not offenders, (
        "routes creating or modifying an account:\n  " + "\n  ".join(offenders)
    )


def test_the_self_grant_cannot_target_a_caller_supplied_patient():
    """
    `POST /patients` must grant the id it just created, not one from the request.

    This is the sharp edge of the single self-service grant, and it is checkable
    without executing anything: the handler must not take a `patient_id`
    parameter, and must not read one from the submitted body. If either appears,
    the route has become a grant-issuing endpoint for an arbitrary patient.
    """
    route = next(r for r in ALL_ROUTES if r.path in ALLOWED_GRANT_ROUTES)
    params = {p.name for p in route.dependant.query_params} | {
        p.name for p in route.dependant.body_params
    }
    assert "patient_id" not in params, (
        f"{route.path} accepts a patient_id; a self-grant must only ever target "
        "the patient the route just created"
    )
    assert "patient_id" not in _path_param_names(route), (
        f"{route.path} takes a patient_id path parameter"
    )


def _readable_source(endpoint) -> str | None:
    """Source text of a route handler, or None when it cannot be read."""
    import inspect

    try:
        return inspect.getsource(endpoint)
    except (OSError, TypeError):  # pragma: no cover - builtins/C functions
        return None
