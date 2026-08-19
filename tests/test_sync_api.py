import base64
import hashlib
import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

import sync_store
from app import create_app
from sync_store import (
    MAX_CLIENT_INFO_LIST_ITEMS,
    MAX_CLIENT_INFO_STRING_LENGTH,
    MAX_TRACKED_CLIENTS,
    SyncStore,
    atomic_write_bytes,
    sha256_json,
)


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

    def unfiled_directory_metadata(self):
        return {
            "version": 1,
            "generatedAt": "2026-06-30T10:07:36.000Z",
            "workspaces": [
                {"id": "unfiled", "name": "미지정 워크스페이스"},
                {"id": "미지정-워크스페이스", "name": "미지정 워크스페이스"},
            ],
            "notes": [
                {
                    "id": "note-unfiled",
                    "title": "Memo",
                    "workspace": "unfiled",
                    "workspaceName": "미지정 워크스페이스",
                    "folder": "unfiled",
                    "fileName": "Memo.md",
                    "relativePath": "미지정 워크스페이스/Memo.md",
                    "updatedAtMs": 10,
                }
            ],
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

    def test_admin_can_reset_storage_for_resync(self):
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
        self.assertGreater(upload["serverRevision"], 0)
        self.assertTrue(
            (Path(self.tempdir.name) / "files" / "memo" / "새 노트.md").exists()
        )

        rejected = self.client.post(
            "/api/admin/storage/reset",
            headers=self.headers,
            json={"confirm": "NO"},
        )
        self.assertEqual(rejected.status_code, 400)

        reset = self.client.post(
            "/api/admin/storage/reset",
            headers=self.headers,
            json={"confirm": "RESET"},
        )

        self.assertEqual(reset.status_code, 200)
        payload = reset.get_json()
        self.assertEqual(payload["status"], "reset")
        manifest = payload["manifest"]
        self.assertEqual(manifest["serverRevision"], 0)
        self.assertEqual(manifest["files"], [])
        self.assertEqual(manifest["attachments"], [])
        self.assertEqual(manifest["metadata"]["revision"], 0)
        self.assertFalse((Path(self.tempdir.name) / "metadata.json").exists())
        self.assertTrue((Path(self.tempdir.name) / "metadata.db").exists())

        still_authenticated = self.client.get("/api/manifest", headers=self.headers)
        self.assertEqual(still_authenticated.status_code, 200)
        self.assertEqual(still_authenticated.get_json()["serverRevision"], 0)

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

    def test_file_upload_uses_readable_storage_path_and_sqlite_metadata(self):
        metadata = self.note_metadata()
        metadata["notes"][0]["id"] = "note-f7fdc36f7aad279d"
        metadata["notes"][0]["title"] = "회의 기록"
        metadata["notes"][0]["fileName"] = "note-1779945632257.md"
        metadata["notes"][0]["relativePath"] = "memo/note-1779945632257.md"

        response = self.client.post(
            "/api/sync/file",
            headers=self.headers,
            json={
                "clientId": "client-a",
                "baseRevision": 0,
                "relativePath": "memo/note-1779945632257.md",
                "lastKnownRevision": 0,
                "updatedAtMs": 10,
                "contentEncoding": "utf-8",
                "content": "# 회의 기록\n",
                "workspace": {"id": "memo", "name": "memo"},
                "note": metadata["notes"][0],
            },
        )

        self.assertEqual(response.status_code, 200)
        upload = response.get_json()
        self.assertEqual(upload["file"]["storagePath"], "memo/회의 기록.md")

        storage_root = Path(self.tempdir.name)
        self.assertTrue((storage_root / "metadata.db").exists())
        self.assertFalse((storage_root / "metadata.json").exists())
        self.assertTrue((storage_root / "files" / "memo" / "회의 기록.md").exists())
        self.assertFalse(
            (storage_root / "files" / "memo" / "note-1779945632257.md").exists()
        )

        download = self.client.get(
            "/api/files/memo/note-1779945632257.md",
            headers=self.headers,
        ).get_json()
        self.assertEqual(self.decoded_content(download), "# 회의 기록\n")
        self.assertEqual(download["storagePath"], "memo/회의 기록.md")

    def test_file_upload_normalizes_workspace_alias_to_directory_folder(self):
        metadata = self.unfiled_directory_metadata()

        response = self.client.post(
            "/api/sync/file",
            headers=self.headers,
            json={
                "clientId": "client-a",
                "baseRevision": 0,
                "relativePath": "미지정 워크스페이스/Memo.md",
                "lastKnownRevision": 0,
                "updatedAtMs": 10,
                "contentEncoding": "utf-8",
                "content": "# Memo\n",
                "workspace": {"id": "unfiled", "name": "미지정 워크스페이스"},
                "note": metadata["notes"][0],
            },
        )

        self.assertEqual(response.status_code, 200)
        upload = response.get_json()
        note = upload["manifest"]["files"][0]["note"]
        self.assertEqual(note["folder"], "미지정 워크스페이스")
        self.assertEqual(note["workspace"], "미지정 워크스페이스")
        self.assertEqual(note["workspaceName"], "미지정 워크스페이스")

        plan = self.client.post(
            "/api/sync/plan",
            headers=self.headers,
            json={
                "clientId": "client-b",
                "baseRevision": 0,
                "metadata": {
                    "lastKnownRevision": 0,
                    "body": {"version": 1, "workspaces": [], "notes": []},
                },
            },
        ).get_json()["plan"]

        download = plan["downloadFiles"][0]
        self.assertEqual(download["note"]["folder"], "미지정 워크스페이스")
        self.assertEqual(download["workspace"]["id"], "미지정 워크스페이스")

    def test_metadata_sync_normalizes_workspace_alias_to_directory_folder(self):
        metadata = self.unfiled_directory_metadata()

        response = self.client.post(
            "/api/sync",
            headers=self.headers,
            json={
                "clientId": "client-a",
                "baseRevision": 0,
                "metadata": {
                    "lastKnownRevision": 0,
                    "contentHash": sha256_json(metadata),
                    "body": metadata,
                },
                "files": [],
            },
        )

        self.assertEqual(response.status_code, 200)
        sync_payload = response.get_json()
        self.assertEqual(sync_payload["metadata"]["status"], "accepted")

        plan_payload = self.client.post(
            "/api/sync/plan",
            headers=self.headers,
            json={
                "clientId": "client-b",
                "baseRevision": 0,
                "metadata": {
                    "lastKnownRevision": 0,
                    "body": {"version": 1, "workspaces": [], "notes": []},
                },
            },
        ).get_json()
        server_metadata = plan_payload["metadata"]["serverMetadata"]

        self.assertEqual(
            server_metadata["workspaces"],
            [{"id": "미지정 워크스페이스", "name": "미지정 워크스페이스"}],
        )
        self.assertEqual(server_metadata["notes"][0]["folder"], "미지정 워크스페이스")
        self.assertEqual(server_metadata["notes"][0]["workspace"], "미지정 워크스페이스")

    def test_attachment_upload_uses_readable_note_storage_path(self):
        content = b"\x89PNG\r\nreadable-attachment"
        digest = hashlib.sha256(content).hexdigest()
        metadata = self.note_metadata()
        metadata["notes"][0]["title"] = "첨부 노트"
        attachment = self.attachment_metadata(content)

        response = self.client.post(
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

        self.assertEqual(response.status_code, 200)
        upload = response.get_json()
        self.assertEqual(
            upload["attachment"]["storagePath"],
            "memo/attachments/첨부 노트/diagram.png",
        )
        self.assertTrue(
            (
                Path(self.tempdir.name)
                / "files"
                / "memo"
                / "attachments"
                / "첨부 노트"
                / "diagram.png"
            ).exists()
        )

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

    def test_fresh_client_metadata_divergence_does_not_create_synthetic_conflict(self):
        metadata = self.note_metadata()
        upload = self.client.post(
            "/api/sync/file",
            headers=self.headers,
            json={
                "clientId": "existing-client",
                "baseRevision": 0,
                "relativePath": "memo/note.md",
                "lastKnownRevision": 0,
                "updatedAtMs": 10,
                "contentEncoding": "utf-8",
                "content": "# existing server note\n",
                "workspace": {"id": "memo", "name": "memo"},
                "note": metadata["notes"][0],
            },
        )
        self.assertEqual(upload.status_code, 200)

        plan_response = self.client.post(
            "/api/sync/plan",
            headers=self.headers,
            json={
                "clientId": "fresh-windows-client",
                "baseRevision": 0,
                "metadata": {
                    "lastKnownRevision": 0,
                    "body": {
                        "version": 1,
                        "generatedAt": "2026-07-16T00:00:00.000Z",
                        "workspaces": [],
                        "notes": [],
                    },
                },
                "knownFiles": [],
                "knownAttachments": [],
            },
        )

        self.assertEqual(plan_response.status_code, 200)
        payload = plan_response.get_json()
        self.assertEqual(payload["metadata"]["status"], "diverged")
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["plan"]["conflicts"], [])
        self.assertEqual(
            payload["plan"]["downloadFiles"][0]["relativePath"],
            "memo/note.md",
        )

    def test_fresh_client_same_path_becomes_file_conflict(self):
        server_metadata = self.note_metadata(updated_at_ms=10)
        upload = self.client.post(
            "/api/sync/file",
            headers=self.headers,
            json={
                "clientId": "existing-client",
                "baseRevision": 0,
                "relativePath": "memo/note.md",
                "lastKnownRevision": 0,
                "updatedAtMs": 10,
                "contentEncoding": "utf-8",
                "content": "# server content\n",
                "workspace": {"id": "memo", "name": "memo"},
                "note": server_metadata["notes"][0],
            },
        )
        self.assertEqual(upload.status_code, 200)

        local_metadata = self.note_metadata(updated_at_ms=20)
        plan_response = self.client.post(
            "/api/sync/plan",
            headers=self.headers,
            json={
                "clientId": "fresh-client-with-local-note",
                "baseRevision": 0,
                "metadata": {
                    "lastKnownRevision": 0,
                    "body": local_metadata,
                },
                "knownFiles": [],
                "knownAttachments": [],
            },
        )

        self.assertEqual(plan_response.status_code, 200)
        payload = plan_response.get_json()
        self.assertEqual(payload["status"], "conflict")
        self.assertEqual(len(payload["plan"]["conflicts"]), 1)
        conflict = payload["plan"]["conflicts"][0]
        self.assertEqual(conflict["relativePath"], "memo/note.md")
        self.assertEqual(conflict["reason"], "same_path_without_sync_history")

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

        self.assertEqual(metadata_only["attachment"]["status"], "accepted")
        self.assertEqual(
            metadata_only["attachment"]["storagePath"],
            "memo/attachments/새 노트/diagram.png",
        )
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

    def test_plan_uses_content_ancestry_instead_of_device_clock_order(self):
        initial_file = "initial file"
        server_file = "server changed with a future clock"
        local_file = "local changed with a past clock"
        initial_attachment_content = b"initial attachment"
        server_attachment_content = b"server attachment changed"
        local_attachment_content = b"local attachment changed"
        initial_attachment_hash = hashlib.sha256(
            initial_attachment_content
        ).hexdigest()
        server_attachment_hash = hashlib.sha256(
            server_attachment_content
        ).hexdigest()
        local_attachment_hash = hashlib.sha256(
            local_attachment_content
        ).hexdigest()

        initial_metadata = self.note_metadata(updated_at_ms=100)
        initial_attachment = self.attachment_metadata(initial_attachment_content)
        initial_metadata["notes"][0]["attachments"] = [initial_attachment]
        initial_file_upload = self.client.post(
            "/api/sync/file",
            headers=self.headers,
            json={
                "clientId": "device-a",
                "relativePath": "memo/note.md",
                "lastKnownRevision": 0,
                "contentEncoding": "utf-8",
                "content": initial_file,
                "note": initial_metadata["notes"][0],
            },
        ).get_json()
        initial_attachment_upload = self.client.post(
            "/api/sync/attachment",
            headers=self.headers,
            json={
                "clientId": "device-a",
                "noteRelativePath": "memo/note.md",
                "relativePath": initial_attachment["relativePath"],
                "lastKnownRevision": 0,
                "contentEncoding": "base64",
                "content": base64.b64encode(initial_attachment_content).decode("ascii"),
                "contentHash": initial_attachment_hash,
                "note": initial_metadata["notes"][0],
                "attachment": initial_attachment,
            },
        ).get_json()

        server_metadata = self.note_metadata(updated_at_ms=10_000)
        server_attachment = dict(
            initial_attachment,
            contentHash=server_attachment_hash,
            size=len(server_attachment_content),
            updatedAtMs=10_000,
        )
        server_metadata["notes"][0].update(
            {"title": "Server future clock", "attachments": [server_attachment]}
        )
        server_file_upload = self.client.post(
            "/api/sync/file",
            headers=self.headers,
            json={
                "clientId": "device-b",
                "relativePath": "memo/note.md",
                "lastKnownRevision": initial_file_upload["file"]["revision"],
                "contentEncoding": "utf-8",
                "content": server_file,
                "note": server_metadata["notes"][0],
            },
        ).get_json()
        server_attachment_upload = self.client.post(
            "/api/sync/attachment",
            headers=self.headers,
            json={
                "clientId": "device-b",
                "noteRelativePath": "memo/note.md",
                "relativePath": initial_attachment["relativePath"],
                "lastKnownRevision": initial_attachment_upload["attachment"][
                    "revision"
                ],
                "contentEncoding": "base64",
                "content": base64.b64encode(server_attachment_content).decode("ascii"),
                "contentHash": server_attachment_hash,
                "note": server_metadata["notes"][0],
                "attachment": server_attachment,
            },
        ).get_json()
        self.assertEqual(server_file_upload["status"], "ok")
        self.assertEqual(server_attachment_upload["status"], "ok")

        local_metadata = self.note_metadata(updated_at_ms=50)
        local_attachment = dict(
            initial_attachment,
            contentHash=local_attachment_hash,
            size=len(local_attachment_content),
            updatedAtMs=50,
        )
        local_metadata["notes"][0]["attachments"] = [local_attachment]
        changed_plan = self.client.post(
            "/api/sync/plan",
            headers=self.headers,
            json={
                "clientId": "device-a",
                "metadata": {
                    "lastKnownRevision": initial_attachment_upload["manifest"][
                        "metadata"
                    ]["revision"],
                    "body": local_metadata,
                },
                "knownFiles": [
                    {
                        "relativePath": "memo/note.md",
                        "lastKnownRevision": initial_file_upload["file"]["revision"],
                        "contentHash": hashlib.sha256(local_file.encode()).hexdigest(),
                    }
                ],
                "knownAttachments": [
                    {
                        "relativePath": initial_attachment["relativePath"],
                        "lastKnownRevision": initial_attachment_upload["attachment"][
                            "revision"
                        ],
                        "contentHash": local_attachment_hash,
                    }
                ],
            },
        ).get_json()["plan"]
        self.assertEqual(changed_plan["downloadFiles"], [])
        self.assertEqual(changed_plan["downloadAttachments"], [])
        self.assertEqual(
            {item["relativePath"] for item in changed_plan["conflicts"]},
            {"memo/note.md", initial_attachment["relativePath"]},
        )

        stale_plan = self.client.post(
            "/api/sync/plan",
            headers=self.headers,
            json={
                "clientId": "device-c",
                "metadata": {
                    "lastKnownRevision": initial_attachment_upload["manifest"][
                        "metadata"
                    ]["revision"],
                    "body": initial_metadata,
                },
                "knownFiles": [
                    {
                        "relativePath": "memo/note.md",
                        "lastKnownRevision": initial_file_upload["file"]["revision"],
                        "contentHash": hashlib.sha256(initial_file.encode()).hexdigest(),
                    }
                ],
                "knownAttachments": [
                    {
                        "relativePath": initial_attachment["relativePath"],
                        "lastKnownRevision": initial_attachment_upload["attachment"][
                            "revision"
                        ],
                        "contentHash": initial_attachment_hash,
                    }
                ],
            },
        ).get_json()["plan"]
        self.assertEqual(stale_plan["conflicts"], [])
        self.assertEqual(
            [item["relativePath"] for item in stale_plan["downloadFiles"]],
            ["memo/note.md"],
        )
        self.assertEqual(
            [item["relativePath"] for item in stale_plan["downloadAttachments"]],
            [initial_attachment["relativePath"]],
        )

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

    def test_future_or_global_revision_cannot_bypass_optimistic_locks(self):
        metadata = self.note_metadata()
        upload = self.client.post(
            "/api/sync/file",
            headers=self.headers,
            json={
                "clientId": "device-a",
                "relativePath": "memo/note.md",
                "lastKnownRevision": 0,
                "contentEncoding": "utf-8",
                "content": "authoritative content",
                "note": metadata["notes"][0],
            },
        ).get_json()

        overwrite = self.client.post(
            "/api/sync/file",
            headers=self.headers,
            json={
                "clientId": "device-b",
                "relativePath": "memo/note.md",
                "lastKnownRevision": upload["serverRevision"] + 100,
                "contentEncoding": "utf-8",
                "content": "must not bypass the file revision",
            },
        ).get_json()

        self.assertEqual(overwrite["status"], "conflict")
        self.assertEqual(overwrite["file"]["status"], "conflict")
        self.assertEqual(
            overwrite["file"]["serverRevision"],
            upload["file"]["revision"],
        )
        current = self.client.get(
            "/api/files/memo/note.md",
            headers=self.headers,
        ).get_json()
        self.assertEqual(self.decoded_content(current), "authoritative content")

        changed_metadata = self.note_metadata(updated_at_ms=30)
        changed_metadata["notes"][0]["title"] = "Must not overwrite"
        metadata_overwrite = self.client.post(
            "/api/sync",
            headers=self.headers,
            json={
                "clientId": "device-b",
                "metadata": {
                    "lastKnownRevision": upload["serverRevision"] + 100,
                    "body": changed_metadata,
                },
            },
        ).get_json()
        self.assertEqual(metadata_overwrite["status"], "conflict")
        self.assertEqual(metadata_overwrite["metadata"]["status"], "conflict")
        self.assertEqual(
            metadata_overwrite["manifest"]["metadata"],
            upload["manifest"]["metadata"],
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

    def test_rejects_mismatched_nested_note_path_before_file_write(self):
        response = self.client.post(
            "/api/sync/file",
            headers=self.headers,
            json={
                "clientId": "client-a",
                "relativePath": "memo/new.md",
                "lastKnownRevision": 0,
                "contentEncoding": "utf-8",
                "content": "new content",
                "note": {
                    "id": "note-new",
                    "title": "New",
                    "relativePath": "memo/old.md",
                },
            },
        )

        self.assertEqual(response.status_code, 400)
        manifest = self.client.get("/api/manifest", headers=self.headers).get_json()
        self.assertEqual(manifest["files"], [])
        stored_files = [
            path
            for path in (Path(self.tempdir.name) / "files").rglob("*")
            if path.is_file() and ".git" not in path.parts
        ]
        self.assertEqual(stored_files, [])

    def test_rejects_note_id_path_reassignment_without_overwriting(self):
        metadata = self.note_metadata()
        first = self.client.post(
            "/api/sync/file",
            headers=self.headers,
            json={
                "clientId": "client-a",
                "relativePath": "memo/note.md",
                "lastKnownRevision": 0,
                "contentEncoding": "utf-8",
                "content": "original",
                "note": metadata["notes"][0],
            },
        ).get_json()

        different_id = dict(metadata["notes"][0], id="note-2", title="Other")
        response = self.client.post(
            "/api/sync/file",
            headers=self.headers,
            json={
                "clientId": "client-a",
                "relativePath": "memo/note.md",
                "lastKnownRevision": first["file"]["revision"],
                "contentEncoding": "utf-8",
                "content": "replacement",
                "note": different_id,
            },
        )
        self.assertEqual(response.status_code, 400)

        moved_note = dict(
            metadata["notes"][0],
            relativePath="memo/other.md",
            title="Moved",
        )
        response = self.client.post(
            "/api/sync/file",
            headers=self.headers,
            json={
                "clientId": "client-a",
                "relativePath": "memo/other.md",
                "lastKnownRevision": 0,
                "contentEncoding": "utf-8",
                "content": "moved",
                "note": moved_note,
            },
        )
        self.assertEqual(response.status_code, 400)
        current = self.client.get(
            "/api/files/memo/note.md",
            headers=self.headers,
        ).get_json()
        self.assertEqual(self.decoded_content(current), "original")

    def test_rejects_duplicate_metadata_id_and_path_before_batch_write(self):
        metadata = self.note_metadata()
        duplicate = dict(metadata["notes"][0], id="note-2")
        metadata["notes"].append(duplicate)
        response = self.client.post(
            "/api/sync",
            headers=self.headers,
            json={
                "clientId": "client-a",
                "metadata": {"lastKnownRevision": 0, "body": metadata},
                "files": [
                    {
                        "relativePath": "memo/note.md",
                        "lastKnownRevision": 0,
                        "contentEncoding": "utf-8",
                        "content": "must not be written",
                    }
                ],
            },
        )
        self.assertEqual(response.status_code, 400)
        manifest = self.client.get("/api/manifest", headers=self.headers).get_json()
        self.assertEqual(manifest["serverRevision"], 0)
        self.assertEqual(manifest["files"], [])

        metadata = self.note_metadata()
        second = dict(
            metadata["notes"][0],
            relativePath="memo/second.md",
        )
        metadata["notes"].append(second)
        response = self.client.post(
            "/api/sync",
            headers=self.headers,
            json={
                "clientId": "client-a",
                "metadata": {"lastKnownRevision": 0, "body": metadata},
            },
        )
        self.assertEqual(response.status_code, 400)

    def test_same_content_rename_moves_storage_and_stale_retry_cannot_revert(self):
        metadata = self.note_metadata()
        metadata["notes"][0].update(
            {"title": "Old", "fileName": "Old.md", "storagePath": "memo/Old.md"}
        )
        first = self.client.post(
            "/api/sync/file",
            headers=self.headers,
            json={
                "clientId": "client-a",
                "relativePath": "memo/note.md",
                "lastKnownRevision": 0,
                "contentEncoding": "utf-8",
                "content": "same body",
                "note": metadata["notes"][0],
            },
        ).get_json()

        renamed_note = dict(
            metadata["notes"][0],
            title="Renamed",
            fileName="Renamed.md",
            storagePath="memo/Renamed.md",
            updatedAtMs=20,
        )
        renamed = self.client.post(
            "/api/sync/file",
            headers=self.headers,
            json={
                "clientId": "client-a",
                "relativePath": "memo/note.md",
                "lastKnownRevision": first["file"]["revision"],
                "updatedAtMs": 20,
                "contentEncoding": "utf-8",
                "content": "same body",
                "note": renamed_note,
            },
        ).get_json()
        self.assertEqual(renamed["file"]["status"], "accepted")
        self.assertEqual(renamed["file"]["storagePath"], "memo/Renamed.md")
        self.assertEqual(renamed["file"]["revision"], renamed["serverRevision"])
        self.assertEqual(
            renamed["manifest"]["files"][0]["note"]["fileName"],
            "Renamed.md",
        )
        self.assertFalse((Path(self.tempdir.name) / "files/memo/Old.md").exists())
        self.assertTrue((Path(self.tempdir.name) / "files/memo/Renamed.md").exists())

        retry = self.client.post(
            "/api/sync/file",
            headers=self.headers,
            json={
                "clientId": "client-a",
                "relativePath": "memo/note.md",
                "lastKnownRevision": first["file"]["revision"],
                "updatedAtMs": 20,
                "contentEncoding": "utf-8",
                "content": "same body",
                "note": renamed_note,
            },
        ).get_json()
        self.assertEqual(retry["file"]["status"], "unchanged")

        stale = self.client.post(
            "/api/sync/file",
            headers=self.headers,
            json={
                "clientId": "client-a",
                "relativePath": "memo/note.md",
                "lastKnownRevision": first["file"]["revision"],
                "contentEncoding": "utf-8",
                "content": "same body",
                "note": metadata["notes"][0],
            },
        ).get_json()
        self.assertEqual(stale["status"], "conflict")
        self.assertTrue((Path(self.tempdir.name) / "files/memo/Renamed.md").exists())

    def test_same_content_rename_avoids_orphan_physical_target(self):
        metadata = self.note_metadata()
        metadata["notes"][0].update(
            {"title": "Old", "fileName": "Old.md", "storagePath": "memo/Old.md"}
        )
        first = self.client.post(
            "/api/sync/file",
            headers=self.headers,
            json={
                "clientId": "client-a",
                "relativePath": "memo/note.md",
                "lastKnownRevision": 0,
                "contentEncoding": "utf-8",
                "content": "same body",
                "note": metadata["notes"][0],
            },
        ).get_json()

        orphan_path = Path(self.tempdir.name) / "files/memo/Renamed.md"
        orphan_path.write_text("orphan content", encoding="utf-8")
        renamed_note = dict(
            metadata["notes"][0],
            title="Renamed",
            fileName="Renamed.md",
            storagePath="memo/Renamed.md",
        )
        response = self.client.post(
            "/api/sync/file",
            headers=self.headers,
            json={
                "clientId": "client-a",
                "relativePath": "memo/note.md",
                "lastKnownRevision": first["file"]["revision"],
                "contentEncoding": "utf-8",
                "content": "same body",
                "note": renamed_note,
            },
        )

        self.assertEqual(response.status_code, 200)
        renamed = response.get_json()["file"]
        self.assertEqual(renamed["status"], "accepted")
        self.assertNotEqual(renamed["storagePath"], "memo/Renamed.md")
        self.assertTrue(
            (Path(self.tempdir.name) / "files" / renamed["storagePath"]).exists()
        )
        self.assertEqual(orphan_path.read_text(encoding="utf-8"), "orphan content")

    def test_full_metadata_preserves_existing_note_and_attachment_identities(self):
        content = b"attachment"
        metadata = self.note_metadata()
        metadata["notes"][0]["attachments"] = [self.attachment_metadata(content)]
        first = self.client.post(
            "/api/sync/file",
            headers=self.headers,
            json={
                "clientId": "client-a",
                "relativePath": "memo/note.md",
                "lastKnownRevision": 0,
                "contentEncoding": "utf-8",
                "content": "body",
                "note": metadata["notes"][0],
            },
        ).get_json()
        metadata_revision = first["manifest"]["metadata"]["revision"]

        missing_note_id = self.note_metadata()
        missing_note_id["notes"][0].pop("id")
        response = self.client.post(
            "/api/sync",
            headers=self.headers,
            json={
                "clientId": "client-a",
                "metadata": {
                    "lastKnownRevision": metadata_revision,
                    "body": missing_note_id,
                },
            },
        )
        self.assertEqual(response.status_code, 400)

        remapped_attachment = self.note_metadata()
        attachment = self.attachment_metadata(content)
        attachment["id"] = "att-2"
        remapped_attachment["notes"][0]["attachments"] = [attachment]
        response = self.client.post(
            "/api/sync",
            headers=self.headers,
            json={
                "clientId": "client-a",
                "metadata": {
                    "lastKnownRevision": metadata_revision,
                    "body": remapped_attachment,
                },
            },
        )
        self.assertEqual(response.status_code, 400)

    def test_full_metadata_requires_matching_delete_for_active_attachment(self):
        content = b"active attachment"
        digest = hashlib.sha256(content).hexdigest()
        metadata = self.note_metadata()
        attachment = self.attachment_metadata(content)
        metadata["notes"][0]["attachments"] = [attachment]
        upload = self.client.post(
            "/api/sync/attachment",
            headers=self.headers,
            json={
                "clientId": "client-a",
                "noteRelativePath": "memo/note.md",
                "relativePath": attachment["relativePath"],
                "lastKnownRevision": 0,
                "contentEncoding": "base64",
                "content": base64.b64encode(content).decode("ascii"),
                "contentHash": digest,
                "note": metadata["notes"][0],
                "attachment": attachment,
            },
        ).get_json()

        without_attachment = self.note_metadata()
        without_attachment["notes"][0]["attachments"] = []
        rejected = self.client.post(
            "/api/sync",
            headers=self.headers,
            json={
                "clientId": "client-a",
                "metadata": {
                    "lastKnownRevision": upload["manifest"]["metadata"]["revision"],
                    "body": without_attachment,
                },
            },
        ).get_json()
        self.assertEqual(rejected["status"], "conflict")
        self.assertEqual(
            rejected["metadata"]["reason"],
            "metadata_removes_existing_server_attachments",
        )

        deleted = self.client.post(
            "/api/sync",
            headers=self.headers,
            json={
                "clientId": "client-a",
                "metadata": {
                    "lastKnownRevision": upload["manifest"]["metadata"]["revision"],
                    "body": without_attachment,
                },
                "attachments": [
                    {
                        "relativePath": attachment["relativePath"],
                        "lastKnownRevision": upload["attachment"]["revision"],
                        "deleted": True,
                    }
                ],
            },
        ).get_json()
        self.assertEqual(deleted["status"], "ok")
        self.assertEqual(deleted["metadata"]["status"], "accepted")
        self.assertTrue(deleted["acceptedAttachments"][0]["deleted"])

        state_only_path = "memo/.attachments/note-1/state-only.bin"
        state_only = self.client.post(
            "/api/sync",
            headers=self.headers,
            json={
                "clientId": "client-a",
                "attachments": [
                    {
                        "relativePath": state_only_path,
                        "noteRelativePath": "memo/note.md",
                        "lastKnownRevision": 0,
                        "contentEncoding": "base64",
                        "content": base64.b64encode(b"state only").decode("ascii"),
                        "attachment": {"id": "att-state"},
                    }
                ],
            },
        ).get_json()
        state_only_removal = self.client.post(
            "/api/sync",
            headers=self.headers,
            json={
                "clientId": "client-a",
                "metadata": {
                    "lastKnownRevision": state_only["manifest"]["metadata"]["revision"],
                    "body": without_attachment,
                },
            },
        ).get_json()
        self.assertEqual(state_only_removal["status"], "conflict")
        self.assertEqual(
            state_only_removal["metadata"]["reason"],
            "metadata_removes_existing_server_attachments",
        )
        remapped = self.note_metadata()
        remapped["notes"][0]["attachments"] = [
            {"id": "att-remapped", "relativePath": state_only_path}
        ]
        response = self.client.post(
            "/api/sync",
            headers=self.headers,
            json={
                "clientId": "client-a",
                "metadata": {
                    "lastKnownRevision": state_only["manifest"]["metadata"]["revision"],
                    "body": remapped,
                },
            },
        )
        self.assertEqual(response.status_code, 400)

    def test_attachment_metadata_only_retry_stale_guard_and_canonical_storage(self):
        content = b"attachment metadata"
        digest = hashlib.sha256(content).hexdigest()
        metadata = self.note_metadata()
        attachment = self.attachment_metadata(content)
        metadata["notes"][0]["attachments"] = [attachment]
        orphan_path = (
            Path(self.tempdir.name)
            / "files/memo/attachments/새 노트/diagram.png"
        )
        orphan_path.parent.mkdir(parents=True)
        orphan_path.write_bytes(b"orphan")

        upload = self.client.post(
            "/api/sync/attachment",
            headers=self.headers,
            json={
                "clientId": "client-a",
                "noteRelativePath": "memo/note.md",
                "relativePath": attachment["relativePath"],
                "lastKnownRevision": 0,
                "contentEncoding": "base64",
                "content": base64.b64encode(content).decode("ascii"),
                "contentHash": digest,
                "note": metadata["notes"][0],
                "attachment": attachment,
            },
        ).get_json()
        storage_path = upload["attachment"]["storagePath"]
        self.assertNotEqual(storage_path, "memo/attachments/새 노트/diagram.png")

        updated_attachment = dict(
            attachment,
            mimeType="application/octet-stream",
            tags=["synced"],
            status="reviewed",
        )
        update_payload = {
            "clientId": "client-a",
            "noteRelativePath": "memo/note.md",
            "relativePath": attachment["relativePath"],
            "lastKnownRevision": upload["attachment"]["revision"],
            "contentHash": digest,
            "attachment": updated_attachment,
        }
        updated = self.client.post(
            "/api/sync/attachment",
            headers=self.headers,
            json=update_payload,
        ).get_json()
        self.assertEqual(updated["metadata"]["status"], "accepted")
        self.assertEqual(updated["attachment"]["revision"], updated["serverRevision"])

        retry = self.client.post(
            "/api/sync/attachment",
            headers=self.headers,
            json=update_payload,
        ).get_json()
        self.assertEqual(retry["status"], "ok")
        self.assertEqual(retry["metadata"]["status"], "unchanged")

        stale_attachment = dict(attachment, tags=["stale"], status="draft")
        stale = self.client.post(
            "/api/sync/attachment",
            headers=self.headers,
            json={**update_payload, "attachment": stale_attachment},
        ).get_json()
        self.assertEqual(stale["status"], "conflict")

        state = json.loads(
            (Path(self.tempdir.name) / "state.json").read_text(encoding="utf-8")
        )
        record = state["files"][attachment["relativePath"]]
        self.assertEqual(record["revision"], updated["serverRevision"])
        self.assertEqual(record["attachmentSnapshot"]["tags"], ["synced"])
        self.assertEqual(record["attachmentSnapshot"]["status"], "reviewed")
        self.assertEqual(record["attachmentSnapshot"]["storagePath"], storage_path)
        self.assertEqual(
            record["attachmentSnapshot"]["fileName"],
            Path(storage_path).name,
        )
        self.assertEqual(orphan_path.read_bytes(), b"orphan")

    def test_attachment_requires_note_path_before_single_or_batch_write(self):
        for endpoint, relative_path in (
            ("/api/sync/attachment", "a.bin"),
            ("/api/sync", "b.bin"),
        ):
            item = {
                "relativePath": relative_path,
                "lastKnownRevision": 0,
                "contentEncoding": "base64",
                "content": base64.b64encode(b"orphan").decode("ascii"),
            }
            body = {"clientId": "client-a", **item}
            if endpoint == "/api/sync":
                body = {"clientId": "client-a", "attachments": [item]}
            response = self.client.post(endpoint, headers=self.headers, json=body)
            self.assertEqual(response.status_code, 400)

        manifest = self.client.get("/api/manifest", headers=self.headers).get_json()
        self.assertEqual(manifest["serverRevision"], 0)
        self.assertEqual(manifest["attachments"], [])
        self.assertFalse((Path(self.tempdir.name) / "files/a.bin").exists())
        self.assertFalse((Path(self.tempdir.name) / "files/b.bin").exists())

    def test_atomic_write_keeps_secure_new_mode_and_preserves_existing_mode(self):
        path = Path(self.tempdir.name) / "private.json"
        previous_umask = os.umask(0o077)
        try:
            atomic_write_bytes(path, b"first")
        finally:
            os.umask(previous_umask)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

        path.chmod(0o640)
        atomic_write_bytes(path, b"second")
        self.assertEqual(path.stat().st_mode & 0o777, 0o640)
        self.assertEqual(path.read_bytes(), b"second")

    def test_batch_validation_prevents_partial_metadata_and_file_writes(self):
        metadata = self.note_metadata()
        response = self.client.post(
            "/api/sync",
            headers=self.headers,
            json={
                "clientId": "client-a",
                "metadata": {"lastKnownRevision": 0, "body": metadata},
                "files": [
                    {
                        "relativePath": "memo/note.md",
                        "lastKnownRevision": 0,
                        "contentEncoding": "utf-8",
                        "content": "valid first",
                    },
                    {
                        "relativePath": "memo/invalid.md",
                        "lastKnownRevision": 0,
                        "contentEncoding": "utf-8",
                    },
                ],
            },
        )
        self.assertEqual(response.status_code, 400)
        manifest = self.client.get("/api/manifest", headers=self.headers).get_json()
        self.assertEqual(manifest["serverRevision"], 0)
        self.assertEqual(manifest["metadata"]["revision"], 0)
        self.assertEqual(manifest["files"], [])

    def test_batch_conflict_skips_all_metadata_and_file_mutations(self):
        metadata = self.note_metadata()
        first = self.client.post(
            "/api/sync/file",
            headers=self.headers,
            json={
                "clientId": "device-a",
                "relativePath": "memo/note.md",
                "lastKnownRevision": 0,
                "contentEncoding": "utf-8",
                "content": "server version",
                "note": metadata["notes"][0],
            },
        ).get_json()
        before = first["manifest"]

        next_metadata = self.note_metadata(updated_at_ms=20)
        next_metadata["notes"].append(
            {
                "id": "note-2",
                "title": "New note",
                "workspace": "memo",
                "relativePath": "memo/new.md",
                "updatedAtMs": 20,
            }
        )
        batch = self.client.post(
            "/api/sync",
            headers=self.headers,
            json={
                "clientId": "device-b",
                "metadata": {
                    "lastKnownRevision": before["metadata"]["revision"],
                    "body": next_metadata,
                },
                "files": [
                    {
                        "relativePath": "memo/new.md",
                        "lastKnownRevision": 0,
                        "contentEncoding": "utf-8",
                        "content": "must be skipped",
                    },
                    {
                        "relativePath": "memo/note.md",
                        "lastKnownRevision": 0,
                        "contentEncoding": "utf-8",
                        "content": "stale overwrite",
                    },
                ],
            },
        ).get_json()

        self.assertEqual(batch["status"], "conflict")
        self.assertEqual(batch["accepted"], [])
        self.assertEqual(batch["metadata"]["status"], "skipped")
        self.assertEqual(batch["metadata"]["reason"], "batch_conflict")
        self.assertEqual(
            batch["metadata"]["revision"],
            before["metadata"]["revision"],
        )
        self.assertGreater(
            batch["metadata"]["previewRevision"],
            batch["metadata"]["revision"],
        )
        self.assertEqual(
            [item["relativePath"] for item in batch["skipped"]],
            ["memo/new.md"],
        )
        self.assertEqual(batch["manifest"]["serverRevision"], before["serverRevision"])
        self.assertEqual(batch["manifest"]["metadata"], before["metadata"])

        missing = self.client.get("/api/files/memo/new.md", headers=self.headers)
        self.assertEqual(missing.status_code, 400)
        current = self.client.get("/api/files/memo/note.md", headers=self.headers)
        self.assertEqual(self.decoded_content(current.get_json()), "server version")

    def test_rejects_duplicate_plan_and_batch_paths_before_writes(self):
        plan = self.client.post(
            "/api/sync/plan",
            headers=self.headers,
            json={
                "clientId": "client-a",
                "metadata": {
                    "lastKnownRevision": 0,
                    "body": {"version": 1, "workspaces": [], "notes": []},
                },
                "knownFiles": [
                    {"relativePath": "memo/note.md", "lastKnownRevision": 0},
                    {"relativePath": "memo/note.md", "lastKnownRevision": 0},
                ],
            },
        )
        self.assertEqual(plan.status_code, 400)
        self.assertIn("duplicate relativePath", plan.get_json()["message"])

        batch = self.client.post(
            "/api/sync",
            headers=self.headers,
            json={
                "clientId": "client-a",
                "files": [
                    {
                        "relativePath": "memo/note.md",
                        "lastKnownRevision": 0,
                        "contentEncoding": "utf-8",
                        "content": "first",
                    },
                    {
                        "relativePath": "memo/note.md",
                        "lastKnownRevision": 0,
                        "contentEncoding": "utf-8",
                        "content": "second",
                    },
                ],
            },
        )
        self.assertEqual(batch.status_code, 400)
        self.assertIn("duplicate relativePath", batch.get_json()["message"])
        manifest = self.client.get("/api/manifest", headers=self.headers).get_json()
        self.assertEqual(manifest["serverRevision"], 0)
        self.assertEqual(manifest["files"], [])

    def test_mutation_failure_rolls_back_file_metadata_state_and_allows_retry(self):
        with TemporaryDirectory() as storage:
            store = SyncStore(storage)
            store.initialize()
            metadata = self.note_metadata()
            payload = {
                "clientId": "client-a",
                "relativePath": "memo/note.md",
                "lastKnownRevision": 0,
                "contentEncoding": "utf-8",
                "content": "transactional content",
                "note": metadata["notes"][0],
            }
            original_atomic_write_json = sync_store.atomic_write_json

            def fail_final_state_write(path, value):
                if Path(path) == store.state_path:
                    raise OSError("simulated state write failure")
                return original_atomic_write_json(path, value)

            with mock.patch(
                "sync_store.atomic_write_json",
                side_effect=fail_final_state_write,
            ):
                with self.assertRaises(OSError):
                    store.sync_file_upload(payload, user="tester")

            manifest = store.manifest()
            self.assertEqual(manifest["serverRevision"], 0)
            self.assertEqual(manifest["metadata"]["revision"], 0)
            self.assertEqual(manifest["files"], [])
            self.assertFalse((Path(storage) / "files/memo/새 노트.md").exists())
            self.assertFalse(store.transaction_root.exists())

            retry = store.sync_file_upload(payload, user="tester")
            self.assertEqual(retry["status"], "ok")
            self.assertEqual(retry["file"]["status"], "accepted")

    def test_initialize_recovers_interrupted_durable_transaction(self):
        with TemporaryDirectory() as storage:
            store = SyncStore(storage)
            store.initialize()
            state = store._load_state()
            store._begin_mutation_transaction()
            result = store._sync_file(
                state,
                {
                    "relativePath": "memo/crash.md",
                    "lastKnownRevision": 0,
                    "contentEncoding": "utf-8",
                    "content": "interrupted write",
                },
                base_revision=0,
                now="2026-08-19T00:00:00Z",
            )
            self.assertEqual(result["status"], "accepted")
            sync_store.atomic_write_json(store.state_path, state)
            self.assertTrue((Path(storage) / "files/memo/crash.md").exists())
            self.assertTrue(store.transaction_root.exists())

            recovered_store = SyncStore(storage)
            recovered_store.initialize()
            manifest = recovered_store.manifest()
            self.assertEqual(manifest["serverRevision"], 0)
            self.assertEqual(manifest["files"], [])
            self.assertFalse((Path(storage) / "files/memo/crash.md").exists())
            self.assertFalse(recovered_store.transaction_root.exists())

    def test_client_telemetry_is_bounded_and_sanitized(self):
        with TemporaryDirectory() as storage:
            store = SyncStore(storage)
            state = store._initial_state("2026-08-19T00:00:00Z")
            for index in range(MAX_TRACKED_CLIENTS + 5):
                store._record_client(
                    state,
                    f"client-{index:03d}",
                    "tester",
                    {
                        "clientInfo": {
                            "label": "x" * (MAX_CLIENT_INFO_STRING_LENGTH + 50),
                            "items": list(range(MAX_CLIENT_INFO_LIST_ITEMS + 10)),
                        }
                    },
                    None,
                    f"2026-08-19T00:{index:03d}:00Z",
                    "plan",
                )

            self.assertEqual(len(state["clients"]), MAX_TRACKED_CLIENTS)
            self.assertNotIn("client-000", state["clients"])
            latest = state["clients"][f"client-{MAX_TRACKED_CLIENTS + 4:03d}"]
            self.assertEqual(
                len(latest["clientInfo"]["label"]),
                MAX_CLIENT_INFO_STRING_LENGTH,
            )
            self.assertEqual(
                len(latest["clientInfo"]["items"]),
                MAX_CLIENT_INFO_LIST_ITEMS,
            )

        too_long = self.client.post(
            "/api/sync/plan",
            headers=self.headers,
            json={
                "clientId": "x" * 257,
                "metadata": {
                    "lastKnownRevision": 0,
                    "body": {"version": 1, "workspaces": [], "notes": []},
                },
            },
        )
        self.assertEqual(too_long.status_code, 400)
        self.assertIn("at most", too_long.get_json()["message"])

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
