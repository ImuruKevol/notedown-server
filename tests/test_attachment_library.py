"""Attachment cleanup must protect references and use transactional sync writes."""

import base64
import concurrent.futures
import copy
import posixpath
import tempfile
import unittest
from unittest import mock
from urllib.parse import quote

from fastapi.testclient import TestClient

from app import create_app
from editor_api import _referenced_attachment_paths


class AttachmentLibraryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = create_app({"NOTE_SYNC_STORAGE": self.temp.name, "NOTE_SYNC_USERNAME": "tester", "NOTE_SYNC_PASSWORD": "secret", "NOTE_SYNC_SECRET": "attachment-test-secret"})
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        login = self.client.post("/api/login", json={"username": "tester", "password": "secret"})
        self.token = login.json()["accessToken"]
        self.store = self.app.state.store
        self.workspace = self.client.post("/api/editor/workspaces", json={"name": "자료 공간"}).json()["workspace"]

    def note(self, content="", title="문서", workspace=None):
        response = self.client.post("/api/editor/notes", json={"workspaceId": (workspace or self.workspace)["id"], "title": title, "content": content})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def upload(self, note, name="사진 파일.png", content=b"test attachment"):
        response = self.client.post(f"/api/editor/notes/{note['note']['id']}/attachments", files={"file": (name, content, "application/octet-stream")})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["attachment"]

    def save(self, note, content):
        response = self.client.put(f"/api/editor/notes/{note['note']['id']}", json={"revision": note["revision"], "title": note["note"]["title"], "content": content})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def library(self):
        response = self.client.get("/api/editor/attachment-library")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def cleanup(self, library, attachments=None):
        selected = attachments if attachments is not None else library["attachments"]
        return self.client.post("/api/editor/attachments/cleanup", json={"serverRevision": library["serverRevision"], "attachmentIds": [item["id"] for item in selected]})

    def test_reference_in_another_workspace_protects_owner_upload(self):
        owner = self.note()
        attachment = self.upload(owner)
        other_workspace = self.client.post("/api/editor/workspaces", json={"name": "다른 공간"}).json()["workspace"]
        reader = self.note("![공유](/" + attachment["markdownPath"] + ")", workspace=other_workspace)
        library = self.library()
        item = library["attachments"][0]
        self.assertTrue(item["referenced"])
        self.assertEqual(item["ownerNote"]["id"], owner["note"]["id"])
        self.assertEqual(item["references"][0]["id"], reader["note"]["id"])
        response = self.cleanup(library)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["error"], "attachment_referenced")
        self.assertEqual(self.client.get(attachment["url"]).content, b"test attachment")

    def test_inline_reference_html_relative_encoded_and_code_mentions_are_retained(self):
        owner = self.note()
        attachment = self.upload(owner, "도표 (최종)&정리.png")
        path = attachment["relativePath"]
        relative = posixpath.relpath(path, posixpath.dirname(owner["note"]["relativePath"]))
        variants = [
            "![제목](<" + path + ">)",
            "[자료][ref]\n\n[ref]: <" + quote(path, safe="/") + '> "제목"',
            '<img src="/' + quote(path, safe="/") + '">',
            '<a href="' + relative.replace("&", "&amp;") + '">파일</a>',
            "![제목](<./" + relative + ">)",
            "![제목](<" + quote(quote(path, safe="/"), safe="/") + ">)",
            "```md\n![코드 안의 경로](<" + path + ">)\n```",
            "[직접 다운로드](" + attachment["url"] + "?download=true)",
            "[절대 다운로드](https://notes.example.test" + attachment["url"] + ")",
            '<a href="' + relative.replace(".attachments/", ".attachments/unused/../").replace("&", "&amp;") + '">파일</a>',
            "![제목](<" + relative.replace(".attachments/", ".attachments/unused/../") + ">)",
        ]
        for content in variants:
            with self.subTest(content=content):
                owner = self.save(owner, content)
                self.assertTrue(self.library()["attachments"][0]["referenced"])

    def test_relative_dot_segments_and_unicode_normalization(self):
        import unicodedata

        owner = self.note()
        attachment = self.upload(owner, "도표.png")
        relative = posixpath.relpath(attachment["relativePath"], posixpath.dirname(owner["note"]["relativePath"]))
        target = relative.replace(".attachments/", ".attachments/unused/../")
        self.save(owner, "![도표](" + quote(unicodedata.normalize("NFD", target), safe="/") + "#preview)")
        self.assertTrue(self.library()["attachments"][0]["referenced"])

    def test_stale_scan_cannot_delete_newly_referenced_attachment(self):
        owner = self.note()
        attachment = self.upload(owner)
        library = self.library()
        self.assertFalse(library["attachments"][0]["referenced"])
        self.save(owner, "![보호](<" + attachment["relativePath"] + ">)")
        response = self.cleanup(library)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["error"], "attachment_snapshot_stale")
        self.assertEqual(self.client.get(attachment["url"]).status_code, 200)

    def test_cleanup_rescans_actual_content_even_without_revision_change(self):
        owner = self.note()
        attachment = self.upload(owner)
        library = self.library()
        state = self.store._load_state()
        path = owner["note"]["relativePath"]
        self.store._file_path(path, state["files"][path]).write_text("[자료](<" + attachment["relativePath"] + ">)")
        response = self.cleanup(library)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["error"], "attachment_referenced")

    def test_unreadable_document_prohibits_cleanup(self):
        owner = self.note()
        attachment = self.upload(owner)
        path = owner["note"]["relativePath"]
        state = self.store._load_state()
        physical = self.store._file_path(path, state["files"][path])
        for corrupt in (b"\xff\xfe", None):
            with self.subTest(corrupt=corrupt):
                if corrupt is None:
                    physical.unlink()
                else:
                    physical.write_bytes(corrupt)
                library = self.library()
                self.assertFalse(library["cleanupAllowed"])
                self.assertEqual(library["scanWarnings"][0]["noteId"], owner["note"]["id"])
                response = self.cleanup(library)
                self.assertEqual(response.status_code, 409, response.text)
                self.assertEqual(response.json()["error"], "reference_scan_incomplete")
                self.assertEqual(self.client.get(attachment["url"]).status_code, 200)

    def test_batch_cleanup_tombstones_files_preserves_note_and_metadata(self):
        owner = self.note()
        first = self.upload(owner, "first.bin", b"first")
        second = self.upload(owner, "second.bin", b"second")
        retained = self.upload(owner, "keep.bin", b"keep")
        owner = self.save(owner, "[보관](" + retained["relativePath"] + ")")
        # Desktop clients may attach extension fields to the metadata database.
        state = self.store._load_state()
        metadata = self.store._read_metadata_body()
        metadata["desktopSetting"] = {"preserve": True}
        metadata["notes"][0]["customColor"] = "indigo"
        self.store.sync({"clientId": "desktop", "baseRevision": state["serverRevision"], "metadata": {"body": metadata, "lastKnownRevision": state["metadata"]["revision"]}}, "tester")
        library = self.library()
        response = self.cleanup(library, [first, second])
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["deletedCount"], 2)
        self.assertEqual(response.json()["deletedBytes"], 11)
        self.assertEqual(self.client.get(first["url"]).status_code, 404)
        self.assertEqual(self.client.get(second["url"]).status_code, 404)
        self.assertEqual(self.client.get(retained["url"]).content, b"keep")
        latest = self.client.get("/api/editor/notes/" + owner["note"]["id"]).json()
        self.assertEqual(latest["content"], owner["content"])
        self.assertEqual(latest["revision"], owner["revision"])
        self.assertEqual(latest["note"]["createdAtMs"], owner["note"]["createdAtMs"])
        self.assertEqual([item["id"] for item in latest["note"]["attachments"]], [retained["id"]])
        current_metadata = self.store._read_metadata_body()
        self.assertEqual(current_metadata["desktopSetting"], {"preserve": True})
        self.assertEqual(current_metadata["notes"][0]["customColor"], "indigo")
        records = {item["relativePath"]: item for item in self.store.manifest()["attachments"]}
        self.assertTrue(records[first["relativePath"]]["deleted"])
        self.assertTrue(records[second["relativePath"]]["deleted"])
        self.assertFalse(records[retained["relativePath"]]["deleted"])

    def test_failure_during_second_delete_rolls_back_whole_batch(self):
        owner = self.note()
        first = self.upload(owner, "first.bin", b"first")
        second = self.upload(owner, "second.bin", b"second")
        library = self.library()
        before = copy.deepcopy(self.store._read_metadata_body())
        original = self.store._commit_file_change
        deleted = []

        def fail_second(relative_path, *args, **kwargs):
            if relative_path in (first["relativePath"], second["relativePath"]):
                deleted.append(relative_path)
                if len(deleted) == 2:
                    raise RuntimeError("Injected second attachment deletion failure")
            return original(relative_path, *args, **kwargs)

        with mock.patch.object(self.store, "_commit_file_change", side_effect=fail_second):
            with TestClient(self.app, raise_server_exceptions=False) as client:
                response = client.post("/api/editor/attachments/cleanup", headers={"Authorization": "Bearer " + self.token}, json={"serverRevision": library["serverRevision"], "attachmentIds": [first["id"], second["id"]]})
        self.assertEqual(response.status_code, 500, response.text)
        self.assertEqual(self.store._read_metadata_body(), before)
        self.assertEqual(self.store.manifest()["serverRevision"], library["serverRevision"])
        self.assertEqual(self.client.get(first["url"]).content, b"first")
        self.assertEqual(self.client.get(second["url"]).content, b"second")

    def test_ownerless_attachment_is_listed_downloadable_and_cleanable(self):
        path = "legacy/.attachments/orphan.bin"
        self.store.sync({"clientId": "desktop", "baseRevision": self.store.manifest()["serverRevision"], "attachments": [{"relativePath": path, "noteRelativePath": "legacy/deleted.md", "lastKnownRevision": 0, "contentEncoding": "base64", "content": base64.b64encode(b"orphan").decode(), "attachment": {"id": "orphan-attachment", "relativePath": path, "fileName": "orphan.bin"}}]}, "tester")
        library = self.library()
        attachment = library["attachments"][0]
        self.assertIsNone(attachment["ownerNote"])
        self.assertEqual(self.client.get(attachment["url"]).content, b"orphan")
        editor_library = self.client.get("/api/editor/library").json()
        self.assertEqual(editor_library["notes"], [])
        self.assertEqual(len(editor_library["attachments"]), 1)
        preview_attachment = editor_library["attachments"][0]
        self.assertEqual(preview_attachment["id"], attachment["id"])
        self.assertEqual(preview_attachment["markdownPath"], quote(path, safe="/"))
        self.assertEqual(self.client.get(preview_attachment["url"]).content, b"orphan")
        response = self.cleanup(library)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.client.get(attachment["url"]).status_code, 404)

    def test_referenced_selection_makes_entire_batch_fail_without_partial_deletion(self):
        owner = self.note()
        first = self.upload(owner, "first.bin")
        second = self.upload(owner, "second.bin")
        self.save(owner, "[보관](" + second["relativePath"] + ")")
        response = self.cleanup(self.library())
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(self.client.get(first["url"]).status_code, 200)
        self.assertEqual(self.client.get(second["url"]).status_code, 200)

    def test_cleanup_requires_authentication_and_valid_unique_ids(self):
        attachment = self.upload(self.note())
        library = self.library()
        for ids in ([], ["missing"], [attachment["id"], attachment["id"]]):
            response = self.client.post("/api/editor/attachments/cleanup", json={"serverRevision": library["serverRevision"], "attachmentIds": ids})
            self.assertEqual(response.status_code, 422, response.text)
        with TestClient(self.app) as anonymous:
            self.assertEqual(anonymous.get("/api/editor/attachment-library").status_code, 401)
            response = anonymous.post("/api/editor/attachments/cleanup", json={"serverRevision": library["serverRevision"], "attachmentIds": [attachment["id"]]})
            self.assertEqual(response.status_code, 401)

    def test_concurrent_cleanup_commits_once(self):
        attachment = self.upload(self.note())
        library = self.library()

        def cleanup(_):
            with TestClient(self.app) as client:
                return client.post("/api/editor/attachments/cleanup", headers={"Authorization": "Bearer " + self.token}, json={"serverRevision": library["serverRevision"], "attachmentIds": [attachment["id"]]}).status_code

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            statuses = list(executor.map(cleanup, range(2)))
        self.assertCountEqual(statuses, [200, 409])
        self.assertEqual(self.library()["attachments"], [])

    def test_thumbnails_require_raster_image_bytes(self):
        owner = self.note()
        svg = self.upload(owner, "misleading.png", b'<svg xmlns="http://www.w3.org/2000/svg"/>')
        png = self.upload(owner, "actual.png", base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a3ioAAAAASUVORK5CYII="))
        items = {item["id"]: item for item in self.library()["attachments"]}
        self.assertFalse(items[svg["id"]]["isImage"])
        self.assertTrue(items[png["id"]]["isImage"])

    def test_creation_timestamp_preserved_on_save_and_legacy_unknown_is_null(self):
        owner = self.note()
        saved = self.save(owner, "수정한 문서")
        self.assertEqual(saved["note"]["createdAtMs"], owner["note"]["createdAtMs"])
        self.store.sync_file_upload({"clientId": "desktop", "baseRevision": self.store.manifest()["serverRevision"], "relativePath": "legacy/note.md", "lastKnownRevision": 0, "contentEncoding": "utf-8", "content": "legacy", "updatedAtMs": 1789000000000}, "tester")
        legacy = next(note for note in self.client.get("/api/editor/library").json()["notes"] if note["relativePath"] == "legacy/note.md")
        self.assertIsNone(legacy["createdAtMs"])

    def test_creation_timestamp_reads_explicit_iso_metadata_without_mtime_fallback(self):
        owner = self.note()
        state = self.store._load_state()
        metadata = self.store._read_metadata_body()
        metadata["notes"][0].pop("createdAtMs")
        metadata["notes"][0]["createdAt"] = "2024-01-01T00:00:00Z"
        self.store.sync({"clientId": "desktop", "baseRevision": state["serverRevision"], "metadata": {"body": metadata, "lastKnownRevision": state["metadata"]["revision"]}}, "tester")
        latest = self.client.get("/api/editor/notes/" + owner["note"]["id"]).json()
        self.assertEqual(latest["note"]["createdAtMs"], 1704067200000)
        saved = self.save(latest, "ISO creation preserved")
        self.assertEqual(saved["note"]["createdAtMs"], 1704067200000)
        self.assertEqual(saved["note"]["createdAt"], "2024-01-01T00:00:00Z")


class AttachmentReferenceSyntaxTest(unittest.TestCase):
    def test_browser_relative_urls_clamp_parent_traversal_at_root(self):
        attachment = {"relativePath": "ws/.attachments/n/att/photo.png", "url": "/api/editor/attachments/att"}
        markdown = "![img](../../../ws/unused/../.attachments/n/att/photo.png)"
        self.assertEqual(_referenced_attachment_paths(markdown, "ws/n.md", [attachment]), {attachment["relativePath"]})

    def test_dot_segments_preserve_escaped_parentheses_and_markdown_delimiters(self):
        for filename in ("photo(final).png", "photo[1].png", "photo 1.png", "photo.png"):
            attachment = {"relativePath": "ws/.attachments/n/att/" + filename, "url": "/api/editor/attachments/att"}
            destination = ".attachments/unused/../n/att/" + filename
            escaped = destination.replace("(", r"\(").replace(")", r"\)").replace("[", r"\[").replace("]", r"\]")
            for markdown in (
                "![img](" + escaped + ")",
                '[ref]: ' + escaped + ' "title"',
                "![img](" + quote(destination, safe="/") + ").",
                "![img](<" + destination + ">)",
            ):
                with self.subTest(markdown=markdown):
                    self.assertEqual(_referenced_attachment_paths(markdown, "ws/n.md", [attachment]), {attachment["relativePath"]})


if __name__ == "__main__":
    unittest.main()
