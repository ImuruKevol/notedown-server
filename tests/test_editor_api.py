"""Integration coverage for browser editing and desktop sync interoperability."""

import base64
import concurrent.futures
import tempfile
import unittest
from unittest import mock
from urllib.parse import unquote

from fastapi.testclient import TestClient

import editor_api
from app import create_app


PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a3ioAAAAASUVORK5CYII=")


class EditorApiTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = create_app({
            "NOTE_SYNC_STORAGE": self.temp.name,
            "NOTE_SYNC_USERNAME": "tester",
            "NOTE_SYNC_PASSWORD": "secret",
            "NOTE_SYNC_SECRET": "editor-test-secret",
            "NOTE_SYNC_PUBLIC_ORIGIN": "https://notes.example",
        })
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        login = self.client.post("/api/login", json={"username": "tester", "password": "secret"})
        self.assertEqual(login.status_code, 200)
        self.token = login.json()["accessToken"]
        response = self.client.post("/api/editor/workspaces", json={"name": "개발 기록"})
        self.assertEqual(response.status_code, 201, response.text)
        self.workspace = response.json()["workspace"]

    def create_note(self, title="첫 문서", content="# 안녕하세요\n\n- [ ] 할 일"):
        response = self.client.post("/api/editor/notes", json={"workspaceId": self.workspace["id"], "title": title, "content": content})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def upload(self, note, name="이미지.png", content=PNG, media_type="image/png"):
        response = self.client.post(f"/api/editor/notes/{note['note']['id']}/attachments", files={"file": (name, content, media_type)})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def save(self, note, content, title="수정한 문서"):
        return self.client.put(f"/api/editor/notes/{note['note']['id']}", json={"revision": note["revision"], "title": title, "content": content})

    def test_create_edit_rename_and_desktop_download_history(self):
        note = self.create_note()
        path = note["note"]["relativePath"]
        response = self.save(note, "# 저장한 내용\n\n| 항목 | 값 |\n|---|---|\n| 상태 | 완료 |")
        self.assertEqual(response.status_code, 200, response.text)
        saved = response.json()
        self.assertEqual(saved["note"]["relativePath"], path)
        self.assertGreater(saved["revision"], note["revision"])
        self.assertEqual(saved["note"]["fileName"], "수정한 문서.md")
        desktop = self.client.get("/api/files/" + path)
        self.assertEqual(desktop.status_code, 200, desktop.text)
        self.assertEqual(base64.b64decode(desktop.json()["content"]).decode(), saved["content"])
        history = self.app.state.store.file_history(path)
        self.assertGreaterEqual(len(history["commits"]), 2)
        original_commit = history["commits"][-1]["commit"]
        original_version = self.app.state.store.file_version_payload(path, original_commit)
        self.assertEqual(base64.b64decode(original_version["content"]).decode(), note["content"])
        library = self.client.get("/api/editor/library").json()
        self.assertEqual(library["workspaces"][0]["count"], 1)
        self.assertEqual(library["notes"][0]["title"], "수정한 문서")
        self.app.state.store.rollback_file(path, original_commit, user="tester")
        restored = self.client.get(f"/api/editor/notes/{note['note']['id']}").json()
        self.assertEqual(restored["content"], note["content"])
        self.assertEqual(restored["note"]["title"], note["note"]["title"])

    def test_stale_save_and_delete_are_rejected_without_losing_content(self):
        note = self.create_note()
        saved = self.save(note, "# 다른 창에서 저장").json()
        stale = self.save(note, "# 오래된 내용")
        self.assertEqual(stale.status_code, 409, stale.text)
        self.assertEqual(stale.json()["error"], "revision_conflict")
        stale_delete = self.client.delete(f"/api/editor/notes/{note['note']['id']}", params={"revision": note["revision"]})
        self.assertEqual(stale_delete.status_code, 409)
        latest = self.client.get(f"/api/editor/notes/{note['note']['id']}").json()
        self.assertEqual(latest["content"], saved["content"])

    def test_browser_save_at_configured_https_origin_behind_http_proxy(self):
        note = self.create_note()
        endpoint = f"/api/editor/notes/{note['note']['id']}"
        payload = {"revision": note["revision"], "title": note["note"]["title"], "content": "saved through proxy"}
        with TestClient(self.app, base_url="http://notes.example") as proxy:
            login = proxy.post("/api/login", json={"username": "tester", "password": "secret"})
            self.assertEqual(login.status_code, 200)
            same_origin = {"Origin": "https://notes.example", "Sec-Fetch-Site": "same-origin"}
            for headers in (
                {**same_origin, "Origin": "https://other.example"},
                {**same_origin, "Sec-Fetch-Site": "cross-site"},
                {**same_origin, "Origin": "http://notes.example"},
            ):
                blocked = proxy.put(endpoint, headers=headers, json=payload)
                self.assertEqual(blocked.status_code, 403)
                self.assertEqual(blocked.json()["error"], "cross_origin_session_write")

            wrong_host = self.client.put(endpoint, headers=same_origin, json=payload)
            self.assertEqual(wrong_host.status_code, 403)
            saved = proxy.put(endpoint, headers=same_origin, json=payload)
            self.assertEqual(saved.status_code, 200, saved.text)
            self.assertEqual(proxy.get(endpoint).json()["content"], payload["content"])

    def test_concurrent_saves_accept_only_one_revision(self):
        note = self.create_note()
        endpoint = f"/api/editor/notes/{note['note']['id']}"

        def save(value):
            with TestClient(self.app) as client:
                return client.put(endpoint, headers={"Authorization": "Bearer " + self.token}, json={"revision": note["revision"], "title": "동시 저장", "content": value}).status_code

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            statuses = list(executor.map(save, ["A", "B"]))
        self.assertCountEqual(statuses, [200, 409])

    def test_duplicate_filename_uploads_preserve_both_and_desktop_links(self):
        note = self.create_note()
        first = self.upload(note)
        second = self.upload(note, content=PNG + b"second")
        self.assertNotEqual(first["attachment"]["relativePath"], second["attachment"]["relativePath"])
        self.assertEqual(unquote(first["attachment"]["markdownPath"]), first["attachment"]["relativePath"])
        self.assertEqual(first["revision"], note["revision"])
        self.assertEqual(self.client.get(first["attachment"]["url"]).content, PNG)
        self.assertEqual(self.client.get(second["attachment"]["url"]).content, PNG + b"second")
        self.assertEqual(self.client.get(first["attachment"]["url"]).headers["content-type"], "image/png")
        self.assertTrue(first["markdown"].startswith("!["))
        saved = self.save(note, first["markdown"] + "\n" + second["markdown"]).json()
        self.assertEqual(len(saved["note"]["attachments"]), 2)
        desktop = self.client.get("/api/attachments/" + first["attachment"]["relativePath"])
        self.assertEqual(base64.b64decode(desktop.json()["content"]), PNG)

    def test_active_uploads_are_downloads_even_when_claiming_image_mime(self):
        note = self.create_note()
        uploaded = self.upload(note, "../../danger.svg", b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"/>', "image/png")
        self.assertEqual(uploaded["attachment"]["fileName"], "danger.svg")
        self.assertFalse(uploaded["markdown"].startswith("!"))
        response = self.client.get(uploaded["attachment"]["url"])
        self.assertEqual(response.headers["content-type"], "application/octet-stream")
        self.assertTrue(response.headers["content-disposition"].startswith("attachment;"))
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        self.assertIn("sandbox", response.headers["content-security-policy"])

    def test_note_delete_tombstones_attachments_atomically(self):
        note = self.create_note()
        uploaded = self.upload(note)
        response = self.client.delete(f"/api/editor/notes/{note['note']['id']}", params={"revision": note["revision"]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.client.get(uploaded["attachment"]["url"]).status_code, 404)
        self.assertEqual(self.client.get("/api/editor/library").json()["notes"], [])
        manifest = self.app.state.store.manifest()
        self.assertTrue(manifest["files"][0]["deleted"])
        self.assertTrue(manifest["attachments"][0]["deleted"])

    def test_empty_note_delete_after_other_note_rollback_preserves_attachments(self):
        previous = self.create_note()
        path = previous["note"]["relativePath"]
        original_commit = self.app.state.store.file_history(path)["commits"][0]["commit"]
        uploaded = self.upload(previous)
        self.assertEqual(self.save(previous, "# 변경한 문서").status_code, 200)
        self.app.state.store.rollback_file(path, original_commit, user="tester")
        empty = self.create_note(title="삭제할 빈 문서", content="")
        response = self.client.delete(f"/api/editor/notes/{empty['note']['id']}", params={"revision": empty["revision"]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.client.get(uploaded["attachment"]["url"]).content, PNG)
        library = self.client.get("/api/editor/library").json()
        self.assertEqual(len(library["notes"]), 1)
        self.assertEqual(len(library["notes"][0]["attachments"]), 1)

    def test_attachment_delete_retains_note_and_blocks_stale_delete(self):
        note = self.create_note()
        uploaded = self.upload(note)
        attachment = uploaded["attachment"]
        endpoint = f"/api/editor/notes/{note['note']['id']}/attachments/{attachment['id']}"
        self.assertEqual(self.client.delete(endpoint, params={"revision": 0}).status_code, 409)
        response = self.client.delete(endpoint, params={"revision": attachment["revision"]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["note"]["attachments"], [])
        self.assertEqual(response.json()["content"], note["content"])
        self.assertEqual(self.client.get(attachment["url"]).status_code, 404)

    def test_anonymous_access_blocked_and_validation_is_bounded(self):
        note = self.create_note()
        uploaded = self.upload(note)
        with TestClient(self.app) as anonymous:
            for endpoint in ["/api/editor/library", f"/api/editor/notes/{note['note']['id']}", uploaded["attachment"]["url"]]:
                self.assertEqual(anonymous.get(endpoint).status_code, 401)
        self.assertEqual(self.client.post("/api/editor/workspaces", json={"name": "   "}).status_code, 422)
        self.assertEqual(self.client.post("/api/editor/workspaces", json={"name": "개발 기록"}).status_code, 409)
        self.assertEqual(self.client.post("/api/editor/notes", json={"workspaceId": "../../outside", "title": "오류"}).status_code, 404)
        self.assertEqual(self.save(note, "내용", "가" * 120).status_code, 422)
        with mock.patch.object(editor_api, "MAX_ATTACHMENT_BYTES", 10):
            oversized = self.client.post(f"/api/editor/notes/{note['note']['id']}/attachments", files={"file": ("large.txt", b"x" * 11, "text/plain")})
            self.assertEqual(oversized.status_code, 413)
        with mock.patch.object(editor_api, "MAX_NOTE_BYTES", 4):
            self.assertEqual(self.save(note, "가나다").status_code, 413)

    def test_sync_files_without_note_metadata_can_be_edited(self):
        store = self.app.state.store
        state = store.manifest()
        store.sync_file_upload({"clientId": "desktop", "baseRevision": state["serverRevision"], "relativePath": "legacy/note.md", "lastKnownRevision": 0, "content": "# Existing", "contentEncoding": "utf-8"}, "tester")
        library = self.client.get("/api/editor/library").json()
        self.assertEqual(len(library["notes"]), 1)
        note = self.client.get("/api/editor/notes/" + library["notes"][0]["id"]).json()
        self.assertEqual(note["content"], "# Existing")
        response = self.save(note, "# Migrated")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["note"]["relativePath"], "legacy/note.md")

    def test_failed_save_rolls_back_content_and_revision(self):
        note = self.create_note()
        with mock.patch.object(self.app.state.store, "_commit_file_change", side_effect=RuntimeError("injected Git failure")):
            with TestClient(self.app, raise_server_exceptions=False) as failing_client:
                response = failing_client.put(f"/api/editor/notes/{note['note']['id']}", headers={"Authorization": "Bearer " + self.token}, json={"revision": note["revision"], "title": "실패한 저장", "content": "must not persist"})
        self.assertEqual(response.status_code, 500)
        latest = self.client.get(f"/api/editor/notes/{note['note']['id']}").json()
        self.assertEqual(latest["content"], note["content"])
        self.assertEqual(latest["revision"], note["revision"])
        self.assertEqual(latest["note"]["title"], note["note"]["title"])


if __name__ == "__main__":
    unittest.main()
