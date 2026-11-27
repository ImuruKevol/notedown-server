"""PWA assets remain public without weakening document authentication."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from fastapi.testclient import TestClient

import app as app_module


class PwaRoutesTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.dist = self.root / "dist"
        self.public = self.root / "public"
        self.dist.mkdir()
        (self.public / "icons").mkdir(parents=True)
        self.assets = {
            "manifest.webmanifest": (json.dumps({"name": "Notedown", "start_url": "/admin", "scope": "/", "display": "standalone"}).encode(), "application/manifest+json"),
            "sw.js": (b"self.addEventListener('fetch', () => {});", "text/javascript"),
            "offline.html": (b"<!doctype html><title>Notedown offline</title>", "text/html"),
            "favicon.ico": (b"\x00\x00\x01\x00", "image/vnd.microsoft.icon"),
            "icons/icon-192.png": (b"\x89PNG\r\n\x1a\n", "image/png"),
            "icons/favicon.svg": (b'<svg xmlns="http://www.w3.org/2000/svg"/>', "image/svg+xml"),
            "icons/notedown.svg": (b'<svg xmlns="http://www.w3.org/2000/svg"/>', "image/svg+xml"),
        }
        for name, (content, _) in self.assets.items():
            (self.public / name).write_bytes(content)
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(app_module, "FRONTEND_DIR", self.dist).start()
        mock.patch.object(app_module, "FRONTEND_PUBLIC_DIR", self.public).start()
        self.config = {
            "NOTE_SYNC_STORAGE": str(self.root / "storage"),
            "NOTE_SYNC_USERNAME": "tester",
            "NOTE_SYNC_PASSWORD": "test-password",
            "NOTE_SYNC_SECRET": "pwa-route-test-secret",
        }
        self.app = app_module.create_app(self.config)
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)

    def test_anonymous_assets_use_correct_mime_without_redirects(self):
        for name, (content, media_type) in self.assets.items():
            with self.subTest(asset=name):
                response = self.client.get("/" + name, follow_redirects=False)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.content, content)
                self.assertEqual(response.headers["content-type"].split(";")[0], media_type)
                self.assertEqual(response.headers["cache-control"], "no-cache")
                self.assertEqual(response.headers["x-content-type-options"], "nosniff")
                self.assertNotIn("location", response.headers)
                self.assertNotIn("set-cookie", response.headers)

    def test_worker_scope_and_revalidation_headers_support_updates(self):
        response = self.client.get("/sw.js", headers={"Service-Worker": "script"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["service-worker-allowed"], "/")
        self.assertEqual(response.headers["cache-control"], "no-cache")
        self.assertNotIn("service-worker-allowed", self.client.get("/manifest.webmanifest").headers)

    def test_head_has_same_headers_without_asset_body(self):
        for name in self.assets:
            with self.subTest(asset=name):
                response = self.client.head("/" + name)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.content, b"")
                self.assertEqual(int(response.headers["content-length"]), len(self.assets[name][0]))

    def test_built_assets_take_precedence_and_missing_build_assets_fall_back(self):
        built_manifest = b'{"name":"Built Notedown"}'
        (self.dist / "manifest.webmanifest").write_bytes(built_manifest)
        (self.dist / "icons").mkdir()
        (self.dist / "icons" / "icon-192.png").write_bytes(b"built png")
        self.assertEqual(self.client.get("/manifest.webmanifest").content, built_manifest)
        self.assertEqual(self.client.get("/icons/icon-192.png").content, b"built png")
        self.assertEqual(self.client.get("/sw.js").content, self.assets["sw.js"][0])
        self.assertEqual(self.client.get("/icons/favicon.svg").content, self.assets["icons/favicon.svg"][0])

    def test_unknown_assets_and_traversal_do_not_expose_files(self):
        marker = b"private document must stay private"
        (self.public / "private.png").write_bytes(marker)
        (self.root / "outside.png").write_bytes(marker)
        for path in [
            "/icons/missing.png",
            "/icons/manifest.webmanifest",
            "/icons/%2e%2e/private.png",
            "/icons/..%2fprivate.png",
            "/icons/..%5cprivate.png",
            "/icons/%00.png",
            "/icons/nested/image.png",
            "/icons/" + "x" * 256 + ".png",
            "/sw.js/../private.png",
        ]:
            with self.subTest(path=path):
                response = self.client.get(path, follow_redirects=False)
                self.assertEqual(response.status_code, 404)
                self.assertNotIn(marker, response.content)
        (self.public / "sw.js").unlink()
        self.assertEqual(self.client.get("/sw.js").status_code, 404)

    def test_public_asset_symlinks_cannot_escape_public_directories(self):
        outside = self.root / "outside.png"
        outside.write_bytes(b"private marker")
        (self.public / "icons" / "escape.png").symlink_to(outside)
        (self.public / "sw.js").unlink()
        (self.public / "sw.js").symlink_to(outside)
        self.assertEqual(self.client.get("/icons/escape.png").status_code, 404)
        self.assertEqual(self.client.get("/sw.js").status_code, 404)

    def test_public_routes_preserve_login_and_api_authentication(self):
        index = '<!doctype html><link rel="manifest" href="/manifest.webmanifest"><link rel="icon" href="/icons/favicon.svg">'
        (self.dist / "index.html").write_text(index)
        for path in ["/", "/admin", "/editor"]:
            with self.subTest(path=path):
                response = self.client.get(path, follow_redirects=False)
                self.assertEqual(response.status_code, 302)
                self.assertEqual(response.headers["location"], "/login")
        response = self.client.get("/login")
        self.assertEqual(response.text, index)
        self.assertEqual(response.headers["cache-control"], "no-cache")
        self.assertEqual(self.client.get("/api/editor/library").status_code, 401)
        self.assertEqual(self.client.get("/api/manifest").status_code, 401)
        self.assertEqual(self.client.get("/api/health").status_code, 200)
        login = self.client.post("/api/login", json={"username": "tester", "password": "test-password"})
        self.assertEqual(login.status_code, 200)
        for path in ["/admin", "/editor", "/login"]:
            with self.subTest(authenticated_path=path):
                self.assertEqual(self.client.get(path).text, index)
        self.assertEqual(self.client.get("/sw.js").status_code, 200)

    def test_setup_and_assets_work_before_account_is_configured(self):
        index = '<!doctype html><link rel="manifest" href="/manifest.webmanifest">'
        (self.dist / "index.html").write_text(index)
        config = {**self.config, "NOTE_SYNC_USERNAME": None, "NOTE_SYNC_PASSWORD": None,
                  "NOTE_SYNC_PASSWORD_HASH": None, "NOTE_SYNC_AUTH_FILE": str(self.root / "not-configured.json")}
        with TestClient(app_module.create_app(config)) as client:
            response = client.get("/", follow_redirects=False)
            self.assertEqual(response.headers["location"], "/setup")
            self.assertEqual(client.get("/setup").text, index)
            for name in self.assets:
                with self.subTest(asset=name):
                    self.assertEqual(client.get("/" + name).status_code, 200)


if __name__ == "__main__":
    unittest.main()
