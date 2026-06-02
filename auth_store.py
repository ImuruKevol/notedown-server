import hmac
import json
from pathlib import Path

from werkzeug.security import check_password_hash, generate_password_hash

from sync_store import atomic_write_json


class AuthStore:
    def __init__(self, config):
        self.config = config
        auth_file = config.get("NOTE_SYNC_AUTH_FILE")
        self.auth_file = Path(auth_file) if auth_file else Path(config["NOTE_SYNC_STORAGE"]) / "auth.json"

    def is_configured(self):
        return self.source() != "none"

    def source(self):
        if self.config.get("NOTE_SYNC_USERNAME") and (
            self.config.get("NOTE_SYNC_PASSWORD")
            or self.config.get("NOTE_SYNC_PASSWORD_HASH")
        ):
            return "environment"
        if self.auth_file.exists():
            return "file"
        return "none"

    def verify(self, username, password):
        source = self.source()
        if source == "environment":
            return self._verify_environment(username, password)
        if source == "file":
            return self._verify_file(username, password)
        return False

    def current_username(self):
        source = self.source()
        if source == "environment":
            return self.config.get("NOTE_SYNC_USERNAME")
        if source == "file":
            return self._load_file().get("username")
        return None

    def can_update_credentials(self):
        return self.source() == "file"

    def setup(self, username, password):
        if self.is_configured():
            raise ValueError("auth_already_configured")

        payload = {
            "username": self._normalize_username(username),
            "passwordHash": self._hash_password(password),
        }
        atomic_write_json(self.auth_file, payload)
        return payload["username"]

    def update_credentials(self, current_password, username, password):
        if self.source() != "file":
            raise ValueError("auth_update_not_supported")

        current = self._load_file()
        current_username = current.get("username", "")
        if not self.verify(current_username, current_password):
            raise ValueError("invalid_current_password")

        payload = {
            "username": self._normalize_username(username),
            "passwordHash": self._hash_password(password),
        }
        atomic_write_json(self.auth_file, payload)
        return payload["username"]

    def _verify_environment(self, username, password):
        expected_username = self.config.get("NOTE_SYNC_USERNAME")
        if not hmac.compare_digest(str(username), str(expected_username)):
            return False

        password_hash = self.config.get("NOTE_SYNC_PASSWORD_HASH")
        if password_hash:
            return check_password_hash(password_hash, str(password))

        return hmac.compare_digest(str(password), str(self.config.get("NOTE_SYNC_PASSWORD")))

    def _verify_file(self, username, password):
        payload = self._load_file()
        expected_username = payload.get("username", "")
        password_hash = payload.get("passwordHash", "")
        if not hmac.compare_digest(str(username), str(expected_username)):
            return False
        return check_password_hash(password_hash, str(password))

    def _load_file(self):
        return json.loads(self.auth_file.read_text(encoding="utf-8"))

    def _normalize_username(self, username):
        if not isinstance(username, str) or not username.strip():
            raise ValueError("username_required")
        return username.strip()

    def _hash_password(self, password):
        if not isinstance(password, str) or len(password) < 8:
            raise ValueError("password_too_short")
        return generate_password_hash(password, method="pbkdf2:sha256")
