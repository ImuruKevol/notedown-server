import json
import secrets
import threading
from pathlib import Path

from sync_store import atomic_write_json, utc_now


class TokenStore:
    def __init__(self, root):
        self.root = Path(root)
        self.tokens_path = self.root / "tokens.json"
        self.lock = threading.Lock()

    def initialize(self):
        if not self.tokens_path.exists():
            atomic_write_json(
                self.tokens_path,
                {
                    "schemaVersion": 1,
                    "tokens": {},
                },
            )

    def issue(self, username, connection_info=None):
        with self.lock:
            state = self._load()
            now = utc_now()
            token_id = secrets.token_urlsafe(18)
            record = {
                "id": token_id,
                "username": username,
                "issuedAt": now,
                "lastUsedAt": None,
            }
            connection = self._clean_mapping(connection_info)
            if connection is not None:
                record["connectionInfo"] = connection

            state.setdefault("tokens", {})[token_id] = record
            atomic_write_json(self.tokens_path, state)
            return dict(record)

    def list_tokens(self):
        with self.lock:
            state = self._load()
            tokens = [
                dict(record)
                for record in state.get("tokens", {}).values()
                if isinstance(record, dict)
            ]
        return sorted(tokens, key=lambda item: item.get("issuedAt") or "", reverse=True)

    def touch(self, token_id, connection_info=None):
        if not isinstance(token_id, str) or not token_id:
            return None

        with self.lock:
            state = self._load()
            tokens = state.setdefault("tokens", {})
            record = tokens.get(token_id)
            if not isinstance(record, dict):
                return None

            record["lastUsedAt"] = utc_now()
            connection = self._clean_mapping(connection_info)
            if connection is not None:
                record["connectionInfo"] = connection

            atomic_write_json(self.tokens_path, state)
            return dict(record)

    def delete(self, token_id):
        with self.lock:
            state = self._load()
            tokens = state.setdefault("tokens", {})
            record = tokens.pop(token_id, None)
            if record is None:
                return None

            atomic_write_json(self.tokens_path, state)
            return dict(record) if isinstance(record, dict) else {"id": token_id}

    def _load(self):
        self.initialize()
        return json.loads(self.tokens_path.read_text(encoding="utf-8"))

    def _clean_mapping(self, value):
        if not isinstance(value, dict):
            return None

        cleaned = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key.strip():
                continue

            if isinstance(item, dict):
                item = self._clean_mapping(item)
            elif item is None or item == "":
                continue

            if item is not None:
                cleaned[key.strip()] = item

        return cleaned or None
