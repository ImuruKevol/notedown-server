import base64
import unittest
from tempfile import TemporaryDirectory

from app import create_app


class SyncApiTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = TemporaryDirectory()
        self.app = create_app(
            {
                "TESTING": True,
                "NOTE_SYNC_STORAGE": self.tempdir.name,
                "NOTE_SYNC_USERNAME": "tester",
                "NOTE_SYNC_PASSWORD": "secret",
                "NOTE_SYNC_SECRET": "test-secret",
            }
        )
        self.client = self.app.test_client()
        response = self.client.post(
            "/api/login",
            json={"username": "tester", "password": "secret"},
        )
        self.assertEqual(response.status_code, 200)
        login_payload = response.get_json()
        self.assertNotIn("expiresIn", login_payload)
        self.assertIn("tokenId", login_payload)
        self.access_token = login_payload["accessToken"]
        self.token_id = login_payload["tokenId"]
        self.headers = {"Authorization": f"Bearer {self.access_token}"}

    def tearDown(self):
        self.tempdir.cleanup()

    def note_metadata(self, updated_at_ms=10):
        return {
            "version": 1,
            "generatedAt": "2026-05-29T04:58:06.497Z",
            "workspaces": [{"id": "memo", "name": "memo"}],
            "notes": [
                {
                    "id": "note-1",
                    "title": "새 노트",
                    "workspace": "memo",
                    "workspaceName": "memo",
                    "folder": "memo",
                    "fileName": "note.md",
                    "relativePath": "memo/note.md",
                    "updatedAtMs": updated_at_ms,
                }
            ],
        }

    def decoded_content(self, payload):
        return base64.b64decode(payload["content"]).decode("utf-8")

    def test_push_and_manifest(self):
        response = self.client.post(
            "/api/sync",
            headers=self.headers,
            json={
                "clientId": "client-a",
                "baseRevision": 0,
                "metadata": {
                    "lastKnownRevision": 0,
                    "body": {
                        "version": 1,
                        "workspaces": [{"id": "memo", "name": "memo"}],
                        "notes": [],
                    },
                },
                "files": [
                    {
                        "relativePath": "memo/note.md",
                        "lastKnownRevision": 0,
                        "contentEncoding": "utf-8",
                        "content": "# hello\n",
                        "updatedAtMs": 1,
                    }
                ],
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["serverRevision"], 2)
        self.assertEqual(payload["accepted"][0]["relativePath"], "memo/note.md")

        manifest = self.client.get("/api/manifest", headers=self.headers).get_json()
        self.assertEqual(manifest["serverRevision"], 2)
        self.assertEqual(manifest["files"][0]["relativePath"], "memo/note.md")

    def test_plan_records_client_details(self):
        response = self.client.post(
            "/api/sync/plan",
            headers={**self.headers, "User-Agent": "Notedown/1.2"},
            environ_overrides={"REMOTE_ADDR": "10.0.0.5"},
            json={
                "clientId": "client-a",
                "baseRevision": 0,
                "clientInfo": {
                    "hostname": "macbook-pro",
                    "ipAddress": "192.168.0.12",
                    "platform": "macOS",
                    "appVersion": "1.2.3",
                    "browser": "Chrome",
                    "browserVersion": "125.0.0.0",
                },
                "metadata": {
                    "lastKnownRevision": 0,
                    "body": {
                        "version": 1,
                        "workspaces": [],
                        "notes": [],
                    },
                },
            },
        )

        self.assertEqual(response.status_code, 200)
        client = response.get_json()["manifest"]["clients"]["client-a"]
        self.assertEqual(client["clientInfo"]["hostname"], "macbook-pro")
        self.assertEqual(client["clientInfo"]["appVersion"], "1.2.3")
        self.assertEqual(client["clientInfo"]["browser"], "Chrome")
        self.assertEqual(client["clientInfo"]["browserVersion"], "125.0.0.0")
        self.assertEqual(client["connectionInfo"]["ipAddress"], "10.0.0.5")
        self.assertEqual(client["connectionInfo"]["userAgent"], "Notedown/1.2")
        self.assertEqual(client["lastAction"], "plan")
        self.assertIn("lastPlanAt", client)

    def test_admin_can_list_and_revoke_tokens(self):
        tokens_response = self.client.get("/api/admin/tokens")
        self.assertEqual(tokens_response.status_code, 200)

        tokens = tokens_response.get_json()["tokens"]
        token_ids = {token["id"] for token in tokens}
        self.assertIn(self.token_id, token_ids)

        bearer_client = self.app.test_client()
        authenticated = bearer_client.get("/api/manifest", headers=self.headers)
        self.assertEqual(authenticated.status_code, 200)

        touched_tokens = self.client.get("/api/admin/tokens").get_json()["tokens"]
        touched = next(token for token in touched_tokens if token["id"] == self.token_id)
        self.assertIsNotNone(touched["lastUsedAt"])

        delete_response = self.client.delete(f"/api/admin/tokens/{self.token_id}")
        self.assertEqual(delete_response.status_code, 200)
        self.assertEqual(delete_response.get_json()["status"], "deleted")

        remaining = self.client.get("/api/admin/tokens").get_json()["tokens"]
        self.assertNotIn(self.token_id, {token["id"] for token in remaining})

        rejected = self.app.test_client().get("/api/manifest", headers=self.headers)
        self.assertEqual(rejected.status_code, 401)
        self.assertEqual(rejected.get_json()["error"], "invalid_token")

    def test_update_file_backed_admin_credentials(self):
        with TemporaryDirectory() as tempdir:
            app = create_app(
                {
                    "TESTING": True,
                    "NOTE_SYNC_STORAGE": tempdir,
                    "NOTE_SYNC_USERNAME": None,
                    "NOTE_SYNC_PASSWORD": None,
                    "NOTE_SYNC_PASSWORD_HASH": None,
                    "NOTE_SYNC_SECRET": "file-auth-secret",
                }
            )
            client = app.test_client()
            setup = client.post(
                "/api/setup",
                json={"username": "admin", "password": "old-password"},
            )
            self.assertEqual(setup.status_code, 200)

            update = client.post(
                "/api/admin/account",
                json={
                    "username": "owner",
                    "currentPassword": "old-password",
                    "password": "new-password",
                    "confirmPassword": "new-password",
                },
            )
            self.assertEqual(update.status_code, 200)
            self.assertEqual(update.get_json()["username"], "owner")

            old_login = client.post(
                "/api/login",
                json={"username": "admin", "password": "old-password"},
            )
            self.assertEqual(old_login.status_code, 401)

            new_login = client.post(
                "/api/login",
                json={"username": "owner", "password": "new-password"},
            )
            self.assertEqual(new_login.status_code, 200)

    def test_reject_environment_admin_credentials_update(self):
        response = self.client.post(
            "/api/admin/account",
            json={
                "username": "owner",
                "currentPassword": "secret",
                "password": "new-password",
                "confirmPassword": "new-password",
            },
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()["error"], "auth_update_not_supported")

    def test_reject_admin_password_confirmation_mismatch(self):
        with TemporaryDirectory() as tempdir:
            app = create_app(
                {
                    "TESTING": True,
                    "NOTE_SYNC_STORAGE": tempdir,
                    "NOTE_SYNC_USERNAME": None,
                    "NOTE_SYNC_PASSWORD": None,
                    "NOTE_SYNC_PASSWORD_HASH": None,
                    "NOTE_SYNC_SECRET": "file-auth-secret",
                }
            )
            client = app.test_client()
            setup = client.post(
                "/api/setup",
                json={"username": "admin", "password": "old-password"},
            )
            self.assertEqual(setup.status_code, 200)

            response = client.post(
                "/api/admin/account",
                json={
                    "username": "admin",
                    "currentPassword": "old-password",
                    "password": "new-password",
                    "confirmPassword": "different-password",
                },
            )

            self.assertEqual(response.status_code, 400)
            self.assertEqual(
                response.get_json()["error"],
                "password_confirmation_mismatch",
            )

    def test_plan_then_single_file_upload(self):
        metadata = self.note_metadata()

        plan_response = self.client.post(
            "/api/sync/plan",
            headers=self.headers,
            json={
                "clientId": "client-a",
                "baseRevision": 0,
                "metadata": {"lastKnownRevision": 0, "body": metadata},
            },
        )

        self.assertEqual(plan_response.status_code, 200)
        plan = plan_response.get_json()
        self.assertEqual(plan["status"], "ok")
        self.assertEqual(plan["serverRevision"], 0)
        self.assertEqual(plan["plan"]["uploadFiles"][0]["relativePath"], "memo/note.md")

        upload_response = self.client.post(
            "/api/sync/file",
            headers=self.headers,
            json={
                "clientId": "client-a",
                "baseRevision": 0,
                "relativePath": "memo/note.md",
                "lastKnownRevision": 0,
                "updatedAtMs": 10,
                "contentEncoding": "utf-8",
                "content": "# hello\n",
                "workspace": {"id": "memo", "name": "memo"},
                "note": metadata["notes"][0],
            },
        )

        self.assertEqual(upload_response.status_code, 200)
        upload = upload_response.get_json()
        self.assertEqual(upload["status"], "ok")
        self.assertEqual(upload["file"]["status"], "accepted")
        self.assertEqual(upload["metadata"]["status"], "accepted")
        self.assertEqual(upload["serverRevision"], 2)

    def test_file_upload_creates_git_history(self):
        first = self.client.post(
            "/api/sync/file",
            headers=self.headers,
            json={
                "clientId": "client-a",
                "baseRevision": 0,
                "relativePath": "memo/note.md",
                "lastKnownRevision": 0,
                "contentEncoding": "utf-8",
                "content": "first\n",
            },
        ).get_json()

        second = self.client.post(
            "/api/sync/file",
            headers=self.headers,
            json={
                "clientId": "client-a",
                "baseRevision": first["serverRevision"],
                "relativePath": "memo/note.md",
                "lastKnownRevision": first["file"]["revision"],
                "contentEncoding": "utf-8",
                "content": "second\n",
            },
        ).get_json()

        self.assertEqual(second["file"]["status"], "accepted")
        self.assertIn("gitCommit", second["file"])

        history = self.client.get(
            "/api/admin/files/memo/note.md/history",
            headers=self.headers,
        )
        self.assertEqual(history.status_code, 200)
        commits = history.get_json()["commits"]
        self.assertEqual(len(commits), 2)
        self.assertEqual(commits[0]["message"], "Update memo/note.md")
        self.assertFalse(commits[0]["deleted"])
        self.assertEqual(commits[0]["size"], len("second\n"))

    def test_admin_can_preview_and_rollback_git_version(self):
        first = self.client.post(
            "/api/sync/file",
            headers=self.headers,
            json={
                "clientId": "client-a",
                "baseRevision": 0,
                "relativePath": "memo/note.md",
                "lastKnownRevision": 0,
                "contentEncoding": "utf-8",
                "content": "first\n",
            },
        ).get_json()

        self.client.post(
            "/api/sync/file",
            headers=self.headers,
            json={
                "clientId": "client-a",
                "baseRevision": first["serverRevision"],
                "relativePath": "memo/note.md",
                "lastKnownRevision": first["file"]["revision"],
                "contentEncoding": "utf-8",
                "content": "second\n",
            },
        )

        commits = self.client.get(
            "/api/admin/files/memo/note.md/history",
            headers=self.headers,
        ).get_json()["commits"]
        older_commit = commits[-1]["commit"]

        version = self.client.get(
            f"/api/admin/files/memo/note.md/history/{older_commit}",
            headers=self.headers,
        )
        self.assertEqual(version.status_code, 200)
        self.assertEqual(self.decoded_content(version.get_json()), "first\n")

        rollback = self.client.post(
            "/api/admin/files/memo/note.md/rollback",
            headers=self.headers,
            json={"commit": older_commit},
        )
        self.assertEqual(rollback.status_code, 200)
        rollback_payload = rollback.get_json()
        self.assertEqual(rollback_payload["status"], "accepted")
        self.assertEqual(rollback_payload["rolledBackToCommit"], older_commit)

        current = self.client.get(
            "/api/files/memo/note.md",
            headers=self.headers,
        ).get_json()
        self.assertEqual(self.decoded_content(current), "first\n")
        self.assertEqual(current["revision"], rollback_payload["revision"])

        next_history = self.client.get(
            "/api/admin/files/memo/note.md/history",
            headers=self.headers,
        ).get_json()["commits"]
        self.assertTrue(next_history[0]["message"].startswith("Rollback memo/note.md"))

    def test_rollback_updates_metadata_and_sync_plan(self):
        first_metadata = self.note_metadata(updated_at_ms=10)
        first = self.client.post(
            "/api/sync/file",
            headers=self.headers,
            json={
                "clientId": "client-a",
                "baseRevision": 0,
                "relativePath": "memo/note.md",
                "lastKnownRevision": 0,
                "updatedAtMs": 10,
                "contentEncoding": "utf-8",
                "content": "first\n",
                "workspace": {"id": "memo", "name": "memo"},
                "note": first_metadata["notes"][0],
            },
        ).get_json()

        second_metadata = self.note_metadata(updated_at_ms=20)
        second = self.client.post(
            "/api/sync/file",
            headers=self.headers,
            json={
                "clientId": "client-a",
                "baseRevision": first["serverRevision"],
                "relativePath": "memo/note.md",
                "lastKnownRevision": first["file"]["revision"],
                "updatedAtMs": 20,
                "contentEncoding": "utf-8",
                "content": "second\n",
                "workspace": {"id": "memo", "name": "memo"},
                "note": second_metadata["notes"][0],
            },
        ).get_json()

        commits = self.client.get(
            "/api/admin/files/memo/note.md/history",
            headers=self.headers,
        ).get_json()["commits"]
        older_commit = commits[-1]["commit"]

        rollback = self.client.post(
            "/api/admin/files/memo/note.md/rollback",
            headers=self.headers,
            json={"commit": older_commit},
        ).get_json()

        self.assertEqual(rollback["metadata"]["status"], "accepted")
        self.assertGreater(
            rollback["manifest"]["metadata"]["revision"],
            second["manifest"]["metadata"]["revision"],
        )

        plan_response = self.client.post(
            "/api/sync/plan",
            headers=self.headers,
            json={
                "clientId": "client-a",
                "baseRevision": second["serverRevision"],
                "metadata": {
                    "lastKnownRevision": second["manifest"]["metadata"]["revision"],
                    "body": second_metadata,
                },
                "knownFiles": [
                    {
                        "relativePath": "memo/note.md",
                        "lastKnownRevision": second["file"]["revision"],
                        "contentHash": second["file"]["contentHash"],
                    }
                ],
            },
        )

        self.assertEqual(plan_response.status_code, 200)
        plan = plan_response.get_json()
        self.assertEqual(plan["metadata"]["status"], "diverged")
        server_note = plan["metadata"]["serverMetadata"]["notes"][0]
        self.assertGreater(server_note["updatedAtMs"], 20)
        self.assertEqual(
            plan["plan"]["downloadFiles"][0]["relativePath"],
            "memo/note.md",
        )

    def test_plan_downloads_file_changed_without_metadata_timestamp(self):
        metadata = self.note_metadata(updated_at_ms=10)
        first = self.client.post(
            "/api/sync/file",
            headers=self.headers,
            json={
                "clientId": "client-a",
                "baseRevision": 0,
                "relativePath": "memo/note.md",
                "lastKnownRevision": 0,
                "updatedAtMs": 10,
                "contentEncoding": "utf-8",
                "content": "first\n",
                "workspace": {"id": "memo", "name": "memo"},
                "note": metadata["notes"][0],
            },
        ).get_json()

        second = self.client.post(
            "/api/sync/file",
            headers=self.headers,
            json={
                "clientId": "client-a",
                "baseRevision": first["serverRevision"],
                "relativePath": "memo/note.md",
                "lastKnownRevision": first["file"]["revision"],
                "updatedAtMs": 10,
                "contentEncoding": "utf-8",
                "content": "second\n",
                "workspace": {"id": "memo", "name": "memo"},
                "note": metadata["notes"][0],
            },
        ).get_json()

        self.assertEqual(second["metadata"]["status"], "unchanged")

        plan_response = self.client.post(
            "/api/sync/plan",
            headers=self.headers,
            json={
                "clientId": "client-a",
                "baseRevision": first["serverRevision"],
                "metadata": {
                    "lastKnownRevision": first["manifest"]["metadata"]["revision"],
                    "body": metadata,
                },
                "knownFiles": [
                    {
                        "relativePath": "memo/note.md",
                        "lastKnownRevision": first["file"]["revision"],
                        "contentHash": first["file"]["contentHash"],
                    }
                ],
            },
        )

        self.assertEqual(plan_response.status_code, 200)
        plan = plan_response.get_json()["plan"]
        self.assertEqual(plan["downloadFiles"][0]["reason"], "server_file_changed")
        self.assertEqual(plan["downloadFiles"][0]["relativePath"], "memo/note.md")

    def test_single_file_upload_conflict(self):
        first = self.client.post(
            "/api/sync/file",
            headers=self.headers,
            json={
                "clientId": "client-a",
                "baseRevision": 0,
                "relativePath": "memo/note.md",
                "lastKnownRevision": 0,
                "contentEncoding": "utf-8",
                "content": "first\n",
            },
        ).get_json()
        first_revision = first["file"]["revision"]

        self.client.post(
            "/api/sync/file",
            headers=self.headers,
            json={
                "clientId": "client-b",
                "baseRevision": first_revision,
                "relativePath": "memo/note.md",
                "lastKnownRevision": first_revision,
                "contentEncoding": "utf-8",
                "content": "second\n",
            },
        )

        stale = self.client.post(
            "/api/sync/file",
            headers=self.headers,
            json={
                "clientId": "client-a",
                "baseRevision": first_revision,
                "relativePath": "memo/note.md",
                "lastKnownRevision": first_revision,
                "contentEncoding": "utf-8",
                "content": "stale\n",
            },
        )

        self.assertEqual(stale.status_code, 200)
        payload = stale.get_json()
        self.assertEqual(payload["status"], "conflict")
        self.assertEqual(payload["file"]["relativePath"], "memo/note.md")

    def test_conflict_when_stale_client_overwrites_changed_file(self):
        first = self.client.post(
            "/api/sync",
            headers=self.headers,
            json={
                "clientId": "client-a",
                "baseRevision": 0,
                "files": [
                    {
                        "relativePath": "memo/note.md",
                        "lastKnownRevision": 0,
                        "contentEncoding": "utf-8",
                        "content": "server version\n",
                    }
                ],
            },
        ).get_json()
        file_revision = first["accepted"][0]["revision"]

        self.client.post(
            "/api/sync",
            headers=self.headers,
            json={
                "clientId": "client-b",
                "baseRevision": file_revision,
                "files": [
                    {
                        "relativePath": "memo/note.md",
                        "lastKnownRevision": file_revision,
                        "contentEncoding": "utf-8",
                        "content": "changed elsewhere\n",
                    }
                ],
            },
        )

        stale = self.client.post(
            "/api/sync",
            headers=self.headers,
            json={
                "clientId": "client-a",
                "baseRevision": file_revision,
                "files": [
                    {
                        "relativePath": "memo/note.md",
                        "lastKnownRevision": file_revision,
                        "contentEncoding": "utf-8",
                        "content": "my local edit\n",
                    }
                ],
            },
        )

        self.assertEqual(stale.status_code, 200)
        payload = stale.get_json()
        self.assertEqual(payload["status"], "conflict")
        self.assertEqual(payload["conflicts"][0]["relativePath"], "memo/note.md")
        self.assertEqual(payload["conflicts"][0]["serverFile"]["contentEncoding"], "base64")

    def test_reject_path_traversal(self):
        response = self.client.post(
            "/api/sync",
            headers=self.headers,
            json={
                "clientId": "client-a",
                "baseRevision": 0,
                "files": [
                    {
                        "relativePath": "../secret.md",
                        "contentEncoding": "utf-8",
                        "content": "nope",
                    }
                ],
            },
        )

        self.assertEqual(response.status_code, 400)

    def test_openapi_document_and_swagger_ui_are_public(self):
        spec_response = self.client.get("/api/openapi.json")
        self.assertEqual(spec_response.status_code, 200)

        spec = spec_response.get_json()
        self.assertEqual(spec["openapi"], "3.1.0")
        self.assertIn("/api/sync", spec["paths"])
        self.assertIn("/api/sync/plan", spec["paths"])
        self.assertIn("/api/sync/file", spec["paths"])
        self.assertIn("/api/setup", spec["paths"])
        self.assertIn("/api/setup/status", spec["paths"])
        self.assertIn("/api/admin/tokens", spec["paths"])
        self.assertIn("/api/admin/tokens/{token_id}", spec["paths"])
        self.assertIn("/api/admin/files/{relative_path}/history", spec["paths"])
        self.assertIn(
            "/api/admin/files/{relative_path}/history/{commit}",
            spec["paths"],
        )
        self.assertIn("/api/admin/files/{relative_path}/rollback", spec["paths"])
        self.assertIn("bearerAuth", spec["components"]["securitySchemes"])

        schemas = spec["components"]["schemas"]
        self.assertIn("tokenId", schemas["LoginResponse"]["required"])
        self.assertIn("tokenId", schemas["LoginResponse"]["properties"])
        self.assertNotIn("expiresIn", schemas["LoginResponse"]["required"])
        self.assertNotIn("expiresIn", schemas["LoginResponse"]["properties"])
        self.assertIn("tokenId", schemas["SetupResponse"]["required"])
        self.assertIn("tokenId", schemas["SetupResponse"]["properties"])
        self.assertNotIn("expiresIn", schemas["SetupResponse"]["required"])
        self.assertNotIn("expiresIn", schemas["SetupResponse"]["properties"])
        self.assertIn("TokenRecord", schemas)
        self.assertIn("TokenListResponse", schemas)
        self.assertIn("TokenDeleteResponse", schemas)
        self.assertIn("FileHistoryCommit", schemas)
        self.assertIn("FileHistoryResponse", schemas)
        self.assertIn("FileVersionResponse", schemas)
        self.assertIn("FileRollbackRequest", schemas)
        self.assertIn("FileRollbackResponse", schemas)

        client_info = schemas["ClientInfo"]
        self.assertIn("ipAddress", client_info["properties"])
        self.assertIn("browser", client_info["properties"])
        self.assertIn("browserVersion", client_info["properties"])
        self.assertIn("userAgent", client_info["properties"])
        self.assertIn("ConnectionInfo", schemas)
        self.assertIn("ipAddress", schemas["ConnectionInfo"]["properties"])
        self.assertIn("forwardedFor", schemas["ConnectionInfo"]["properties"])
        self.assertIn("userAgent", schemas["ConnectionInfo"]["properties"])
        self.assertEqual(
            schemas["ClientRecord"]["properties"]["connectionInfo"]["$ref"],
            "#/components/schemas/ConnectionInfo",
        )

        plan_example = (
            spec["paths"]["/api/sync/plan"]["post"]["requestBody"]["content"][
                "application/json"
            ]["example"]
        )
        self.assertEqual(plan_example["clientInfo"]["browser"], "Chrome")
        file_example = (
            spec["paths"]["/api/sync/file"]["post"]["requestBody"]["content"][
                "application/json"
            ]["example"]
        )
        self.assertEqual(file_example["clientInfo"]["browserVersion"], "125.0.0.0")
        sync_example = (
            spec["paths"]["/api/sync"]["post"]["requestBody"]["content"][
                "application/json"
            ]["example"]
        )
        self.assertEqual(sync_example["clientInfo"]["ipAddress"], "192.168.0.12")

        docs_response = self.client.get("/api/docs")
        self.assertEqual(docs_response.status_code, 200)
        self.assertIn("text/html", docs_response.content_type)
        self.assertIn(b"SwaggerUIBundle", docs_response.data)
        self.assertIn(b"/api/openapi.json", docs_response.data)

    def test_admin_ui_is_served(self):
        index_response = self.client.get("/")
        self.assertEqual(index_response.status_code, 302)
        self.assertEqual(index_response.headers["Location"], "/admin")

        admin_response = self.client.get("/admin")
        self.assertEqual(admin_response.status_code, 200)
        self.assertIn("text/html", admin_response.content_type)
        self.assertIn(b"Notedown Sync Admin", admin_response.data)
        self.assertIn(b'id="tokens-table"', admin_response.data)
        self.assertIn(b'id="history-table"', admin_response.data)
        self.assertIn(b'id="rollback-button"', admin_response.data)

        anonymous = self.app.test_client()
        protected_response = anonymous.get("/admin")
        self.assertEqual(protected_response.status_code, 302)
        self.assertEqual(protected_response.headers["Location"], "/login")

    def test_admin_session_wins_over_stale_bearer_token(self):
        response = self.client.get(
            "/api/manifest",
            headers={"Authorization": "Bearer stale-token"},
        )
        self.assertEqual(response.status_code, 200)

    def test_initial_setup_flow_when_no_credentials_exist(self):
        with TemporaryDirectory() as tempdir:
            app = create_app(
                {
                    "TESTING": True,
                    "NOTE_SYNC_STORAGE": tempdir,
                    "NOTE_SYNC_USERNAME": None,
                    "NOTE_SYNC_PASSWORD": None,
                    "NOTE_SYNC_PASSWORD_HASH": None,
                    "NOTE_SYNC_SECRET": "setup-secret",
                }
            )
            client = app.test_client()

            self.assertEqual(client.get("/").headers["Location"], "/setup")
            self.assertEqual(client.get("/admin").headers["Location"], "/setup")
            self.assertEqual(client.get("/login").headers["Location"], "/setup")

            login_response = client.post(
                "/api/login",
                json={"username": "owner", "password": "password123"},
            )
            self.assertEqual(login_response.status_code, 409)

            setup_response = client.post(
                "/api/setup",
                json={"username": "owner", "password": "password123"},
            )
            self.assertEqual(setup_response.status_code, 200)
            setup_payload = setup_response.get_json()
            self.assertTrue(setup_payload["configured"])
            self.assertNotIn("expiresIn", setup_payload)
            self.assertIn("tokenId", setup_payload)

            admin_response = client.get("/admin")
            self.assertEqual(admin_response.status_code, 200)
            self.assertIn(b"Notedown Sync Admin", admin_response.data)

            duplicate_setup = client.post(
                "/api/setup",
                json={"username": "other", "password": "password123"},
            )
            self.assertEqual(duplicate_setup.status_code, 409)


if __name__ == "__main__":
    unittest.main()
