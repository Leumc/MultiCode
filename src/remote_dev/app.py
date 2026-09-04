"""FastAPI control plane."""

from __future__ import annotations

from pathlib import Path
import sqlite3
from typing import Annotated, Any
import uuid

from fastapi import Cookie, Depends, FastAPI, Header, HTTPException, Request, Response, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .constants import MAX_DEVELOPER_ACCOUNTS
from .database import Database
from .headers import validate_direct_includes
from .schemas import (
    ExtensionCatalogCreate, ExtensionDecision, ExtensionRequestCreate,
    GrantCreate, HeaderUpdate, JobCreate, LoginRequest, UserCreate, UserUpdate,
)
from .security import new_api_token, new_csrf_token, new_session_token, verify_password
from .workspace_image import create_workspace_image

SESSION_COOKIE = "rdp_session"


def public_user(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value for key, value in row.items()
        if key not in {"id", "password_hash", "api_token_hash"}
    }


def create_app(
    *, database: Database, workspace_root: str | Path, secure_cookies: bool = True,
    workspace_size_mib: int = 1024, managed_mode: bool = False,
) -> FastAPI:
    workspace_root = Path(workspace_root)
    app = FastAPI(title="Remote Dev Control Plane", version="0.1.0", docs_url=None, redoc_url=None)
    app.state.database = database
    app.state.workspace_root = workspace_root
    static_root = Path(__file__).with_name("static")
    app.mount("/static", StaticFiles(directory=static_root), name="static")

    @app.get("/", include_in_schema=False)
    def login_page():
        return FileResponse(static_root / "login.html")

    @app.get("/admin", include_in_schema=False)
    def admin_page():
        return FileResponse(static_root / "admin.html")

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Cache-Control"] = "no-store"
        return response

    def current_session(
        token: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
    ) -> tuple[dict[str, Any], str]:
        if not token:
            raise HTTPException(status_code=401, detail="authentication required")
        session = database.get_session_user(token)
        if not session:
            raise HTTPException(status_code=401, detail="invalid session")
        return session

    def admin_session(session=Depends(current_session)) -> tuple[dict[str, Any], str]:
        if session[0]["role"] != "admin":
            raise HTTPException(status_code=403, detail="administrator required")
        return session

    def admin_user(session=Depends(admin_session)) -> dict[str, Any]:
        return session[0]

    def csrf_admin(
        supplied: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
        session=Depends(admin_session),
    ) -> dict[str, Any]:
        if not supplied or supplied != session[1]:
            raise HTTPException(status_code=403, detail="CSRF validation failed")
        return session[0]

    def developer_from_public_id(public_id: str) -> dict[str, Any]:
        try:
            if str(uuid.UUID(public_id)) != public_id:
                raise ValueError
        except (ValueError, AttributeError):
            raise HTTPException(status_code=404, detail="developer not found") from None
        user = database.get_user_by_public_id(public_id)
        if not user or user["role"] != "developer":
            raise HTTPException(status_code=404, detail="developer not found")
        return user

    def developer_user(
        authorization: Annotated[str | None, Header(alias="Authorization")] = None,
    ) -> dict[str, Any]:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="developer token required")
        user = database.get_user_by_api_token(authorization[7:].strip())
        if not user or user["role"] != "developer":
            raise HTTPException(status_code=401, detail="invalid developer token")
        return user

    @app.get("/api/health")
    def health():
        try:
            with database.connect() as connection:
                check = connection.execute("PRAGMA quick_check").fetchone()[0]
        except sqlite3.Error:
            raise HTTPException(status_code=503, detail="service unavailable") from None
        if check != "ok":
            raise HTTPException(status_code=503, detail="service unavailable")
        return {"status": "ok"}

    def issue_session(user: dict[str, Any], response: Response) -> str:
        token = new_session_token()
        csrf = new_csrf_token()
        database.create_session(user["id"], token, csrf)
        response.set_cookie(
            SESSION_COOKIE, token, httponly=True, secure=secure_cookies,
            samesite="strict", max_age=43_200, path="/",
        )
        return csrf

    def login_source(request: Request) -> str:
        return request.client.host if request.client else "unknown"

    def enforce_login_limit(scope: str, username: str, source: str) -> None:
        retry_after = database.login_retry_after(scope, username, source)
        if retry_after:
            raise HTTPException(
                status_code=429,
                detail="too many login attempts",
                headers={"Retry-After": str(retry_after)},
            )

    @app.post("/api/login")
    def developer_login(payload: LoginRequest, response: Response, request: Request):
        source = login_source(request)
        enforce_login_limit("developer", payload.username, source)
        user = database.get_user_by_username(payload.username)
        if (
            not user or user["role"] != "developer" or user["status"] != "active"
            or not verify_password(user["password_hash"], payload.password)
        ):
            database.record_login_failure("developer", payload.username, source)
            raise HTTPException(status_code=401, detail="invalid credentials")
        database.clear_login_failures("developer", payload.username, source)
        issue_session(user, response)
        database.audit(user["id"], "developer.login", "user", user["public_id"])
        return {
            "role": "developer", "username": user["username"],
            "workspace_url": f"/u/{user['public_id']}/",
        }

    @app.get("/api/auth/forward")
    def forward_auth(
        response: Response,
        forwarded_uri: Annotated[str | None, Header(alias="X-Forwarded-Uri")] = None,
        session=Depends(current_session),
    ):
        user = session[0]
        expected = f"/u/{user['public_id']}/"
        if user["role"] != "developer" or not forwarded_uri or not forwarded_uri.startswith(expected):
            raise HTTPException(status_code=403, detail="workspace access denied")
        response.headers["X-Remote-User"] = user["public_id"]
        return {"ok": True}

    @app.post("/api/logout")
    def logout(
        response: Response,
        encoded: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
        _session=Depends(current_session),
    ):
        if encoded:
            database.revoke_session_token(encoded)
        response.delete_cookie(
            SESSION_COOKIE, path="/", secure=secure_cookies,
            httponly=True, samesite="strict",
        )
        return {"status": "logged_out"}

    @app.post("/api/admin/login")
    def login(payload: LoginRequest, response: Response, request: Request):
        source = login_source(request)
        enforce_login_limit("admin", payload.username, source)
        user = database.get_user_by_username(payload.username)
        if (
            not user or user["role"] != "admin" or user["status"] != "active"
            or not verify_password(user["password_hash"], payload.password)
        ):
            database.record_login_failure("admin", payload.username, source)
            raise HTTPException(status_code=401, detail="invalid credentials")
        database.clear_login_failures("admin", payload.username, source)
        csrf = issue_session(user, response)
        database.audit(user["id"], "admin.login", "user", user["public_id"])
        return {"role": "admin", "username": user["username"], "csrf_token": csrf}

    @app.get("/api/admin/users")
    def list_users(_: dict[str, Any] = Depends(admin_user)):
        return [public_user(user) for user in database.list_developers()]

    @app.post("/api/admin/users", status_code=status.HTTP_201_CREATED)
    def create_user(payload: UserCreate, admin: dict[str, Any] = Depends(csrf_admin)):
        if managed_mode:
            raise HTTPException(status_code=403, detail="managed instances require offline bootstrap")
        if len(database.list_developers()) >= MAX_DEVELOPER_ACCOUNTS:
            raise HTTPException(status_code=409, detail="developer account limit reached")
        if database.get_user_by_username(payload.username):
            raise HTTPException(status_code=409, detail="username already exists")
        token = new_api_token()
        public_id = str(uuid.uuid4())
        workspace = create_workspace_image(
            workspace_root, public_id, size_mib=workspace_size_mib
        )
        try:
            user = database.create_developer(
                payload.username, payload.password, token, str(workspace), public_id=public_id
            )
        except Exception:
            workspace.unlink(missing_ok=True)
            raise
        database.audit(
            admin["id"], "user.create", "user", user["public_id"],
            {"username": user["username"]},
        )
        result = public_user(user)
        result["api_token"] = token
        return result

    @app.patch("/api/admin/users/{public_id}")
    def update_user(
        public_id: str,
        payload: UserUpdate,
        admin: dict[str, Any] = Depends(csrf_admin),
    ):
        current = developer_from_public_id(public_id)
        values = payload.model_dump(exclude_none=True)
        if not values:
            raise HTTPException(status_code=422, detail="no user changes supplied")
        try:
            user = database.update_developer(current["id"], **values)
        except sqlite3.IntegrityError as error:
            if "users.username" in str(error):
                raise HTTPException(status_code=409, detail="username already exists") from None
            raise
        if not user:
            raise HTTPException(status_code=404, detail="developer not found")
        database.audit(admin["id"], "user.update", "user", public_id, {key: "[REDACTED]" if key == "password" else value for key, value in values.items()})
        return public_user(user)

    @app.post("/api/admin/users/{public_id}/revoke-sessions")
    def revoke_user_sessions(public_id: str, admin: dict[str, Any] = Depends(csrf_admin)):
        user = developer_from_public_id(public_id)
        count = database.revoke_user_sessions(user["id"])
        if count is None:
            raise HTTPException(status_code=404, detail="developer not found")
        database.audit(
            admin["id"], "user.sessions.revoke", "user", public_id,
            {"revoked_sessions": count},
        )
        return {"public_id": public_id, "revoked_sessions": count}

    @app.post("/api/admin/users/{public_id}/rotate-token")
    def rotate_user_token(public_id: str, admin: dict[str, Any] = Depends(csrf_admin)):
        if managed_mode:
            raise HTTPException(status_code=403, detail="managed instances require offline token rotation")
        current = developer_from_public_id(public_id)
        token = new_api_token()
        user = database.rotate_developer_token(current["id"], token)
        if not user:
            raise HTTPException(status_code=404, detail="developer not found")
        database.audit(admin["id"], "user.token.rotate", "user", public_id)
        return {"public_id": public_id, "api_token": token}

    @app.get("/api/admin/headers")
    def list_headers(_: dict[str, Any] = Depends(admin_user)):
        return database.list_headers()

    @app.put("/api/admin/headers/{header_name:path}")
    def set_header(
        header_name: str, payload: HeaderUpdate,
        admin: dict[str, Any] = Depends(csrf_admin),
    ):
        if not header_name or header_name.startswith("/") or ".." in header_name.split("/"):
            raise HTTPException(status_code=422, detail="invalid header name")
        result = database.set_header(header_name, payload.enabled)
        database.audit(
            admin["id"], "header.update", "header", header_name,
            {"enabled": payload.enabled},
        )
        return result

    @app.get("/api/extensions")
    def extension_catalog(_: dict[str, Any] = Depends(developer_user)):
        return database.list_extensions()

    @app.post("/api/extensions/requests", status_code=status.HTTP_201_CREATED)
    def request_extension(
        payload: ExtensionRequestCreate,
        user: dict[str, Any] = Depends(developer_user),
    ):
        try:
            return database.request_extension(user["id"], payload.catalog_id)
        except LookupError:
            raise HTTPException(status_code=404, detail="extension not in approved catalog") from None

    @app.get("/api/admin/extensions")
    def admin_extensions(_: dict[str, Any] = Depends(admin_user)):
        return database.list_extensions()

    @app.post("/api/admin/extensions", status_code=status.HTTP_201_CREATED)
    def add_extension(
        payload: ExtensionCatalogCreate,
        admin: dict[str, Any] = Depends(csrf_admin),
    ):
        item = database.create_extension(payload.model_dump())
        database.audit(admin["id"], "extension.catalog.add", "extension", str(item["id"]), payload.model_dump())
        return item

    @app.get("/api/admin/extension-requests")
    def extension_requests(_: dict[str, Any] = Depends(admin_user)):
        return database.list_extension_requests()

    @app.post("/api/admin/extension-requests/{request_id}/approve")
    def approve_extension(
        request_id: int,
        payload: ExtensionDecision,
        admin: dict[str, Any] = Depends(csrf_admin),
    ):
        item = database.decide_extension_request(request_id, admin["id"], payload.scope)
        if not item:
            raise HTTPException(status_code=404, detail="pending extension request not found")
        database.audit(admin["id"], "extension.request.approve", "extension_request", str(request_id), payload.model_dump())
        return item

    @app.post("/api/admin/users/{public_id}/grants", status_code=status.HTTP_201_CREATED)
    def create_grant(
        public_id: str, payload: GrantCreate,
        admin: dict[str, Any] = Depends(csrf_admin),
    ):
        user = developer_from_public_id(public_id)
        values = payload.model_dump()
        if not any(value is not None for value in values.values()):
            raise HTTPException(status_code=422, detail="grant has no effect")
        grant = database.create_grant(user["id"], admin["id"], values)
        database.audit(admin["id"], "grant.create", "user", public_id, values)
        return grant

    @app.get("/api/admin/jobs")
    def admin_jobs(_: dict[str, Any] = Depends(admin_user)):
        return database.list_jobs_admin()

    @app.post("/api/admin/jobs/{job_id}/cancel")
    def admin_cancel_job(job_id: int, admin: dict[str, Any] = Depends(csrf_admin)):
        job = database.cancel_job_admin(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="job not found")
        database.audit(admin["id"], "job.cancel", "job", str(job_id))
        return {"id": job_id, "status": job["status"], "cancel_requested": bool(job["cancel_requested"])}

    @app.get("/api/admin/audit")
    def audit_log(_: dict[str, Any] = Depends(admin_user)):
        return database.list_audit()

    @app.post("/api/jobs", status_code=status.HTTP_202_ACCEPTED)
    def create_job(payload: JobCreate, user: dict[str, Any] = Depends(developer_user)):
        include_errors = validate_direct_includes(payload.source, database.enabled_headers())
        if include_errors:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "include_policy",
                    "errors": [error.__dict__ for error in include_errors],
                },
            )
        try:
            job = database.create_job(user["id"], payload.model_dump())
        except OverflowError:
            raise HTTPException(
                status_code=409,
                detail={"code": "inflight_limit", "message": "too many unfinished jobs"},
            ) from None
        except ValueError as exc:
            if str(exc) == "quota_exceeded":
                raise HTTPException(
                    status_code=422,
                    detail={"code": "quota_exceeded", "message": "requested limits exceed active authorization"},
                ) from None
            raise
        return {"id": job["id"], "status": job["status"], "case_count": job["case_count"]}

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: int, user: dict[str, Any] = Depends(developer_user)):
        job = database.get_job(job_id, user["id"])
        if not job:
            raise HTTPException(status_code=404, detail="job not found")
        job.pop("source", None)
        for case in job["cases"]:
            case.pop("input_text", None)
        return job

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel_job(job_id: int, user: dict[str, Any] = Depends(developer_user)):
        job = database.cancel_job(job_id, user["id"])
        if not job:
            raise HTTPException(status_code=404, detail="job not found")
        return {"id": job["id"], "status": job["status"], "cancel_requested": bool(job["cancel_requested"])}

    return app
