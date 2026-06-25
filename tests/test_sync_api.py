import base64
import hashlib
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

    def attachment_metadata(self, content):
        digest = hashlib.sha256(content).hexdigest()
        return {
            "id": "att-1",
            "fileName": "diagram.png",
            "relativePath": "memo/.attachments/note-1/diagram.png",
            "mimeType": "image/png",
            "size": len(content),
            "contentHash": digest,
            "updatedAtMs": 11,
        }

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
        manifest_file = upload["manifest"]["files"][0]
        self.assertEqual(manifest_file["note"]["title"], "새 노트")
        self.assertEqual(manifest_file["note"]["folder"], "memo")
        self.assertNotIn("id", manifest_file["note"])

    def test_attachment_upload_download_and_plan_checksum(self):
        content = b"\x89PNG\r\nnotedown-image"
        digest = hashlib.sha256(content).hexdigest()
        metadata = self.note_metadata()
        attachment = self.attachment_metadata(content)
        metadata["notes"][0]["attachments"] = [attachment]

        upload_response = self.client.post(
            "/api/sync/attachment",
            headers=self.headers,
            json={
                "clientId": "client-a",
                "baseRevision": 0,
                "noteRelativePath": "memo/note.md",
                "relativePath": attachment["relativePath"],
                "lastKnownRevision": 0,
                "updatedAtMs": attachment["updatedAtMs"],
                "contentEncoding": "base64",
                "content": base64.b64encode(content).decode("ascii"),
                "contentHash": digest,
                "workspace": {"id": "memo", "name": "memo"},
                "note": metadata["notes"][0],
                "attachment": attachment,
            },
        )

        self.assertEqual(upload_response.status_code, 200)
        upload = upload_response.get_json()
        self.assertEqual(upload["status"], "ok")
        self.assertEqual(upload["attachment"]["status"], "accepted")
        self.assertEqual(upload["metadata"]["status"], "accepted")
        self.assertEqual(upload["attachment"]["kind"], "attachment")
        self.assertEqual(upload["manifest"]["attachments"][0]["contentHash"], digest)
        self.assertEqual(
            upload["manifest"]["attachments"][0]["note"]["title"],
            "새 노트",
        )

        download_response = self.client.get(
            f"/api/attachments/{attachment['relativePath']}",
            headers=self.headers,
        )
        self.assertEqual(download_response.status_code, 200)
        download = download_response.get_json()
        self.assertEqual(download["mimeType"], "image/png")
        self.assertEqual(download["contentHash"], digest)
        self.assertEqual(base64.b64decode(download["content"]), content)

        plan_response = self.client.post(
            "/api/sync/plan",
            headers=self.headers,
            json={
                "clientId": "client-a",
                "baseRevision": upload["serverRevision"],
                "metadata": {
                    "lastKnownRevision": upload["manifest"]["metadata"]["revision"],
                    "body": metadata,
                },
                "knownAttachments": [
                    {
                        "relativePath": attachment["relativePath"],
                        "lastKnownRevision": upload["attachment"]["revision"],
                        "contentHash": digest,
                    }
                ],
            },
        )

        self.assertEqual(plan_response.status_code, 200)
        plan = plan_response.get_json()["plan"]
        self.assertEqual(plan["uploadAttachments"], [])
        self.assertEqual(plan["downloadAttachments"], [])

        changed_metadata = self.note_metadata()
        changed_attachment = dict(attachment)
        changed_attachment["contentHash"] = hashlib.sha256(b"changed").hexdigest()
        changed_attachment["size"] = len(b"changed")
        changed_metadata["notes"][0]["attachments"] = [changed_attachment]
        changed_plan = self.client.post(
            "/api/sync/plan",
            headers=self.headers,
            json={
                "clientId": "client-a",
                "baseRevision": upload["serverRevision"],
                "metadata": {
                    "lastKnownRevision": upload["manifest"]["metadata"]["revision"],
                    "body": changed_metadata,
                },
                "knownAttachments": [
                    {
                        "relativePath": attachment["relativePath"],
                        "lastKnownRevision": upload["attachment"]["revision"],
                        "contentHash": digest,
                    }
                ],
            },
        ).get_json()["plan"]

        self.assertEqual(
            changed_plan["uploadAttachments"][0]["relativePath"],
            attachment["relativePath"],
        )
        self.assertTrue(changed_plan["uploadAttachments"][0]["contentRequired"])

    def test_new_client_empty_metadata_downloads_instead_of_deleting_server_files(self):
        note_content = "# synced from mac\n"
        attachment_content = b"\x89PNG\r\nsynced-attachment"
        attachment_digest = hashlib.sha256(attachment_content).hexdigest()
        metadata = self.note_metadata()
        attachment = self.attachment_metadata(attachment_content)
        metadata["notes"][0]["attachments"] = [attachment]

        file_upload = self.client.post(
            "/api/sync/file",
            headers=self.headers,
            json={
                "clientId": "mac-client",
                "baseRevision": 0,
                "relativePath": "memo/note.md",
                "lastKnownRevision": 0,
                "updatedAtMs": 10,
                "contentEncoding": "utf-8",
                "content": note_content,
                "workspace": {"id": "memo", "name": "memo"},
                "note": metadata["notes"][0],
            },
        ).get_json()

        attachment_upload = self.client.post(
            "/api/sync/attachment",
            headers=self.headers,
            json={
                "clientId": "mac-client",
                "baseRevision": file_upload["serverRevision"],
                "noteRelativePath": "memo/note.md",
                "relativePath": attachment["relativePath"],
                "lastKnownRevision": 0,
                "updatedAtMs": attachment["updatedAtMs"],
                "contentEncoding": "base64",
                "content": base64.b64encode(attachment_content).decode("ascii"),
                "contentHash": attachment_digest,
                "note": metadata["notes"][0],
                "attachment": attachment,
            },
        ).get_json()

        manifest = attachment_upload["manifest"]
        manifest_file = manifest["files"][0]
        manifest_attachment = manifest["attachments"][0]

        plan_response = self.client.post(
            "/api/sync/plan",
            headers=self.headers,
            json={
                "clientId": "android-new-client",
                "baseRevision": manifest["serverRevision"],
                "metadata": {
                    "lastKnownRevision": manifest["metadata"]["revision"],
                    "body": {"version": 1, "workspaces": [], "notes": []},
                },
                "knownFiles": [
                    {
                        "relativePath": manifest_file["relativePath"],
                        "lastKnownRevision": manifest_file["revision"],
                        "contentHash": manifest_file["contentHash"],
                    }
                ],
                "knownAttachments": [
                    {
                        "relativePath": manifest_attachment["relativePath"],
                        "lastKnownRevision": manifest_attachment["revision"],
                        "contentHash": manifest_attachment["contentHash"],
                    }
                ],
            },
        )

        self.assertEqual(plan_response.status_code, 200)
        plan = plan_response.get_json()["plan"]
        self.assertEqual(plan["deleteServerFiles"], [])
        self.assertEqual(plan["deleteServerAttachments"], [])
        self.assertEqual(plan["downloadFiles"][0]["relativePath"], "memo/note.md")
        self.assertEqual(
            plan["downloadAttachments"][0]["relativePath"],
            attachment["relativePath"],
        )

    def test_plan_server_delete_requires_explicit_known_tombstone(self):
        metadata = self.note_metadata()
        upload = self.client.post(
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
        ).get_json()

        plan = self.client.post(
            "/api/sync/plan",
            headers=self.headers,
            json={
                "clientId": "client-a",
                "baseRevision": upload["serverRevision"],
                "metadata": {
                    "lastKnownRevision": upload["manifest"]["metadata"]["revision"],
                    "body": {"version": 1, "workspaces": [], "notes": []},
                },
                "knownFiles": [
                    {
                        "relativePath": "memo/note.md",
                        "lastKnownRevision": upload["file"]["revision"],
                        "contentHash": upload["file"]["contentHash"],
                        "deleted": True,
                    }
                ],
            },
        ).get_json()["plan"]

        self.assertEqual(plan["downloadFiles"], [])
        self.assertEqual(plan["deleteServerFiles"][0]["relativePath"], "memo/note.md")
        self.assertEqual(plan["deleteServerFiles"][0]["reason"], "client_deleted_file")

    def test_metadata_sync_rejects_wiping_notes_for_existing_server_files(self):
        metadata = self.note_metadata()
        upload = self.client.post(
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
        ).get_json()

        wipe = self.client.post(
            "/api/sync",
            headers=self.headers,
            json={
                "clientId": "android-new-client",
                "baseRevision": upload["serverRevision"],
                "metadata": {
                    "lastKnownRevision": upload["manifest"]["metadata"]["revision"],
                    "body": {"version": 1, "workspaces": [], "notes": []},
                },
                "files": [],
            },
        )

        self.assertEqual(wipe.status_code, 200)
        payload = wipe.get_json()
        self.assertEqual(payload["status"], "conflict")
        self.assertEqual(payload["metadata"]["status"], "conflict")
        self.assertEqual(
            payload["metadata"]["reason"],
            "metadata_removes_existing_server_files",
        )
        self.assertEqual(payload["metadata"]["orphanedFiles"], ["memo/note.md"])

        manifest = self.client.get("/api/manifest", headers=self.headers).get_json()
        self.assertEqual(manifest["files"][0]["note"]["title"], "새 노트")

    def test_delete_upload_requires_explicit_last_known_revision(self):
        response = self.client.post(
            "/api/sync/file",
            headers=self.headers,
            json={
                "clientId": "client-a",
                "baseRevision": 10,
                "relativePath": "memo/note.md",
                "deleted": True,
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("lastKnownRevision", response.get_json()["message"])

        string_deleted = self.client.post(
            "/api/sync/file",
            headers=self.headers,
            json={
                "clientId": "client-a",
                "relativePath": "memo/note.md",
                "lastKnownRevision": 1,
                "deleted": "true",
            },
        )

        self.assertEqual(string_deleted.status_code, 400)
        self.assertIn("must be a boolean", string_deleted.get_json()["message"])

    def test_attachment_metadata_only_sync_skips_existing_content_upload(self):
        content = b"report-binary"
        digest = hashlib.sha256(content).hexdigest()
        metadata = self.note_metadata()
        attachment = self.attachment_metadata(content)
        metadata["notes"][0]["attachments"] = [attachment]

        batch = self.client.post(
            "/api/sync",
            headers=self.headers,
            json={
                "clientId": "client-a",
                "baseRevision": 0,
                "attachments": [
                    {
                        "noteRelativePath": "memo/note.md",
                        "relativePath": attachment["relativePath"],
                        "lastKnownRevision": 0,
                        "contentEncoding": "base64",
                        "content": base64.b64encode(content).decode("ascii"),
                        "contentHash": digest,
                    }
                ],
            },
        ).get_json()

        plan = self.client.post(
            "/api/sync/plan",
            headers=self.headers,
            json={
                "clientId": "client-a",
                "baseRevision": batch["serverRevision"],
                "metadata": {
                    "lastKnownRevision": 0,
                    "body": metadata,
                },
                "knownAttachments": [
                    {
                        "relativePath": attachment["relativePath"],
                        "lastKnownRevision": batch["acceptedAttachments"][0]["revision"],
                        "contentHash": digest,
                    }
                ],
            },
        ).get_json()["plan"]

        self.assertEqual(
            plan["uploadAttachments"][0]["reason"],
            "missing_server_attachment_metadata",
        )
        self.assertFalse(plan["uploadAttachments"][0]["contentRequired"])

        metadata_only = self.client.post(
            "/api/sync/attachment",
            headers=self.headers,
            json={
                "clientId": "client-a",
                "baseRevision": batch["serverRevision"],
                "noteRelativePath": "memo/note.md",
                "relativePath": attachment["relativePath"],
                "lastKnownRevision": batch["acceptedAttachments"][0]["revision"],
                "contentHash": digest,
                "note": metadata["notes"][0],
                "attachment": attachment,
            },
        ).get_json()

        self.assertEqual(metadata_only["attachment"]["status"], "unchanged")
        self.assertEqual(metadata_only["metadata"]["status"], "accepted")
        self.assertEqual(
            metadata_only["manifest"]["attachments"][0]["contentHash"],
            digest,
        )

    def test_dummy_note_with_file_and_image_attachments_round_trip(self):
        note_content = "# 더미 첨부 노트\n\n이미지와 파일을 함께 첨부합니다.\n"
        image_content = b"\x89PNG\r\n\x1a\nnotedown-dummy-image"
        file_content = b"dummy attachment file\nline 2\n"
        metadata = self.note_metadata(updated_at_ms=100)
        metadata["notes"][0]["title"] = "더미 첨부 노트"

        image_attachment = self.attachment_metadata(image_content)
        file_digest = hashlib.sha256(file_content).hexdigest()
        file_attachment = {
            "id": "att-2",
            "fileName": "dummy.txt",
            "relativePath": "memo/.attachments/note-1/dummy.txt",
            "mimeType": "text/plain",
            "size": len(file_content),
            "contentHash": file_digest,
            "updatedAtMs": 12,
        }
        metadata["notes"][0]["attachments"] = [
            image_attachment,
            file_attachment,
        ]

        initial_plan = self.client.post(
            "/api/sync/plan",
            headers=self.headers,
            json={
                "clientId": "client-a",
                "baseRevision": 0,
                "metadata": {"lastKnownRevision": 0, "body": metadata},
            },
        ).get_json()["plan"]

        self.assertEqual(
            initial_plan["uploadFiles"][0]["relativePath"],
            "memo/note.md",
        )
        self.assertEqual(len(initial_plan["uploadAttachments"]), 2)
        self.assertTrue(
            all(item["contentRequired"] for item in initial_plan["uploadAttachments"])
        )

        note_upload = self.client.post(
            "/api/sync/file",
            headers=self.headers,
            json={
                "clientId": "client-a",
                "baseRevision": 0,
                "relativePath": "memo/note.md",
                "lastKnownRevision": 0,
                "updatedAtMs": 100,
                "contentEncoding": "utf-8",
                "content": note_content,
                "workspace": {"id": "memo", "name": "memo"},
                "note": metadata["notes"][0],
            },
        ).get_json()

        image_upload = self.client.post(
            "/api/sync/attachment",
            headers=self.headers,
            json={
                "clientId": "client-a",
                "baseRevision": note_upload["serverRevision"],
                "noteRelativePath": "memo/note.md",
                "relativePath": image_attachment["relativePath"],
                "lastKnownRevision": 0,
                "updatedAtMs": image_attachment["updatedAtMs"],
                "contentEncoding": "base64",
                "content": base64.b64encode(image_content).decode("ascii"),
                "contentHash": image_attachment["contentHash"],
                "note": metadata["notes"][0],
                "attachment": image_attachment,
            },
        ).get_json()

        file_upload = self.client.post(
            "/api/sync/attachment",
            headers=self.headers,
            json={
                "clientId": "client-a",
                "baseRevision": image_upload["serverRevision"],
                "noteRelativePath": "memo/note.md",
                "relativePath": file_attachment["relativePath"],
                "lastKnownRevision": 0,
                "updatedAtMs": file_attachment["updatedAtMs"],
                "contentEncoding": "base64",
                "content": base64.b64encode(file_content).decode("ascii"),
                "contentHash": file_attachment["contentHash"],
                "note": metadata["notes"][0],
                "attachment": file_attachment,
            },
        ).get_json()

        self.assertEqual(note_upload["file"]["status"], "accepted")
        self.assertEqual(image_upload["attachment"]["status"], "accepted")
        self.assertEqual(file_upload["attachment"]["status"], "accepted")
        self.assertEqual(len(file_upload["manifest"]["attachments"]), 2)

        note_download = self.client.get(
            "/api/files/memo/note.md",
            headers=self.headers,
        ).get_json()
        image_download = self.client.get(
            f"/api/attachments/{image_attachment['relativePath']}",
            headers=self.headers,
        ).get_json()
        file_download = self.client.get(
            f"/api/attachments/{file_attachment['relativePath']}",
            headers=self.headers,
        ).get_json()

        self.assertEqual(self.decoded_content(note_download), note_content)
        self.assertEqual(base64.b64decode(image_download["content"]), image_content)
        self.assertEqual(base64.b64decode(file_download["content"]), file_content)
        self.assertEqual(image_download["mimeType"], "image/png")
        self.assertEqual(file_download["mimeType"], "text/plain")

        settled_plan = self.client.post(
            "/api/sync/plan",
            headers=self.headers,
            json={
                "clientId": "client-a",
                "baseRevision": file_upload["serverRevision"],
                "metadata": {
                    "lastKnownRevision": file_upload["manifest"]["metadata"]["revision"],
                    "body": metadata,
                },
                "knownFiles": [
                    {
                        "relativePath": "memo/note.md",
                        "lastKnownRevision": note_upload["file"]["revision"],
                        "contentHash": note_upload["file"]["contentHash"],
                    }
                ],
                "knownAttachments": [
                    {
                        "relativePath": image_attachment["relativePath"],
                        "lastKnownRevision": image_upload["attachment"]["revision"],
                        "contentHash": image_attachment["contentHash"],
                    },
                    {
                        "relativePath": file_attachment["relativePath"],
                        "lastKnownRevision": file_upload["attachment"]["revision"],
                        "contentHash": file_attachment["contentHash"],
                    },
                ],
            },
        ).get_json()["plan"]

        self.assertEqual(settled_plan["uploadFiles"], [])
        self.assertEqual(settled_plan["downloadFiles"], [])
        self.assertEqual(settled_plan["uploadAttachments"], [])
        self.assertEqual(settled_plan["downloadAttachments"], [])

    def test_save_time_cross_device_file_and_attachment_sync(self):
        note_content_a = "# A 기기 저장\n\n초기 내용\n"
        image_content = b"\x89PNG\r\ncross-device-image"
        image_digest = hashlib.sha256(image_content).hexdigest()
        metadata_a = self.note_metadata(updated_at_ms=100)
        image_attachment = self.attachment_metadata(image_content)
        metadata_a["notes"][0]["title"] = "A 기기 저장"
        metadata_a["notes"][0]["attachments"] = [image_attachment]

        a_file_upload = self.client.post(
            "/api/sync/file",
            headers=self.headers,
            json={
                "clientId": "device-a",
                "baseRevision": 0,
                "relativePath": "memo/note.md",
                "lastKnownRevision": 0,
                "updatedAtMs": 100,
                "contentEncoding": "utf-8",
                "content": note_content_a,
                "workspace": {"id": "memo", "name": "memo"},
                "note": metadata_a["notes"][0],
            },
        ).get_json()

        a_attachment_upload = self.client.post(
            "/api/sync/attachment",
            headers=self.headers,
            json={
                "clientId": "device-a",
                "baseRevision": a_file_upload["serverRevision"],
                "noteRelativePath": "memo/note.md",
                "relativePath": image_attachment["relativePath"],
                "lastKnownRevision": 0,
                "updatedAtMs": image_attachment["updatedAtMs"],
                "contentEncoding": "base64",
                "content": base64.b64encode(image_content).decode("ascii"),
                "contentHash": image_digest,
                "note": metadata_a["notes"][0],
                "attachment": image_attachment,
            },
        ).get_json()

        b_initial_plan = self.client.post(
            "/api/sync/plan",
            headers=self.headers,
            json={
                "clientId": "device-b",
                "baseRevision": 0,
                "metadata": {
                    "lastKnownRevision": 0,
                    "body": {"version": 1, "workspaces": [], "notes": []},
                },
                "knownFiles": [],
                "knownAttachments": [],
            },
        ).get_json()["plan"]

        self.assertEqual(
            b_initial_plan["downloadFiles"][0]["relativePath"],
            "memo/note.md",
        )
        self.assertEqual(
            b_initial_plan["downloadAttachments"][0]["relativePath"],
            image_attachment["relativePath"],
        )
        self.assertEqual(b_initial_plan["deleteServerFiles"], [])
        self.assertEqual(b_initial_plan["deleteServerAttachments"], [])

        b_file_download = self.client.get(
            "/api/files/memo/note.md",
            headers=self.headers,
        ).get_json()
        b_attachment_download = self.client.get(
            f"/api/attachments/{image_attachment['relativePath']}",
            headers=self.headers,
        ).get_json()
        self.assertEqual(self.decoded_content(b_file_download), note_content_a)
        self.assertEqual(base64.b64decode(b_attachment_download["content"]), image_content)

        note_content_b = "# B 기기 저장\n\n수정 내용\n"
        extra_content = b"extra attachment from device b\n"
        extra_digest = hashlib.sha256(extra_content).hexdigest()
        extra_attachment = {
            "id": "att-2",
            "fileName": "device-b.txt",
            "relativePath": "memo/.attachments/note-1/device-b.txt",
            "mimeType": "text/plain",
            "size": len(extra_content),
            "contentHash": extra_digest,
            "updatedAtMs": 210,
        }
        metadata_b = self.note_metadata(updated_at_ms=200)
        metadata_b["notes"][0]["title"] = "B 기기 저장"
        metadata_b["notes"][0]["attachments"] = [
            image_attachment,
            extra_attachment,
        ]

        b_file_upload = self.client.post(
            "/api/sync/file",
            headers=self.headers,
            json={
                "clientId": "device-b",
                "baseRevision": a_attachment_upload["serverRevision"],
                "relativePath": "memo/note.md",
                "lastKnownRevision": a_file_upload["file"]["revision"],
                "updatedAtMs": 200,
                "contentEncoding": "utf-8",
                "content": note_content_b,
                "workspace": {"id": "memo", "name": "memo"},
                "note": metadata_b["notes"][0],
            },
        ).get_json()

        b_attachment_upload = self.client.post(
            "/api/sync/attachment",
            headers=self.headers,
            json={
                "clientId": "device-b",
                "baseRevision": b_file_upload["serverRevision"],
                "noteRelativePath": "memo/note.md",
                "relativePath": extra_attachment["relativePath"],
                "lastKnownRevision": 0,
                "updatedAtMs": extra_attachment["updatedAtMs"],
                "contentEncoding": "base64",
                "content": base64.b64encode(extra_content).decode("ascii"),
                "contentHash": extra_digest,
                "note": metadata_b["notes"][0],
                "attachment": extra_attachment,
            },
        ).get_json()

        self.assertEqual(b_file_upload["file"]["status"], "accepted")
        self.assertEqual(b_attachment_upload["attachment"]["status"], "accepted")

        a_followup_plan = self.client.post(
            "/api/sync/plan",
            headers=self.headers,
            json={
                "clientId": "device-a",
                "baseRevision": a_attachment_upload["serverRevision"],
                "metadata": {
                    "lastKnownRevision": (
                        a_attachment_upload["manifest"]["metadata"]["revision"]
                    ),
                    "body": metadata_a,
                },
                "knownFiles": [
                    {
                        "relativePath": "memo/note.md",
                        "lastKnownRevision": a_file_upload["file"]["revision"],
                        "contentHash": a_file_upload["file"]["contentHash"],
                    }
                ],
                "knownAttachments": [
                    {
                        "relativePath": image_attachment["relativePath"],
                        "lastKnownRevision": (
                            a_attachment_upload["attachment"]["revision"]
                        ),
                        "contentHash": image_digest,
                    }
                ],
            },
        ).get_json()["plan"]

        self.assertEqual(a_followup_plan["deleteServerFiles"], [])
        self.assertEqual(a_followup_plan["deleteServerAttachments"], [])
        self.assertEqual(
            a_followup_plan["downloadFiles"][0]["relativePath"],
            "memo/note.md",
        )
        self.assertEqual(
            a_followup_plan["downloadAttachments"][0]["relativePath"],
            extra_attachment["relativePath"],
        )

        a_file_after_b_save = self.client.get(
            "/api/files/memo/note.md",
            headers=self.headers,
        ).get_json()
        a_extra_attachment = self.client.get(
            f"/api/attachments/{extra_attachment['relativePath']}",
            headers=self.headers,
        ).get_json()
        self.assertEqual(self.decoded_content(a_file_after_b_save), note_content_b)
        self.assertEqual(base64.b64decode(a_extra_attachment["content"]), extra_content)

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

    def test_rollback_deleted_file_restores_note_metadata_title(self):
        metadata = self.note_metadata(updated_at_ms=10)
        metadata["notes"][0]["title"] = "복구할 노트"
        upload = self.client.post(
            "/api/sync/file",
            headers=self.headers,
            json={
                "clientId": "client-a",
                "baseRevision": 0,
                "relativePath": "memo/note.md",
                "lastKnownRevision": 0,
                "updatedAtMs": 10,
                "contentEncoding": "utf-8",
                "content": "본문만 있는 노트\n",
                "workspace": {"id": "memo", "name": "memo"},
                "note": metadata["notes"][0],
            },
        ).get_json()

        deleted = self.client.post(
            "/api/sync/file",
            headers=self.headers,
            json={
                "clientId": "client-a",
                "baseRevision": upload["serverRevision"],
                "relativePath": "memo/note.md",
                "lastKnownRevision": upload["file"]["revision"],
                "deleted": True,
                "updatedAtMs": 20,
            },
        ).get_json()

        self.assertEqual(deleted["metadata"]["status"], "accepted")
        deleted_file = deleted["manifest"]["files"][0]
        self.assertTrue(deleted_file["deleted"])
        self.assertEqual(deleted_file["note"]["title"], "복구할 노트")

        commits = self.client.get(
            "/api/admin/files/memo/note.md/history",
            headers=self.headers,
        ).get_json()["commits"]
        original_commit = commits[-1]["commit"]

        rollback = self.client.post(
            "/api/admin/files/memo/note.md/rollback",
            headers=self.headers,
            json={"commit": original_commit},
        ).get_json()

        self.assertEqual(rollback["status"], "accepted")
        self.assertEqual(rollback["metadata"]["status"], "accepted")
        restored_file = rollback["manifest"]["files"][0]
        self.assertFalse(restored_file["deleted"])
        self.assertEqual(restored_file["note"]["title"], "복구할 노트")
        self.assertEqual(restored_file["note"]["workspaceName"], "memo")

    def test_rollback_note_restores_deleted_attachment_content_and_metadata(self):
        image_content = b"\x89PNG\r\nrollback-image"
        image_digest = hashlib.sha256(image_content).hexdigest()
        metadata = self.note_metadata(updated_at_ms=100)
        attachment = self.attachment_metadata(image_content)
        metadata["notes"][0]["attachments"] = [attachment]

        note_upload = self.client.post(
            "/api/sync/file",
            headers=self.headers,
            json={
                "clientId": "client-a",
                "baseRevision": 0,
                "relativePath": "memo/note.md",
                "lastKnownRevision": 0,
                "updatedAtMs": 100,
                "contentEncoding": "utf-8",
                "content": "# 첨부가 있는 노트\n",
                "workspace": {"id": "memo", "name": "memo"},
                "note": metadata["notes"][0],
            },
        ).get_json()

        attachment_upload = self.client.post(
            "/api/sync/attachment",
            headers=self.headers,
            json={
                "clientId": "client-a",
                "baseRevision": note_upload["serverRevision"],
                "noteRelativePath": "memo/note.md",
                "relativePath": attachment["relativePath"],
                "lastKnownRevision": 0,
                "updatedAtMs": attachment["updatedAtMs"],
                "contentEncoding": "base64",
                "content": base64.b64encode(image_content).decode("ascii"),
                "contentHash": image_digest,
                "note": metadata["notes"][0],
                "attachment": attachment,
            },
        ).get_json()

        deleted_attachment = self.client.post(
            "/api/sync/attachment",
            headers=self.headers,
            json={
                "clientId": "client-a",
                "baseRevision": attachment_upload["serverRevision"],
                "noteRelativePath": "memo/note.md",
                "relativePath": attachment["relativePath"],
                "lastKnownRevision": attachment_upload["attachment"]["revision"],
                "deleted": True,
                "updatedAtMs": 120,
            },
        ).get_json()
        self.assertEqual(deleted_attachment["attachment"]["status"], "accepted")

        metadata_without_attachment = self.note_metadata(updated_at_ms=130)
        metadata_without_attachment["notes"][0]["attachments"] = []
        second_note = self.client.post(
            "/api/sync/file",
            headers=self.headers,
            json={
                "clientId": "client-a",
                "baseRevision": deleted_attachment["serverRevision"],
                "relativePath": "memo/note.md",
                "lastKnownRevision": note_upload["file"]["revision"],
                "updatedAtMs": 130,
                "contentEncoding": "utf-8",
                "content": "# 첨부가 제거된 노트\n",
                "workspace": {"id": "memo", "name": "memo"},
                "note": metadata_without_attachment["notes"][0],
            },
        ).get_json()

        commits = self.client.get(
            "/api/admin/files/memo/note.md/history",
            headers=self.headers,
        ).get_json()["commits"]
        original_note_commit = commits[-1]["commit"]

        rollback = self.client.post(
            "/api/admin/files/memo/note.md/rollback",
            headers=self.headers,
            json={"commit": original_note_commit},
        ).get_json()

        self.assertEqual(rollback["status"], "accepted")
        self.assertEqual(rollback["metadata"]["status"], "accepted")
        self.assertEqual(
            rollback["restoredAttachments"][0]["relativePath"],
            attachment["relativePath"],
        )

        restored_attachment = self.client.get(
            f"/api/attachments/{attachment['relativePath']}",
            headers=self.headers,
        )
        self.assertEqual(restored_attachment.status_code, 200)
        restored_payload = restored_attachment.get_json()
        self.assertEqual(base64.b64decode(restored_payload["content"]), image_content)
        self.assertEqual(restored_payload["contentHash"], image_digest)
        self.assertEqual(restored_payload["kind"], "attachment")

        plan = self.client.post(
            "/api/sync/plan",
            headers=self.headers,
            json={
                "clientId": "client-a",
                "baseRevision": second_note["serverRevision"],
                "metadata": {
                    "lastKnownRevision": second_note["manifest"]["metadata"]["revision"],
                    "body": metadata_without_attachment,
                },
                "knownFiles": [
                    {
                        "relativePath": "memo/note.md",
                        "lastKnownRevision": second_note["file"]["revision"],
                        "contentHash": second_note["file"]["contentHash"],
                    }
                ],
            },
        ).get_json()

        server_note = plan["metadata"]["serverMetadata"]["notes"][0]
        self.assertEqual(
            server_note["attachments"][0]["relativePath"],
            attachment["relativePath"],
        )

    def test_rollback_attachment_preserves_attachment_kind_and_metadata(self):
        first_content = b"\x89PNG\r\nfirst-image"
        second_content = b"\x89PNG\r\nsecond-image"
        first_digest = hashlib.sha256(first_content).hexdigest()
        second_digest = hashlib.sha256(second_content).hexdigest()
        metadata = self.note_metadata(updated_at_ms=100)
        first_attachment = self.attachment_metadata(first_content)
        metadata["notes"][0]["attachments"] = [first_attachment]

        note_upload = self.client.post(
            "/api/sync/file",
            headers=self.headers,
            json={
                "clientId": "client-a",
                "baseRevision": 0,
                "relativePath": "memo/note.md",
                "lastKnownRevision": 0,
                "updatedAtMs": 100,
                "contentEncoding": "utf-8",
                "content": "# 이미지 노트\n",
                "workspace": {"id": "memo", "name": "memo"},
                "note": metadata["notes"][0],
            },
        ).get_json()

        first_upload = self.client.post(
            "/api/sync/attachment",
            headers=self.headers,
            json={
                "clientId": "client-a",
                "baseRevision": note_upload["serverRevision"],
                "noteRelativePath": "memo/note.md",
                "relativePath": first_attachment["relativePath"],
                "lastKnownRevision": 0,
                "updatedAtMs": first_attachment["updatedAtMs"],
                "contentEncoding": "base64",
                "content": base64.b64encode(first_content).decode("ascii"),
                "contentHash": first_digest,
                "note": metadata["notes"][0],
                "attachment": first_attachment,
            },
        ).get_json()

        second_attachment = dict(first_attachment)
        second_attachment["contentHash"] = second_digest
        second_attachment["size"] = len(second_content)
        second_attachment["updatedAtMs"] = 200
        metadata["notes"][0]["attachments"] = [second_attachment]
        second_upload = self.client.post(
            "/api/sync/attachment",
            headers=self.headers,
            json={
                "clientId": "client-a",
                "baseRevision": first_upload["serverRevision"],
                "noteRelativePath": "memo/note.md",
                "relativePath": first_attachment["relativePath"],
                "lastKnownRevision": first_upload["attachment"]["revision"],
                "updatedAtMs": second_attachment["updatedAtMs"],
                "contentEncoding": "base64",
                "content": base64.b64encode(second_content).decode("ascii"),
                "contentHash": second_digest,
                "note": metadata["notes"][0],
                "attachment": second_attachment,
            },
        ).get_json()
        self.assertEqual(second_upload["attachment"]["contentHash"], second_digest)

        commits = self.client.get(
            f"/api/admin/files/{first_attachment['relativePath']}/history",
            headers=self.headers,
        ).get_json()["commits"]
        original_attachment_commit = commits[-1]["commit"]

        rollback = self.client.post(
            f"/api/admin/files/{first_attachment['relativePath']}/rollback",
            headers=self.headers,
            json={"commit": original_attachment_commit},
        ).get_json()

        self.assertEqual(rollback["status"], "accepted")
        self.assertEqual(rollback["metadata"]["status"], "accepted")
        self.assertEqual(rollback["manifest"]["attachments"][0]["kind"], "attachment")
        self.assertEqual(
            rollback["manifest"]["attachments"][0]["contentHash"],
            first_digest,
        )

        restored = self.client.get(
            f"/api/attachments/{first_attachment['relativePath']}",
            headers=self.headers,
        ).get_json()
        self.assertEqual(base64.b64decode(restored["content"]), first_content)
        self.assertEqual(restored["contentHash"], first_digest)

    def test_global_revision_delete_tombstone_does_not_delete_server_file(self):
        metadata = self.note_metadata()
        upload = self.client.post(
            "/api/sync/file",
            headers=self.headers,
            json={
                "clientId": "client-a",
                "baseRevision": 0,
                "relativePath": "memo/note.md",
                "lastKnownRevision": 0,
                "updatedAtMs": 10,
                "contentEncoding": "utf-8",
                "content": "# synced from client a\n",
                "workspace": {"id": "memo", "name": "memo"},
                "note": metadata["notes"][0],
            },
        ).get_json()

        global_revision_delete = self.client.post(
            "/api/sync",
            headers=self.headers,
            json={
                "clientId": "client-b",
                "baseRevision": upload["serverRevision"],
                "metadata": {
                    "lastKnownRevision": upload["manifest"]["metadata"]["revision"],
                    "body": {"version": 1, "workspaces": [], "notes": []},
                },
                "files": [
                    {
                        "relativePath": "memo/note.md",
                        "lastKnownRevision": upload["serverRevision"],
                        "deleted": True,
                        "updatedAtMs": 20,
                    }
                ],
            },
        ).get_json()

        self.assertEqual(global_revision_delete["status"], "conflict")
        self.assertEqual(global_revision_delete["accepted"], [])
        self.assertEqual(
            global_revision_delete["conflicts"][0]["relativePath"],
            "memo/note.md",
        )

        current = self.client.get(
            "/api/files/memo/note.md",
            headers=self.headers,
        )
        self.assertEqual(current.status_code, 200)
        self.assertEqual(self.decoded_content(current.get_json()), "# synced from client a\n")

        plan = self.client.post(
            "/api/sync/plan",
            headers=self.headers,
            json={
                "clientId": "client-b",
                "baseRevision": upload["serverRevision"],
                "metadata": {
                    "lastKnownRevision": upload["manifest"]["metadata"]["revision"],
                    "body": {"version": 1, "workspaces": [], "notes": []},
                },
                "knownFiles": [
                    {
                        "relativePath": "memo/note.md",
                        "lastKnownRevision": upload["serverRevision"],
                        "contentHash": upload["file"]["contentHash"],
                        "deleted": True,
                    }
                ],
            },
        ).get_json()["plan"]

        self.assertEqual(plan["deleteServerFiles"], [])
        self.assertTrue(
            any(
                item.get("relativePath") == "memo/note.md"
                for item in plan["conflicts"]
            )
        )

    def test_manifest_synthesizes_note_title_for_active_file_without_metadata(self):
        self.client.post(
            "/api/sync/file",
            headers=self.headers,
            json={
                "clientId": "client-a",
                "baseRevision": 0,
                "relativePath": "memo/no-metadata.md",
                "lastKnownRevision": 0,
                "updatedAtMs": 10,
                "contentEncoding": "utf-8",
                "content": "# 제목만 있는 노트\n\n본문\n",
            },
        )

        manifest = self.client.get("/api/manifest", headers=self.headers).get_json()
        file_record = manifest["files"][0]
        self.assertEqual(file_record["note"]["title"], "제목만 있는 노트")
        self.assertEqual(file_record["note"]["folder"], "memo")

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
        self.assertIn("/api/sync/attachment", spec["paths"])
        self.assertIn("/api/attachments/{relative_path}", spec["paths"])
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
        self.assertIn(
            "restoredAttachments",
            schemas["FileRollbackResponse"]["properties"],
        )
        self.assertIn("ManifestFileNote", schemas)
        self.assertIn("NoteAttachmentMetadata", schemas)
        self.assertIn("KnownAttachment", schemas)
        self.assertIn("AttachmentSyncRequest", schemas)
        self.assertIn("AttachmentSyncResponse", schemas)
        self.assertIn("AttachmentPayload", schemas)

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
        self.assertIn("knownAttachments", plan_example)
        self.assertIn("attachments", plan_example["metadata"]["body"]["notes"][0])
        file_example = (
            spec["paths"]["/api/sync/file"]["post"]["requestBody"]["content"][
                "application/json"
            ]["example"]
        )
        self.assertEqual(file_example["clientInfo"]["browserVersion"], "125.0.0.0")
        attachment_example = (
            spec["paths"]["/api/sync/attachment"]["post"]["requestBody"]["content"][
                "application/json"
            ]["example"]
        )
        self.assertEqual(attachment_example["attachment"]["mimeType"], "image/png")
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
        self.assertIn(b'id="summary-file-count"', admin_response.data)
        self.assertIn(b'id="summary-last-sync"', admin_response.data)
        self.assertIn(b'id="folder-filter"', admin_response.data)
        self.assertNotIn(b'class="summary-item"', admin_response.data)
        self.assertIn(b'id="attachment-list"', admin_response.data)
        self.assertIn(b'id="attachment-modal"', admin_response.data)
        self.assertIn(b'id="attachment-modal-body"', admin_response.data)
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
