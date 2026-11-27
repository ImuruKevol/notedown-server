"""Native ASGI application for the Notedown sync service and web editor.

Run with ``uvicorn app:create_app --factory --workers 1``.  The existing
SyncStore is shared by all routes, retaining its on-disk format and locks.
"""

import json
import os
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, Request
from fastapi.openapi.utils import get_openapi
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, URLSafeTimedSerializer
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from auth_store import AuthStore
from openapi_spec import build_openapi_spec
from sync_store import SyncError, SyncStore
from token_store import TokenStore


TOKEN_SALT = "notedown-sync-token"
BASE_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = BASE_DIR / "frontend" / "dist"
FRONTEND_PUBLIC_DIR = BASE_DIR / "frontend" / "public"
MAX_EDITOR_REQUEST_BYTES = 25 * 1024 * 1024

SWAGGER_UI_HTML = """<!doctype html>
<html lang="ko">
  <head>
    <meta charset="utf-8">
    <title>Notedown Sync API</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <link rel="stylesheet" href="https://unpkg.com/swagger-ui-dist@5/swagger-ui.css">
    <style>body { margin: 0; background: #fafafa; } .swagger-ui .topbar { display: none; }</style>
  </head>
  <body>
    <div id="swagger-ui"></div>
    <script src="https://unpkg.com/swagger-ui-dist@5/swagger-ui-bundle.js"></script>
    <script>
      window.ui = SwaggerUIBundle({
        url: "/api/openapi.json", dom_id: "#swagger-ui", deepLinking: true,
        persistAuthorization: true
      });
    </script>
  </body>
</html>
"""


class ApiError(Exception):
    def __init__(self, error, status_code=400):
        self.error = error
        self.status_code = status_code


class EditorBodyLimitMiddleware:
    """Bound editor request streams before JSON/multipart parsers allocate them."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope["path"].startswith("/api/editor/"):
            return await self.app(scope, receive, send)
        error = {"error": "request_too_large", "message": "업로드 요청은 25 MB까지 허용됩니다."}
        headers = dict(scope.get("headers", []))
        try:
            declared_size = int(headers.get(b"content-length", b"0"))
        except ValueError:
            declared_size = 0
        if declared_size > MAX_EDITOR_REQUEST_BYTES:
            return await JSONResponse(error, status_code=413)(scope, receive, send)
        received = 0

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > MAX_EDITOR_REQUEST_BYTES:
                    raise StarletteHTTPException(status_code=413, detail=error)
            return message

        await self.app(scope, limited_receive, send)


def client_connection_info(request: Request):
    forwarded_for = request.headers.get("X-Forwarded-For")
    forwarded_ip = forwarded_for.split(",", 1)[0].strip() if forwarded_for else None
    remote_address = request.client.host if request.client else None
    return {
        "ipAddress": request.headers.get("X-Real-IP") or forwarded_ip or remote_address,
        "remoteAddress": remote_address,
        "forwardedFor": forwarded_for,
        "userAgent": request.headers.get("User-Agent"),
        "requestHost": request.headers.get("host", request.url.netloc),
    }


async def json_object(request: Request):
    """Keep the sync protocol's JSON error shape instead of returning a 422."""
    try:
        data = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise SyncError("JSON object payload is required.") from None
    if not isinstance(data, dict):
        raise SyncError("JSON object payload is required.")
    return data


async def optional_json_object(request: Request):
    """Authentication endpoints historically treated empty input as an object."""
    try:
        data = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def create_app(test_config=None):
    config = {
        "NOTE_SYNC_STORAGE": os.environ.get("NOTE_SYNC_STORAGE", "storage"),
        "NOTE_SYNC_USERNAME": os.environ.get("NOTE_SYNC_USERNAME"),
        "NOTE_SYNC_PASSWORD": os.environ.get("NOTE_SYNC_PASSWORD"),
        "NOTE_SYNC_PASSWORD_HASH": os.environ.get("NOTE_SYNC_PASSWORD_HASH"),
        "NOTE_SYNC_AUTH_FILE": os.environ.get("NOTE_SYNC_AUTH_FILE"),
        "NOTE_SYNC_SECRET": os.environ.get("NOTE_SYNC_SECRET", "dev-secret-change-me"),
        "NOTE_SYNC_CORS_ORIGIN": os.environ.get("NOTE_SYNC_CORS_ORIGIN", "*"),
        "NOTE_SYNC_PUBLIC_ORIGIN": os.environ.get("NOTE_SYNC_PUBLIC_ORIGIN", ""),
        "NOTE_SYNC_SECURE_COOKIES": os.environ.get("NOTE_SYNC_SECURE_COOKIES", "false"),
    }
    if test_config:
        config.update(test_config)

    public_origin = config["NOTE_SYNC_PUBLIC_ORIGIN"].strip().rstrip("/")
    public_url = urlsplit(public_origin)
    if public_origin and (
        public_url.scheme not in {"http", "https"}
        or not public_url.hostname
        or public_url.username is not None
        or public_url.path
        or public_url.query
        or public_url.fragment
    ):
        raise ValueError("NOTE_SYNC_PUBLIC_ORIGIN must be an HTTP(S) origin without a path")

    app = FastAPI(title="Notedown", version="2.0.2", openapi_url=None,
                  docs_url=None, redoc_url=None)
    auth_store = AuthStore(config)
    store = SyncStore(config["NOTE_SYNC_STORAGE"])
    token_store = TokenStore(config["NOTE_SYNC_STORAGE"])
    store.initialize()
    token_store.initialize()
    app.state.config = config
    app.state.store = store
    app.state.auth_store = auth_store
    app.state.token_store = token_store
    app.state.client_connection_info = client_connection_info
    app.add_middleware(EditorBodyLimitMiddleware)

    # A separate cookie name ensures old Flask cookies cannot be mistaken for
    # Starlette sessions. Bearer tokens keep their original signing format.
    app.add_middleware(
        SessionMiddleware,
        secret_key=config["NOTE_SYNC_SECRET"],
        session_cookie="notedown_session",
        same_site="lax",
        https_only=str(config["NOTE_SYNC_SECURE_COOKIES"]).lower() in {"1", "true", "yes"},
        max_age=60 * 60 * 24 * 14,
    )
    templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
    app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
    if (FRONTEND_DIR / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=str(FRONTEND_DIR / "assets")), name="assets")

    def public_asset(relative_path, media_type, extra_headers=None):
        # Vite copies public assets into dist. The source fallback also permits
        # running the API before the frontend has been built. Resolve each path
        # to prevent symlinks from exposing files outside its public directory.
        for directory in (FRONTEND_DIR, FRONTEND_PUBLIC_DIR):
            root = directory.resolve()
            candidate = (root / relative_path).resolve()
            if candidate.is_relative_to(root) and candidate.is_file():
                return FileResponse(candidate, media_type=media_type, headers={
                    "Cache-Control": "no-cache",
                    "X-Content-Type-Options": "nosniff",
                    **(extra_headers or {}),
                })
        raise StarletteHTTPException(status_code=404, detail="Not Found")

    @app.api_route("/manifest.webmanifest", methods=["GET", "HEAD"], include_in_schema=False)
    def web_manifest():
        return public_asset("manifest.webmanifest", "application/manifest+json")

    @app.api_route("/sw.js", methods=["GET", "HEAD"], include_in_schema=False)
    def service_worker():
        return public_asset("sw.js", "text/javascript", {"Service-Worker-Allowed": "/"})

    @app.api_route("/offline.html", methods=["GET", "HEAD"], include_in_schema=False)
    def offline_page():
        return public_asset("offline.html", "text/html")

    @app.api_route("/favicon.ico", methods=["GET", "HEAD"], include_in_schema=False)
    def favicon():
        return public_asset("favicon.ico", "image/vnd.microsoft.icon")

    @app.api_route("/icons/{filename:path}", methods=["GET", "HEAD"], include_in_schema=False)
    def app_icon(filename: str):
        if len(filename.encode("utf-8")) > 255 or "/" in filename or "\\" in filename or "\x00" in filename:
            raise StarletteHTTPException(status_code=404, detail="Not Found")
        media_type = {".png": "image/png", ".svg": "image/svg+xml",
                      ".ico": "image/vnd.microsoft.icon"}.get(Path(filename).suffix.lower())
        if not media_type:
            raise StarletteHTTPException(status_code=404, detail="Not Found")
        return public_asset(Path("icons") / filename, media_type)

    def expected_session_origin(request):
        # A TLS-terminating proxy can use HTTP on the private connection.
        # Match the configured public host before using its external origin.
        base_url = request.base_url
        if public_origin and base_url.netloc.lower() == public_url.netloc.lower():
            return public_origin
        return str(base_url).rstrip("/")

    @app.middleware("http")
    async def add_cors_headers(request, call_next):
        # Desktop bearer clients retain cross-origin access. Browser session
        # writes, including multipart uploads, must come from this site.
        origin = request.headers.get("origin")
        cross_site = request.headers.get("sec-fetch-site") == "cross-site"
        if (
            request.method in {"POST", "PUT", "PATCH", "DELETE"}
            and request.cookies.get("notedown_session")
            and (cross_site or (origin and origin.rstrip("/") != expected_session_origin(request)))
        ):
            response = JSONResponse({"error": "cross_origin_session_write"}, status_code=403)
        else:
            response = await call_next(request)
        response.headers["Access-Control-Allow-Origin"] = config["NOTE_SYNC_CORS_ORIGIN"]
        response.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, PATCH, DELETE, OPTIONS"
        return response

    @app.exception_handler(ApiError)
    async def handle_api_error(request, error):
        return JSONResponse({"error": error.error}, status_code=error.status_code)

    @app.exception_handler(SyncError)
    async def handle_sync_error(request, error):
        return JSONResponse({"error": "invalid_sync_payload", "message": str(error)}, status_code=400)

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_error(request, error):
        payload = error.detail if isinstance(error.detail, dict) else {"detail": error.detail}
        return JSONResponse(payload, status_code=error.status_code, headers=error.headers)

    def serializer():
        return URLSafeTimedSerializer(config["NOTE_SYNC_SECRET"], salt=TOKEN_SALT)

    def authenticate(request: Request):
        if request.session.get("admin_user"):
            request.state.current_user = request.session["admin_user"]
            request.state.current_token_id = None
            return request.state.current_user
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            raise ApiError("missing_bearer_token", 401)
        try:
            payload = serializer().loads(header[len("Bearer "):].strip())
        except BadSignature:
            raise ApiError("invalid_token", 401) from None
        if not isinstance(payload, dict):
            raise ApiError("invalid_token", 401)
        token_id = payload.get("jti")
        record = token_store.touch(token_id, client_connection_info(request))
        if record is None or record.get("username") != payload.get("sub"):
            raise ApiError("invalid_token", 401)
        request.state.current_user = record["username"]
        request.state.current_token_id = token_id
        return request.state.current_user

    app.state.authenticate = authenticate
    protected = [Depends(authenticate)]

    def issue_token(username, request):
        record = token_store.issue(username, client_connection_info(request))
        return serializer().dumps({"sub": username, "jti": record["id"]}), record

    def account_payload():
        return {"username": auth_store.current_username(), "source": auth_store.source(),
                "editable": auth_store.can_update_credentials()}

    def template(name, request):
        if (FRONTEND_DIR / "index.html").is_file():
            return FileResponse(FRONTEND_DIR / "index.html", headers={"Cache-Control": "no-cache"})
        # Accept the legacy Flask spelling while templates are being migrated.
        def url_for(name, **params):
            if name == "static" and "filename" in params:
                params["path"] = params.pop("filename")
            return request.url_for(name, **params)
        return templates.TemplateResponse(request=request, name=name, context={"url_for": url_for})

    def ui_redirect(request):
        if not auth_store.is_configured():
            return "/setup"
        return "/admin" if request.session.get("admin_user") else "/login"

    @app.get("/", include_in_schema=False)
    def index(request: Request):
        return RedirectResponse(ui_redirect(request), status_code=302)

    @app.get("/setup", response_class=HTMLResponse, include_in_schema=False)
    def setup_page(request: Request):
        if auth_store.is_configured():
            return RedirectResponse(ui_redirect(request), status_code=302)
        return template("setup.html", request)

    @app.get("/login", response_class=HTMLResponse, include_in_schema=False)
    def login_page(request: Request):
        target = ui_redirect(request)
        if target != "/login":
            return RedirectResponse(target, status_code=302)
        return template("login.html", request)

    @app.get("/admin", response_class=HTMLResponse, include_in_schema=False)
    @app.get("/editor", response_class=HTMLResponse, include_in_schema=False)
    def admin(request: Request):
        target = ui_redirect(request)
        if target != "/admin":
            return RedirectResponse(target, status_code=302)
        return template("admin.html", request)

    @app.get("/logout", include_in_schema=False)
    def logout_page(request: Request):
        request.session.clear()
        return RedirectResponse("/login" if auth_store.is_configured() else "/setup", status_code=302)

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    @app.get("/api/openapi.json", include_in_schema=False)
    @app.get("/openapi.json", include_in_schema=False)
    def openapi_json(request: Request):
        spec = build_openapi_spec(str(request.base_url).rstrip("/"))
        generated = get_openapi(title=app.title, version=app.version, routes=app.routes)
        for path, methods in generated["paths"].items():
            spec["paths"].setdefault(path, methods)
        for section, values in generated.get("components", {}).items():
            target = spec.setdefault("components", {}).setdefault(section, {})
            for key, value in values.items():
                target.setdefault(key, value)
        return spec

    @app.get("/api/docs", response_class=HTMLResponse, include_in_schema=False)
    @app.get("/docs", response_class=HTMLResponse, include_in_schema=False)
    def swagger_docs():
        return HTMLResponse(SWAGGER_UI_HTML)

    @app.options("/api/{path:path}", include_in_schema=False)
    def options(path: str):
        return Response(status_code=204)

    @app.post("/api/login")
    def login(request: Request, data: dict = Depends(optional_json_object)):
        if not auth_store.is_configured():
            raise ApiError("setup_required", 409)
        username, password = data.get("username"), data.get("password")
        if not username or not password:
            raise ApiError("username_and_password_required")
        if not auth_store.verify(username, password):
            raise ApiError("invalid_credentials", 401)
        request.session["admin_user"] = username
        token, token_record = issue_token(username, request)
        return {"accessToken": token, "tokenType": "Bearer", "tokenId": token_record["id"]}

    @app.get("/api/setup/status")
    def setup_status():
        return {"configured": auth_store.is_configured(), "source": auth_store.source()}

    @app.post("/api/setup")
    def setup_auth(request: Request, data: dict = Depends(optional_json_object)):
        try:
            username = auth_store.setup(data.get("username"), data.get("password"))
        except ValueError as error:
            raise ApiError(str(error), 409 if str(error) == "auth_already_configured" else 400) from None
        request.session["admin_user"] = username
        token, token_record = issue_token(username, request)
        return {"configured": True, "username": username, "accessToken": token,
                "tokenType": "Bearer", "tokenId": token_record["id"]}

    @app.post("/api/logout")
    def logout(request: Request):
        request.session.clear()
        return {"status": "ok"}

    @app.get("/api/admin/account", dependencies=protected)
    def admin_account():
        return account_payload()

    @app.post("/api/admin/account", dependencies=protected)
    def update_admin_account(request: Request, data: dict = Depends(optional_json_object)):
        if data.get("password") != data.get("confirmPassword"):
            raise ApiError("password_confirmation_mismatch")
        try:
            username = auth_store.update_credentials(data.get("currentPassword"), data.get("username"), data.get("password"))
        except ValueError as error:
            status = {"auth_update_not_supported": 409, "invalid_current_password": 401}.get(str(error), 400)
            raise ApiError(str(error), status) from None
        request.session["admin_user"] = username
        return account_payload()

    @app.get("/api/admin/tokens", dependencies=protected)
    def admin_tokens():
        return {"tokens": token_store.list_tokens()}

    @app.delete("/api/admin/tokens/{token_id}", dependencies=protected)
    def delete_admin_token(token_id: str):
        if token_store.delete(token_id) is None:
            raise ApiError("token_not_found", 404)
        return {"status": "deleted", "tokenId": token_id}

    @app.post("/api/admin/storage/reset", dependencies=protected)
    def reset_admin_storage(request: Request, payload: dict = Depends(optional_json_object)):
        if payload.get("confirm") != "RESET":
            raise SyncError('confirm must be "RESET".')
        return store.reset_repository(user=request.state.current_user)

    @app.get("/api/manifest", dependencies=protected)
    def manifest():
        return store.manifest()

    @app.get("/api/files/{relative_path:path}", dependencies=protected)
    def read_file(relative_path: str):
        return store.file_payload(relative_path)

    @app.get("/api/attachments/{relative_path:path}", dependencies=protected)
    def read_attachment(relative_path: str):
        return store.attachment_payload(relative_path)

    @app.get("/api/admin/files/{relative_path:path}/history", dependencies=protected)
    def admin_file_history(relative_path: str):
        return store.file_history(relative_path)

    @app.get("/api/admin/files/{relative_path:path}/history/{commit}", dependencies=protected)
    def admin_file_version(relative_path: str, commit: str):
        return store.file_version_payload(relative_path, commit)

    @app.post("/api/admin/files/{relative_path:path}/rollback", dependencies=protected)
    def admin_file_rollback(relative_path: str, request: Request, payload: dict = Depends(json_object)):
        return store.rollback_file(relative_path, payload.get("commit"), user=request.state.current_user)

    @app.post("/api/sync/plan", dependencies=protected)
    def plan_sync(request: Request, payload: dict = Depends(json_object)):
        return store.plan_sync(payload, user=request.state.current_user, connection_info=client_connection_info(request))

    @app.post("/api/sync/file", dependencies=protected)
    def sync_file(request: Request, payload: dict = Depends(json_object)):
        return store.sync_file_upload(payload, user=request.state.current_user, connection_info=client_connection_info(request))

    @app.post("/api/sync/attachment", dependencies=protected)
    def sync_attachment(request: Request, payload: dict = Depends(json_object)):
        return store.sync_attachment_upload(payload, user=request.state.current_user, connection_info=client_connection_info(request))

    @app.post("/api/sync", dependencies=protected)
    def sync(request: Request, payload: dict = Depends(json_object)):
        return store.sync(payload, user=request.state.current_user, connection_info=client_connection_info(request))

    from editor_api import register_editor_routes
    register_editor_routes(app)
    return app


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(create_app(), host="0.0.0.0", port=int(os.environ.get("PORT", "5500")), workers=1)
