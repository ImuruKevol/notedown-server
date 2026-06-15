import os
from functools import wraps

from flask import (
    Flask,
    jsonify,
    redirect,
    render_template,
    render_template_string,
    request,
    session,
)
from itsdangerous import BadSignature, URLSafeTimedSerializer

from auth_store import AuthStore
from openapi_spec import build_openapi_spec
from sync_store import SyncError, SyncStore
from token_store import TokenStore


TOKEN_SALT = "notedown-sync-token"


SWAGGER_UI_HTML = """<!doctype html>
<html lang="ko">
  <head>
    <meta charset="utf-8">
    <title>Notedown Sync API</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <link rel="stylesheet" href="https://unpkg.com/swagger-ui-dist@5/swagger-ui.css">
    <style>
      body { margin: 0; background: #fafafa; }
      .swagger-ui .topbar { display: none; }
    </style>
  </head>
  <body>
    <div id="swagger-ui"></div>
    <script src="https://unpkg.com/swagger-ui-dist@5/swagger-ui-bundle.js"></script>
    <script>
      window.ui = SwaggerUIBundle({
        url: "{{ spec_url }}",
        dom_id: "#swagger-ui",
        deepLinking: true,
        persistAuthorization: true
      });
    </script>
  </body>
</html>
"""


def create_app(test_config=None):
    app = Flask(__name__)
    app.config.from_mapping(
        NOTE_SYNC_STORAGE=os.environ.get("NOTE_SYNC_STORAGE", "storage"),
        NOTE_SYNC_USERNAME=os.environ.get("NOTE_SYNC_USERNAME"),
        NOTE_SYNC_PASSWORD=os.environ.get("NOTE_SYNC_PASSWORD"),
        NOTE_SYNC_PASSWORD_HASH=os.environ.get("NOTE_SYNC_PASSWORD_HASH"),
        NOTE_SYNC_AUTH_FILE=os.environ.get("NOTE_SYNC_AUTH_FILE"),
        NOTE_SYNC_SECRET=os.environ.get("NOTE_SYNC_SECRET", "dev-secret-change-me"),
        NOTE_SYNC_CORS_ORIGIN=os.environ.get("NOTE_SYNC_CORS_ORIGIN", "*"),
    )

    if test_config:
        app.config.update(test_config)

    app.json.ensure_ascii = False
    app.secret_key = app.config["NOTE_SYNC_SECRET"]
    auth_store = AuthStore(app.config)
    store = SyncStore(app.config["NOTE_SYNC_STORAGE"])
    token_store = TokenStore(app.config["NOTE_SYNC_STORAGE"])
    store.initialize()
    token_store.initialize()

    @app.after_request
    def add_cors_headers(response):
        response.headers["Access-Control-Allow-Origin"] = app.config[
            "NOTE_SYNC_CORS_ORIGIN"
        ]
        response.headers["Access-Control-Allow-Headers"] = (
            "Authorization, Content-Type"
        )
        response.headers["Access-Control-Allow-Methods"] = (
            "GET, POST, DELETE, OPTIONS"
        )
        return response

    def serializer():
        return URLSafeTimedSerializer(app.config["NOTE_SYNC_SECRET"], salt=TOKEN_SALT)

    def auth_required(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if session.get("admin_user"):
                request.current_user = session["admin_user"]
                request.current_token_id = None
                return view(*args, **kwargs)

            header = request.headers.get("Authorization", "")
            token_prefix = "Bearer "
            if not header.startswith(token_prefix):
                return jsonify({"error": "missing_bearer_token"}), 401

            token = header[len(token_prefix) :].strip()
            try:
                payload = serializer().loads(token)
            except BadSignature:
                return jsonify({"error": "invalid_token"}), 401

            if not isinstance(payload, dict):
                return jsonify({"error": "invalid_token"}), 401

            token_id = payload.get("jti")
            record = token_store.touch(token_id, client_connection_info())
            if record is None or record.get("username") != payload.get("sub"):
                return jsonify({"error": "invalid_token"}), 401

            request.current_user = record["username"]
            request.current_token_id = token_id
            return view(*args, **kwargs)

        return wrapped

    def is_ui_logged_in():
        return bool(session.get("admin_user"))

    def issue_token(username):
        record = token_store.issue(username, client_connection_info())
        token = serializer().dumps({"sub": username, "jti": record["id"]})
        return token, record

    def account_payload():
        return {
            "username": auth_store.current_username(),
            "source": auth_store.source(),
            "editable": auth_store.can_update_credentials(),
        }

    def client_connection_info():
        forwarded_for = request.headers.get("X-Forwarded-For")
        forwarded_ip = forwarded_for.split(",", 1)[0].strip() if forwarded_for else None
        ip_address = (
            request.headers.get("X-Real-IP")
            or forwarded_ip
            or request.remote_addr
        )
        return {
            "ipAddress": ip_address,
            "remoteAddress": request.remote_addr,
            "forwardedFor": forwarded_for,
            "userAgent": request.headers.get("User-Agent"),
            "requestHost": request.host,
        }

    def auth_error_response(error):
        status_by_error = {
            "auth_update_not_supported": 409,
            "invalid_current_password": 401,
            "username_required": 400,
            "password_too_short": 400,
            "password_confirmation_mismatch": 400,
        }
        return jsonify({"error": str(error)}), status_by_error.get(str(error), 400)

    @app.errorhandler(SyncError)
    def handle_sync_error(error):
        return jsonify({"error": "invalid_sync_payload", "message": str(error)}), 400

    @app.get("/")
    def index():
        if not auth_store.is_configured():
            return redirect("/setup")
        if is_ui_logged_in():
            return redirect("/admin")
        return redirect("/login")

    @app.get("/setup")
    def setup_page():
        if auth_store.is_configured():
            return redirect("/admin" if is_ui_logged_in() else "/login")
        return render_template("setup.html")

    @app.get("/login")
    def login_page():
        if not auth_store.is_configured():
            return redirect("/setup")
        if is_ui_logged_in():
            return redirect("/admin")
        return render_template("login.html")

    @app.get("/admin")
    def admin():
        if not auth_store.is_configured():
            return redirect("/setup")
        if not is_ui_logged_in():
            return redirect("/login")
        return render_template("admin.html")

    @app.get("/logout")
    def logout_page():
        session.clear()
        return redirect("/login" if auth_store.is_configured() else "/setup")

    @app.get("/api/health")
    def health():
        return jsonify({"status": "ok"})

    @app.get("/api/openapi.json")
    @app.get("/openapi.json")
    def openapi_json():
        return jsonify(build_openapi_spec(request.url_root.rstrip("/")))

    @app.get("/api/docs")
    @app.get("/docs")
    def swagger_docs():
        return render_template_string(
            SWAGGER_UI_HTML,
            spec_url="/api/openapi.json",
        )

    @app.route("/api/<path:_path>", methods=["OPTIONS"])
    def options(_path):
        return ("", 204)

    @app.post("/api/login")
    def login():
        if not auth_store.is_configured():
            return jsonify({"error": "setup_required"}), 409

        data = request.get_json(silent=True) or {}
        username = data.get("username")
        password = data.get("password")
        if not username or not password:
            return jsonify({"error": "username_and_password_required"}), 400

        if not auth_store.verify(username, password):
            return jsonify({"error": "invalid_credentials"}), 401

        session["admin_user"] = username
        token, token_record = issue_token(username)
        return jsonify(
            {
                "accessToken": token,
                "tokenType": "Bearer",
                "tokenId": token_record["id"],
            }
        )

    @app.get("/api/setup/status")
    def setup_status():
        return jsonify(
            {
                "configured": auth_store.is_configured(),
                "source": auth_store.source(),
            }
        )

    @app.post("/api/setup")
    def setup_auth():
        data = request.get_json(silent=True) or {}
        try:
            username = auth_store.setup(data.get("username"), data.get("password"))
        except ValueError as error:
            status = 409 if str(error) == "auth_already_configured" else 400
            return jsonify({"error": str(error)}), status

        session["admin_user"] = username
        token, token_record = issue_token(username)
        return jsonify(
            {
                "configured": True,
                "username": username,
                "accessToken": token,
                "tokenType": "Bearer",
                "tokenId": token_record["id"],
            }
        )

    @app.post("/api/logout")
    def logout():
        session.clear()
        return jsonify({"status": "ok"})

    @app.get("/api/admin/account")
    @auth_required
    def admin_account():
        return jsonify(account_payload())

    @app.post("/api/admin/account")
    @auth_required
    def update_admin_account():
        data = request.get_json(silent=True) or {}
        if data.get("password") != data.get("confirmPassword"):
            return auth_error_response(ValueError("password_confirmation_mismatch"))

        try:
            username = auth_store.update_credentials(
                data.get("currentPassword"),
                data.get("username"),
                data.get("password"),
            )
        except ValueError as error:
            return auth_error_response(error)

        session["admin_user"] = username
        return jsonify(account_payload())

    @app.get("/api/admin/tokens")
    @auth_required
    def admin_tokens():
        return jsonify({"tokens": token_store.list_tokens()})

    @app.delete("/api/admin/tokens/<token_id>")
    @auth_required
    def delete_admin_token(token_id):
        record = token_store.delete(token_id)
        if record is None:
            return jsonify({"error": "token_not_found"}), 404
        return jsonify({"status": "deleted", "tokenId": token_id})

    @app.get("/api/manifest")
    @auth_required
    def manifest():
        return jsonify(store.manifest())

    @app.get("/api/files/<path:relative_path>")
    @auth_required
    def read_file(relative_path):
        return jsonify(store.file_payload(relative_path))

    @app.get("/api/attachments/<path:relative_path>")
    @auth_required
    def read_attachment(relative_path):
        return jsonify(store.attachment_payload(relative_path))

    @app.get("/api/admin/files/<path:relative_path>/history")
    @auth_required
    def admin_file_history(relative_path):
        return jsonify(store.file_history(relative_path))

    @app.get("/api/admin/files/<path:relative_path>/history/<commit>")
    @auth_required
    def admin_file_version(relative_path, commit):
        return jsonify(store.file_version_payload(relative_path, commit))

    @app.post("/api/admin/files/<path:relative_path>/rollback")
    @auth_required
    def admin_file_rollback(relative_path):
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            raise SyncError("JSON object payload is required.")

        return jsonify(
            store.rollback_file(
                relative_path,
                payload.get("commit"),
                user=request.current_user,
            )
        )

    @app.post("/api/sync/plan")
    @auth_required
    def plan_sync():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            raise SyncError("JSON object payload is required.")

        result = store.plan_sync(
            payload,
            user=request.current_user,
            connection_info=client_connection_info(),
        )
        return jsonify(result)

    @app.post("/api/sync/file")
    @auth_required
    def sync_file():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            raise SyncError("JSON object payload is required.")

        result = store.sync_file_upload(
            payload,
            user=request.current_user,
            connection_info=client_connection_info(),
        )
        return jsonify(result)

    @app.post("/api/sync/attachment")
    @auth_required
    def sync_attachment():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            raise SyncError("JSON object payload is required.")

        result = store.sync_attachment_upload(
            payload,
            user=request.current_user,
            connection_info=client_connection_info(),
        )
        return jsonify(result)

    @app.post("/api/sync")
    @auth_required
    def sync():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            raise SyncError("JSON object payload is required.")

        result = store.sync(
            payload,
            user=request.current_user,
            connection_info=client_connection_info(),
        )
        return jsonify(result)

    return app


if __name__ == "__main__":
    create_app().run(host="0.0.0.0", port=int(os.environ.get("PORT", "5500")), debug=True)
