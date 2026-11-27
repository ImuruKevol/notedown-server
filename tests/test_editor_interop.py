"""Round-trip browser documents through the existing desktop sync contract."""

import base64
import tempfile
import unittest
from urllib.parse import quote, unquote

from fastapi.testclient import TestClient

from app import create_app


class EditorInteropTest(unittest.TestCase):
    def test_browser_upload_desktop_download_and_edit_preserve_identity_and_lock(self):
        with tempfile.TemporaryDirectory() as storage:
            app = create_app({
                "NOTE_SYNC_STORAGE": storage,
                "NOTE_SYNC_USERNAME": "owner",
                "NOTE_SYNC_PASSWORD": "password",
                "NOTE_SYNC_SECRET": "interop-test-secret",
            })
            with TestClient(app) as browser, TestClient(app) as desktop:
                login = browser.post("/api/login", json={"username": "owner", "password": "password"}).json()
                desktop.headers["Authorization"] = "Bearer " + login["accessToken"]
                workspace = browser.post("/api/editor/workspaces", json={"name": "회의 자료"}).json()["workspace"]
                initial = browser.post("/api/editor/notes", json={
                    "workspaceId": workspace["id"], "title": "브라우저 문서", "content": "# 첫 내용",
                }).json()
                endpoint = "/api/editor/notes/" + initial["note"]["id"]
                upload = browser.post(endpoint + "/attachments", files={
                    "file": ("보고서 (최종)#1.txt", b"attachment contents", "text/plain"),
                }).json()
                browser_content = "# 브라우저 작성\n\n" + upload["markdown"]
                save = browser.put(endpoint, json={
                    "title": "브라우저 문서", "content": browser_content, "revision": initial["revision"],
                })
                self.assertEqual(save.status_code, 200, save.text)
                saved = save.json()

                plan_response = desktop.post("/api/sync/plan", json={
                    "clientId": "fresh-desktop", "baseRevision": 0,
                    "metadata": {"lastKnownRevision": 0, "body": {"version": 1, "workspaces": [], "notes": []}},
                    "knownFiles": [], "knownAttachments": [],
                })
                self.assertEqual(plan_response.status_code, 200, plan_response.text)
                plan = plan_response.json()
                self.assertEqual(plan["plan"]["conflicts"], [])
                self.assertEqual(plan["plan"]["deleteServerFiles"], [])
                self.assertEqual(len(plan["plan"]["downloadFiles"]), 1)
                self.assertEqual(len(plan["plan"]["downloadAttachments"]), 1)
                note = plan["metadata"]["serverMetadata"]["notes"][0]
                self.assertEqual(note["id"], initial["note"]["id"])
                self.assertEqual(note["relativePath"], initial["note"]["relativePath"])
                attachment = note["attachments"][0]
                self.assertEqual(attachment["id"], upload["attachment"]["id"])
                self.assertEqual(unquote(upload["attachment"]["markdownPath"]), attachment["relativePath"])
                file_payload = desktop.get("/api/files/" + quote(note["relativePath"], safe="/")).json()
                self.assertEqual(base64.b64decode(file_payload["content"]).decode(), browser_content)
                attachment_payload = desktop.get("/api/attachments/" + quote(attachment["relativePath"], safe="/")).json()
                self.assertEqual(base64.b64decode(attachment_payload["content"]), b"attachment contents")

                note["title"] = "데스크톱에서 수정"
                note["updatedAtMs"] += 1
                desktop_content = browser_content + "\n\n데스크톱 추가 내용"
                edited = desktop.post("/api/sync/file", json={
                    "clientId": "fresh-desktop", "baseRevision": plan["serverRevision"],
                    "relativePath": note["relativePath"], "lastKnownRevision": saved["revision"],
                    "contentEncoding": "utf-8", "content": desktop_content,
                    "note": note, "updatedAtMs": note["updatedAtMs"],
                })
                self.assertEqual(edited.status_code, 200, edited.text)
                self.assertEqual(edited.json()["status"], "ok")
                rejected = browser.put(endpoint, json={
                    "title": "오래된 창", "content": "오래된 내용", "revision": saved["revision"],
                })
                self.assertEqual(rejected.status_code, 409, rejected.text)
                latest = browser.get(endpoint).json()
                self.assertEqual(latest["content"], desktop_content)
                self.assertEqual(latest["note"]["title"], "데스크톱에서 수정")
                self.assertEqual(latest["note"]["attachments"][0]["id"], attachment["id"])
                self.assertEqual(browser.get(upload["attachment"]["url"]).content, b"attachment contents")


if __name__ == "__main__":
    unittest.main()
