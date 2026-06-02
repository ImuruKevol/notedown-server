import base64
import binascii
import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath


class SyncError(ValueError):
    pass


COMMIT_RE = re.compile(r"^[0-9a-fA-F]{7,40}$")


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def utc_now_ms():
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def sha256_bytes(value):
    return hashlib.sha256(value).hexdigest()


def sha256_json(value):
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256_bytes(payload)


def atomic_write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.tmp")
    temp_path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temp_path, path)


def atomic_write_bytes(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.tmp")
    temp_path.write_bytes(value)
    os.replace(temp_path, path)


def normalize_relative_path(value):
    if not isinstance(value, str) or not value.strip():
        raise SyncError("relativePath must be a non-empty string.")

    normalized = value.replace("\\", "/")
    if "\x00" in normalized:
        raise SyncError("relativePath contains a null byte.")

    posix_path = PurePosixPath(normalized)
    if posix_path.is_absolute():
        raise SyncError("relativePath must be relative.")

    if any(part in ("", ".", "..") for part in posix_path.parts):
        raise SyncError("relativePath cannot contain empty, '.', or '..' segments.")

    return str(posix_path)


class SyncStore:
    def __init__(self, root):
        self.root = Path(root)
        self.files_root = self.root / "files"
        self.state_path = self.root / "state.json"
        self.metadata_path = self.root / "metadata.json"
        self.lock = threading.Lock()

    def initialize(self):
        self.files_root.mkdir(parents=True, exist_ok=True)
        self._ensure_git_repo(commit_existing=True)
        if not self.state_path.exists():
            now = utc_now()
            atomic_write_json(
                self.state_path,
                {
                    "schemaVersion": 1,
                    "serverRevision": 0,
                    "createdAt": now,
                    "updatedAt": now,
                    "metadata": {
                        "revision": 0,
                        "contentHash": None,
                        "updatedAt": None,
                    },
                    "files": {},
                    "clients": {},
                },
            )

    def manifest(self):
        with self.lock:
            state = self._load_state()
            return self._manifest_from_state(state)

    def file_payload(self, relative_path):
        with self.lock:
            relative_path = normalize_relative_path(relative_path)
            state = self._load_state()
            record = state["files"].get(relative_path)
            if not record or record.get("deleted"):
                raise SyncError("File does not exist on the server.")
            return self._file_change(relative_path, record)

    def file_history(self, relative_path):
        with self.lock:
            relative_path = normalize_relative_path(relative_path)
            commits = self._git_history(relative_path)
            return {
                "relativePath": relative_path,
                "repoPath": str(self.files_root),
                "commits": commits,
            }

    def file_version_payload(self, relative_path, commit):
        with self.lock:
            relative_path = normalize_relative_path(relative_path)
            commit = self._resolve_history_commit(relative_path, commit)
            content = self._git_file_content(relative_path, commit)
            info = self._git_commit_info(commit)
            payload = {
                "relativePath": relative_path,
                "commit": commit,
                "shortCommit": info["shortCommit"],
                "committedAt": info["committedAt"],
                "author": info["author"],
                "message": info["message"],
                "deleted": content is None,
            }
            if content is not None:
                payload.update(
                    {
                        "contentEncoding": "base64",
                        "content": base64.b64encode(content).decode("ascii"),
                        "contentHash": sha256_bytes(content),
                        "size": len(content),
                    }
                )
            return payload

    def rollback_file(self, relative_path, commit, user=None):
        with self.lock:
            relative_path = normalize_relative_path(relative_path)
            commit = self._resolve_history_commit(relative_path, commit)
            content = self._git_file_content(relative_path, commit)
            state = self._load_state()
            current = state["files"].get(relative_path)
            current_revision = current.get("revision", 0) if current else 0
            current_deleted = bool(current and current.get("deleted"))
            current_hash = current.get("contentHash") if current else None
            target_hash = sha256_bytes(content) if content is not None else None

            if content is None:
                if current_deleted:
                    return {
                        "status": "unchanged",
                        "relativePath": relative_path,
                        "revision": current_revision,
                        "deleted": True,
                        "rolledBackToCommit": commit,
                    }
                file_path = self._file_path(relative_path)
                if file_path.exists():
                    file_path.unlink()
            else:
                if not current_deleted and current_hash == target_hash:
                    return {
                        "status": "unchanged",
                        "relativePath": relative_path,
                        "revision": current_revision,
                        "contentHash": target_hash,
                        "deleted": False,
                        "rolledBackToCommit": commit,
                    }
                atomic_write_bytes(self._file_path(relative_path), content)

            now = utc_now()
            state["serverRevision"] += 1
            file_revision = state["serverRevision"]
            message = f"Rollback {relative_path} to {commit[:12]}"
            git_commit = self._commit_file_change(relative_path, message)
            if content is None:
                state["files"][relative_path] = {
                    "revision": file_revision,
                    "contentHash": current_hash,
                    "size": current.get("size", 0) if current else 0,
                    "deleted": True,
                    "serverUpdatedAt": now,
                    "clientUpdatedAtMs": None,
                    "gitCommit": git_commit,
                    "rolledBackToGitCommit": commit,
                }
            else:
                state["files"][relative_path] = {
                    "revision": file_revision,
                    "contentHash": target_hash,
                    "size": len(content),
                    "deleted": False,
                    "serverUpdatedAt": now,
                    "clientUpdatedAtMs": None,
                    "gitCommit": git_commit,
                    "rolledBackToGitCommit": commit,
                }

            metadata_result = self._sync_rollback_metadata(
                state,
                relative_path,
                deleted=content is None,
                now=now,
                updated_at_ms=utc_now_ms(),
            )
            state["updatedAt"] = now
            atomic_write_json(self.state_path, state)
            return {
                "status": "accepted",
                "relativePath": relative_path,
                "revision": file_revision,
                "serverRevision": state["serverRevision"],
                "contentHash": target_hash,
                "deleted": content is None,
                "gitCommit": git_commit,
                "rolledBackToCommit": commit,
                "metadata": metadata_result,
                "manifest": self._manifest_from_state(state),
            }

    def plan_sync(self, payload, user, connection_info=None):
        client_id = payload.get("clientId")
        if not isinstance(client_id, str) or not client_id.strip():
            raise SyncError("clientId is required.")
        client_id = client_id.strip()

        base_revision = self._to_int(payload.get("baseRevision", 0), "baseRevision")
        if "metadata" not in payload:
            raise SyncError("metadata is required for sync planning.")

        metadata_body, metadata_hash, metadata_revision = self._metadata_request_body(
            payload["metadata"],
            base_revision,
        )
        known_files = self._known_files_by_path(
            payload.get("knownFiles", []),
            base_revision,
        )

        with self.lock:
            state = self._load_state()
            now = utc_now()
            server_metadata = self._read_metadata_body() or self._empty_metadata()
            plan = self._build_sync_plan(
                state,
                metadata_body,
                server_metadata,
                known_files,
                base_revision,
            )
            metadata = self._metadata_plan_result(
                state,
                metadata_hash,
                metadata_revision,
                server_metadata,
            )

            conflicts = plan["conflicts"]
            if metadata["status"] == "diverged":
                conflicts = [
                    {
                        "type": "metadata",
                        "relativePath": "metadata.json",
                        "reason": "server_metadata_changed_after_client_base",
                        "serverRevision": metadata["serverRevision"],
                        "clientRevision": metadata_revision,
                    },
                    *conflicts,
                ]
                plan["conflicts"] = conflicts

            self._record_client(
                state,
                client_id,
                user,
                payload,
                connection_info,
                now,
                "plan",
            )
            state["updatedAt"] = now
            atomic_write_json(self.state_path, state)

            return {
                "status": "conflict" if conflicts else "ok",
                "serverRevision": state["serverRevision"],
                "plannedAt": now,
                "metadata": metadata,
                "plan": plan,
                "manifest": self._manifest_from_state(state),
            }

    def sync_file_upload(self, payload, user, connection_info=None):
        client_id = payload.get("clientId")
        if not isinstance(client_id, str) or not client_id.strip():
            raise SyncError("clientId is required.")
        client_id = client_id.strip()

        base_revision = self._to_int(payload.get("baseRevision", 0), "baseRevision")

        with self.lock:
            state = self._load_state()
            now = utc_now()
            file_result = self._sync_file(state, payload, base_revision, now)
            metadata_result = None

            if file_result["status"] != "conflict":
                metadata_result = self._sync_file_metadata(state, payload, now)

            self._record_client(
                state,
                client_id,
                user,
                payload,
                connection_info,
                now,
                "sync_file",
            )
            state["updatedAt"] = now
            atomic_write_json(self.state_path, state)

            return {
                "status": "conflict" if file_result["status"] == "conflict" else "ok",
                "serverRevision": state["serverRevision"],
                "syncedAt": now,
                "file": file_result,
                "metadata": metadata_result,
                "manifest": self._manifest_from_state(state),
            }

    def sync(self, payload, user, connection_info=None):
        client_id = payload.get("clientId")
        if not isinstance(client_id, str) or not client_id.strip():
            raise SyncError("clientId is required.")
        client_id = client_id.strip()

        base_revision = self._to_int(payload.get("baseRevision", 0), "baseRevision")
        files = payload.get("files", [])
        if not isinstance(files, list):
            raise SyncError("files must be a list.")

        with self.lock:
            state = self._load_state()
            accepted = []
            conflicts = []
            accepted_paths = set()
            metadata_result = None
            now = utc_now()

            if "metadata" in payload:
                metadata_result = self._sync_metadata(
                    state,
                    payload["metadata"],
                    base_revision,
                    now,
                )

            for item in files:
                result = self._sync_file(state, item, base_revision, now)
                if result["status"] == "conflict":
                    conflicts.append(result)
                else:
                    accepted.append(result)
                    accepted_paths.add(result["relativePath"])

            self._record_client(
                state,
                client_id,
                user,
                payload,
                connection_info,
                now,
                "sync",
            )
            state["updatedAt"] = now
            atomic_write_json(self.state_path, state)

            remote_changes = self._remote_changes_since(
                state,
                base_revision,
                accepted_paths,
            )

            has_conflict = conflicts or self._metadata_conflicted(metadata_result)
            return {
                "status": "conflict" if has_conflict else "ok",
                "serverRevision": state["serverRevision"],
                "syncedAt": now,
                "metadata": metadata_result,
                "accepted": accepted,
                "conflicts": conflicts,
                "remoteChanges": remote_changes,
                "manifest": self._manifest_from_state(state),
            }

    def _record_client(
        self,
        state,
        client_id,
        user,
        payload,
        connection_info,
        now,
        action,
    ):
        clients = state.setdefault("clients", {})
        current = clients.get(client_id)
        record = dict(current) if isinstance(current, dict) else {}
        client_info = self._clean_mapping(payload.get("clientInfo"))
        connection = self._clean_mapping(connection_info)

        record["lastSeenAt"] = now
        record["lastSeenRevision"] = state["serverRevision"]
        record["lastAction"] = action
        record["user"] = user

        if action == "plan":
            record["lastPlanAt"] = now
        else:
            record["lastSyncAt"] = now

        if client_info is not None:
            record["clientInfo"] = client_info
        else:
            record.setdefault("clientInfo", None)

        if connection is not None:
            record["connectionInfo"] = connection

        clients[client_id] = record

    def _clean_mapping(self, value):
        if not isinstance(value, dict):
            return None

        cleaned = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key.strip():
                continue

            if isinstance(item, dict):
                item = self._clean_mapping(item)
            elif isinstance(item, list):
                item = [entry for entry in item if entry is not None and entry != ""]
            elif item is None or item == "":
                continue

            if item is not None and item != []:
                cleaned[key.strip()] = item

        return cleaned or None

    def _sync_metadata(self, state, metadata_payload, base_revision, now):
        if metadata_payload is None:
            return None

        body, content_hash, last_known_revision = self._metadata_request_body(
            metadata_payload,
            base_revision,
        )

        current = state["metadata"]
        if (
            current.get("revision", 0) > last_known_revision
            and current.get("contentHash") != content_hash
        ):
            return {
                "status": "conflict",
                "revision": current.get("revision", 0),
                "contentHash": current.get("contentHash"),
                "serverMetadata": self._read_metadata_body(),
            }

        if current.get("contentHash") == content_hash:
            return {
                "status": "unchanged",
                "revision": current.get("revision", 0),
                "contentHash": current.get("contentHash"),
            }

        state["serverRevision"] += 1
        atomic_write_json(self.metadata_path, body)
        state["metadata"] = {
            "revision": state["serverRevision"],
            "contentHash": content_hash,
            "updatedAt": now,
        }
        return {
            "status": "accepted",
            "revision": state["serverRevision"],
            "contentHash": content_hash,
        }

    def _sync_file(self, state, item, base_revision, now):
        if not isinstance(item, dict):
            raise SyncError("Each file sync item must be an object.")

        relative_path = normalize_relative_path(item.get("relativePath"))
        deleted = bool(item.get("deleted", False))
        last_known_revision = self._to_int(
            item.get("lastKnownRevision", base_revision),
            f"{relative_path}.lastKnownRevision",
        )
        current = state["files"].get(relative_path)
        current_revision = current.get("revision", 0) if current else 0
        content = b""
        content_hash = None

        if not deleted:
            content = self._decode_content(item, relative_path)
            content_hash = sha256_bytes(content)
            if item.get("contentHash") and item["contentHash"] != content_hash:
                raise SyncError(f"{relative_path}.contentHash does not match content.")

        if (
            current
            and current_revision > last_known_revision
            and not self._same_file_state(current, deleted, content_hash)
        ):
            return {
                "status": "conflict",
                "relativePath": relative_path,
                "clientRevision": last_known_revision,
                "serverRevision": current_revision,
                "serverFile": self._file_change(relative_path, current),
            }

        if deleted:
            if not current or current.get("deleted"):
                return {
                    "status": "unchanged",
                    "relativePath": relative_path,
                    "revision": current_revision,
                    "deleted": True,
                }

            file_path = self._file_path(relative_path)
            if file_path.exists():
                file_path.unlink()
            state["serverRevision"] += 1
            git_commit = self._commit_file_change(
                relative_path,
                f"Delete {relative_path}",
            )
            state["files"][relative_path] = {
                "revision": state["serverRevision"],
                "contentHash": current.get("contentHash"),
                "size": current.get("size", 0),
                "deleted": True,
                "serverUpdatedAt": now,
                "clientUpdatedAtMs": item.get("updatedAtMs"),
                "gitCommit": git_commit,
            }
            return {
                "status": "accepted",
                "relativePath": relative_path,
                "revision": state["serverRevision"],
                "deleted": True,
                "gitCommit": git_commit,
            }

        if current and self._same_file_state(current, deleted, content_hash):
            return {
                "status": "unchanged",
                "relativePath": relative_path,
                "revision": current_revision,
                "contentHash": content_hash,
                "deleted": False,
            }

        atomic_write_bytes(self._file_path(relative_path), content)
        state["serverRevision"] += 1
        git_commit = self._commit_file_change(
            relative_path,
            f"Update {relative_path}",
        )
        state["files"][relative_path] = {
            "revision": state["serverRevision"],
            "contentHash": content_hash,
            "size": len(content),
            "deleted": False,
            "serverUpdatedAt": now,
            "clientUpdatedAtMs": item.get("updatedAtMs"),
            "gitCommit": git_commit,
        }
        return {
            "status": "accepted",
            "relativePath": relative_path,
            "revision": state["serverRevision"],
            "contentHash": content_hash,
            "deleted": False,
            "gitCommit": git_commit,
        }

    def _sync_file_metadata(self, state, payload, now):
        relative_path = normalize_relative_path(payload.get("relativePath"))
        deleted = bool(payload.get("deleted", False))
        note = payload.get("note")
        workspace = payload.get("workspace")

        if note is not None and not isinstance(note, dict):
            raise SyncError("note must be an object.")
        if workspace is not None and not isinstance(workspace, dict):
            raise SyncError("workspace must be an object.")
        if not deleted and note is None and workspace is None:
            return None

        metadata = self._read_metadata_body() or self._empty_metadata()
        changed = False

        if deleted:
            changed = self._remove_note(metadata, relative_path)
        else:
            if workspace:
                changed = self._upsert_workspace(metadata, workspace) or changed
            elif note and note.get("workspace"):
                workspace_from_note = {
                    "id": note.get("workspace"),
                    "name": note.get("workspaceName") or note.get("workspace"),
                }
                changed = self._upsert_workspace(metadata, workspace_from_note) or changed

            if note:
                normalized_note = dict(note)
                normalized_note.setdefault("relativePath", relative_path)
                normalized_note.setdefault("fileName", PurePosixPath(relative_path).name)
                folder = str(PurePosixPath(relative_path).parent)
                if folder != ".":
                    normalized_note.setdefault("folder", folder)
                changed = self._upsert_note(metadata, normalized_note) or changed

        if not changed:
            return {
                "status": "unchanged",
                "revision": state["metadata"].get("revision", 0),
                "contentHash": state["metadata"].get("contentHash"),
            }

        metadata["generatedAt"] = now
        content_hash = sha256_json(metadata)
        state["serverRevision"] += 1
        atomic_write_json(self.metadata_path, metadata)
        state["metadata"] = {
            "revision": state["serverRevision"],
            "contentHash": content_hash,
            "updatedAt": now,
        }
        return {
            "status": "accepted",
            "revision": state["serverRevision"],
            "contentHash": content_hash,
        }

    def _sync_rollback_metadata(
        self,
        state,
        relative_path,
        deleted,
        now,
        updated_at_ms,
    ):
        metadata = self._read_metadata_body() or self._empty_metadata()
        if deleted:
            changed = self._remove_note(metadata, relative_path)
        else:
            changed = self._touch_note(metadata, relative_path, updated_at_ms)

        if not changed:
            return {
                "status": "unchanged",
                "revision": state["metadata"].get("revision", 0),
                "contentHash": state["metadata"].get("contentHash"),
            }

        metadata["generatedAt"] = now
        content_hash = sha256_json(metadata)
        state["serverRevision"] += 1
        atomic_write_json(self.metadata_path, metadata)
        state["metadata"] = {
            "revision": state["serverRevision"],
            "contentHash": content_hash,
            "updatedAt": now,
        }
        return {
            "status": "accepted",
            "revision": state["serverRevision"],
            "contentHash": content_hash,
        }

    def _touch_note(self, metadata, relative_path, updated_at_ms):
        notes = metadata.setdefault("notes", [])
        for note in notes:
            if not isinstance(note, dict):
                continue
            try:
                note_path = normalize_relative_path(note.get("relativePath"))
            except SyncError:
                continue
            if note_path != relative_path:
                continue
            if note.get("updatedAtMs") == updated_at_ms:
                return False
            note["updatedAtMs"] = updated_at_ms
            return True
        return False

    def _metadata_request_body(self, metadata_payload, base_revision):
        if not isinstance(metadata_payload, dict):
            raise SyncError("metadata must be an object.")

        if "body" in metadata_payload:
            body = metadata_payload["body"]
        elif "metadata" in metadata_payload:
            body = metadata_payload["metadata"]
        else:
            body = metadata_payload

        last_known_revision = self._to_int(
            metadata_payload.get("lastKnownRevision", base_revision),
            "metadata.lastKnownRevision",
        )
        content_hash = metadata_payload.get("contentHash") or sha256_json(body)
        computed_hash = sha256_json(body)
        if content_hash != computed_hash:
            raise SyncError("metadata.contentHash does not match metadata body.")

        return body, content_hash, last_known_revision

    def _known_files_by_path(self, known_files, base_revision):
        if known_files is None:
            return {}
        if not isinstance(known_files, list):
            raise SyncError("knownFiles must be a list.")

        by_path = {}
        for item in known_files:
            if not isinstance(item, dict):
                raise SyncError("Each knownFiles item must be an object.")
            relative_path = normalize_relative_path(item.get("relativePath"))
            by_path[relative_path] = {
                "lastKnownRevision": self._to_int(
                    item.get("lastKnownRevision", base_revision),
                    f"{relative_path}.lastKnownRevision",
                ),
                "contentHash": item.get("contentHash"),
                "updatedAtMs": item.get("updatedAtMs"),
            }
        return by_path

    def _build_sync_plan(
        self,
        state,
        client_metadata,
        server_metadata,
        known_files,
        base_revision,
    ):
        client_notes = self._notes_by_path(client_metadata)
        server_notes = self._notes_by_path(server_metadata)
        client_workspaces = self._workspaces_by_id(client_metadata)
        server_workspaces = self._workspaces_by_id(server_metadata)
        paths = sorted(
            set(client_notes)
            | set(server_notes)
            | set(state["files"])
        )
        plan = {
            "uploadFiles": [],
            "downloadFiles": [],
            "deleteServerFiles": [],
            "deleteLocalFiles": [],
            "conflicts": [],
        }

        for relative_path in paths:
            client_note = client_notes.get(relative_path)
            server_note = server_notes.get(relative_path)
            record = state["files"].get(relative_path)
            known = known_files.get(relative_path, {})
            last_known_revision = known.get("lastKnownRevision", base_revision)
            server_revision = record.get("revision", 0) if record else 0
            server_deleted = bool(record and record.get("deleted"))

            if not client_note and not server_note:
                if server_deleted:
                    if server_revision > last_known_revision:
                        plan["deleteLocalFiles"].append(
                            self._delete_plan_item(
                                relative_path,
                                "server_deleted_after_client_base",
                                record,
                            )
                        )
                elif record:
                    if server_revision > last_known_revision or base_revision == 0:
                        plan["downloadFiles"].append(
                            self._download_plan_item(
                                relative_path,
                                "server_file_without_metadata",
                                None,
                                record,
                                server_workspaces,
                            )
                        )
                    else:
                        plan["deleteServerFiles"].append(
                            self._delete_plan_item(
                                relative_path,
                                "missing_in_client_metadata",
                                record,
                            )
                        )
                continue

            if client_note and not server_note:
                if record and not server_deleted and server_revision > last_known_revision:
                    plan["conflicts"].append(
                        self._conflict_plan_item(
                            relative_path,
                            "server_file_exists_without_metadata",
                            client_note,
                            None,
                            record,
                            client_workspaces,
                            server_workspaces,
                        )
                    )
                elif server_deleted and server_revision > last_known_revision:
                    plan["conflicts"].append(
                        self._conflict_plan_item(
                            relative_path,
                            "server_deleted_after_client_base",
                            client_note,
                            None,
                            record,
                            client_workspaces,
                            server_workspaces,
                        )
                    )
                else:
                    plan["uploadFiles"].append(
                        self._upload_plan_item(
                            relative_path,
                            "missing_on_server",
                            client_note,
                            record,
                            client_workspaces,
                        )
                    )
                continue

            if server_note and not client_note:
                if server_deleted:
                    if server_revision > last_known_revision:
                        plan["deleteLocalFiles"].append(
                            self._delete_plan_item(
                                relative_path,
                                "server_deleted_after_client_base",
                                record,
                            )
                        )
                    continue

                if server_revision > last_known_revision or base_revision == 0:
                    plan["downloadFiles"].append(
                        self._download_plan_item(
                            relative_path,
                            "missing_on_client",
                            server_note,
                            record,
                            server_workspaces,
                        )
                    )
                else:
                    plan["deleteServerFiles"].append(
                        self._delete_plan_item(
                            relative_path,
                            "missing_in_client_metadata",
                            record,
                            server_note,
                        )
                    )
                continue

            client_updated = self._note_updated_ms(client_note)
            server_updated = self._note_updated_ms(server_note)
            if server_updated is None and record:
                server_updated = record.get("clientUpdatedAtMs")

            if server_deleted:
                if server_revision > last_known_revision:
                    plan["conflicts"].append(
                        self._conflict_plan_item(
                            relative_path,
                            "server_deleted_after_client_base",
                            client_note,
                            server_note,
                            record,
                            client_workspaces,
                            server_workspaces,
                        )
                    )
                else:
                    plan["uploadFiles"].append(
                        self._upload_plan_item(
                            relative_path,
                            "client_has_file_after_server_delete",
                            client_note,
                            record,
                            client_workspaces,
                        )
                    )
                continue

            if client_updated is not None and server_updated is not None:
                if client_updated > server_updated:
                    if server_revision > last_known_revision:
                        plan["conflicts"].append(
                            self._conflict_plan_item(
                                relative_path,
                                "both_sides_changed",
                                client_note,
                                server_note,
                                record,
                                client_workspaces,
                                server_workspaces,
                            )
                        )
                    else:
                        plan["uploadFiles"].append(
                            self._upload_plan_item(
                                relative_path,
                                "client_newer",
                                client_note,
                                record,
                                client_workspaces,
                            )
                        )
                    continue

                if server_updated > client_updated:
                    plan["downloadFiles"].append(
                        self._download_plan_item(
                            relative_path,
                            "server_newer",
                            server_note,
                            record,
                            server_workspaces,
                        )
                    )
                    continue

            if (
                record
                and server_revision > last_known_revision
                and self._known_file_changed(record, known)
            ):
                plan["downloadFiles"].append(
                    self._download_plan_item(
                        relative_path,
                        "server_file_changed",
                        server_note,
                        record,
                        server_workspaces,
                    )
                )
                continue

            if not record:
                plan["uploadFiles"].append(
                    self._upload_plan_item(
                        relative_path,
                        "missing_server_file",
                        client_note,
                        record,
                        client_workspaces,
                    )
                )

        return plan

    def _known_file_changed(self, record, known):
        known_hash = known.get("contentHash")
        if known_hash:
            return record.get("contentHash") != known_hash
        return True

    def _metadata_plan_result(
        self,
        state,
        client_hash,
        client_revision,
        server_metadata,
    ):
        server_revision = state["metadata"].get("revision", 0)
        server_hash = state["metadata"].get("contentHash")
        if server_hash == client_hash:
            status = "same"
        elif server_revision == 0:
            status = "server_empty"
        elif server_revision > client_revision:
            status = "diverged"
        else:
            status = "client_changed"

        result = {
            "status": status,
            "clientRevision": client_revision,
            "serverRevision": server_revision,
            "clientHash": client_hash,
            "serverHash": server_hash,
        }
        if status in ("diverged", "client_changed"):
            result["serverMetadata"] = server_metadata
        return result

    def _notes_by_path(self, metadata):
        notes = metadata.get("notes", []) if isinstance(metadata, dict) else []
        if not isinstance(notes, list):
            raise SyncError("metadata.notes must be a list.")

        by_path = {}
        for note in notes:
            if not isinstance(note, dict):
                raise SyncError("Each metadata note must be an object.")
            relative_path = note.get("relativePath")
            if not relative_path:
                continue
            by_path[normalize_relative_path(relative_path)] = note
        return by_path

    def _workspaces_by_id(self, metadata):
        workspaces = metadata.get("workspaces", []) if isinstance(metadata, dict) else []
        if not isinstance(workspaces, list):
            raise SyncError("metadata.workspaces must be a list.")

        by_id = {}
        for workspace in workspaces:
            if not isinstance(workspace, dict):
                raise SyncError("Each metadata workspace must be an object.")
            workspace_id = workspace.get("id")
            if workspace_id:
                by_id[workspace_id] = workspace
        return by_id

    def _workspace_for_note(self, note, workspaces):
        if not note:
            return None
        workspace_id = note.get("workspace")
        return workspaces.get(workspace_id) if workspace_id else None

    def _note_updated_ms(self, note):
        if not note:
            return None
        value = note.get("updatedAtMs")
        if value is None:
            return None
        return self._to_int(value, "note.updatedAtMs")

    def _upload_plan_item(self, relative_path, reason, note, record, workspaces):
        return {
            "relativePath": relative_path,
            "reason": reason,
            "note": note,
            "workspace": self._workspace_for_note(note, workspaces),
            "serverFile": self._file_record(relative_path, record) if record else None,
        }

    def _download_plan_item(self, relative_path, reason, note, record, workspaces):
        return {
            "relativePath": relative_path,
            "reason": reason,
            "note": note,
            "workspace": self._workspace_for_note(note, workspaces),
            "serverFile": self._file_record(relative_path, record) if record else None,
        }

    def _delete_plan_item(self, relative_path, reason, record, note=None):
        return {
            "relativePath": relative_path,
            "reason": reason,
            "note": note,
            "serverFile": self._file_record(relative_path, record) if record else None,
        }

    def _conflict_plan_item(
        self,
        relative_path,
        reason,
        client_note,
        server_note,
        record,
        client_workspaces,
        server_workspaces,
    ):
        return {
            "relativePath": relative_path,
            "reason": reason,
            "clientNote": client_note,
            "clientWorkspace": self._workspace_for_note(client_note, client_workspaces),
            "serverNote": server_note,
            "serverWorkspace": self._workspace_for_note(server_note, server_workspaces),
            "serverFile": self._file_record(relative_path, record) if record else None,
        }

    def _file_record(self, relative_path, record):
        if not record:
            return None
        return {
            "relativePath": relative_path,
            "revision": record.get("revision", 0),
            "contentHash": record.get("contentHash"),
            "size": record.get("size", 0),
            "deleted": bool(record.get("deleted")),
            "serverUpdatedAt": record.get("serverUpdatedAt"),
            "clientUpdatedAtMs": record.get("clientUpdatedAtMs"),
            "gitCommit": record.get("gitCommit"),
            "rolledBackToGitCommit": record.get("rolledBackToGitCommit"),
        }

    def _empty_metadata(self):
        return {
            "version": 1,
            "generatedAt": None,
            "workspaces": [],
            "notes": [],
        }

    def _upsert_workspace(self, metadata, workspace):
        workspace_id = workspace.get("id")
        if not workspace_id:
            raise SyncError("workspace.id is required.")

        workspaces = metadata.setdefault("workspaces", [])
        for index, current in enumerate(workspaces):
            if current.get("id") == workspace_id:
                if current == workspace:
                    return False
                workspaces[index] = dict(workspace)
                return True

        workspaces.append(dict(workspace))
        return True

    def _upsert_note(self, metadata, note):
        relative_path = normalize_relative_path(note.get("relativePath"))
        notes = metadata.setdefault("notes", [])
        for index, current in enumerate(notes):
            same_path = current.get("relativePath") == relative_path
            same_id = note.get("id") and current.get("id") == note.get("id")
            if same_path or same_id:
                if current == note:
                    return False
                notes[index] = dict(note)
                return True

        notes.append(dict(note))
        return True

    def _remove_note(self, metadata, relative_path):
        notes = metadata.setdefault("notes", [])
        next_notes = [
            note
            for note in notes
            if note.get("relativePath") != relative_path
        ]
        if len(next_notes) == len(notes):
            return False
        metadata["notes"] = next_notes
        return True

    def _same_file_state(self, record, deleted, content_hash):
        if deleted:
            return bool(record.get("deleted"))
        return not record.get("deleted") and record.get("contentHash") == content_hash

    def _remote_changes_since(self, state, base_revision, accepted_paths):
        changes = []
        for relative_path, record in sorted(state["files"].items()):
            if record.get("revision", 0) <= base_revision:
                continue
            if relative_path in accepted_paths:
                continue
            changes.append(self._file_change(relative_path, record))
        return changes

    def _file_change(self, relative_path, record):
        payload = {
            "relativePath": relative_path,
            "revision": record.get("revision", 0),
            "contentHash": record.get("contentHash"),
            "size": record.get("size", 0),
            "deleted": bool(record.get("deleted")),
            "serverUpdatedAt": record.get("serverUpdatedAt"),
            "clientUpdatedAtMs": record.get("clientUpdatedAtMs"),
            "gitCommit": record.get("gitCommit"),
            "rolledBackToGitCommit": record.get("rolledBackToGitCommit"),
        }

        if not payload["deleted"]:
            payload["contentEncoding"] = "base64"
            payload["content"] = base64.b64encode(
                self._file_path(relative_path).read_bytes()
            ).decode("ascii")

        return payload

    def _manifest_from_state(self, state):
        return {
            "schemaVersion": state["schemaVersion"],
            "serverRevision": state["serverRevision"],
            "updatedAt": state["updatedAt"],
            "metadata": state["metadata"],
            "files": [
                {"relativePath": relative_path, **record}
                for relative_path, record in sorted(state["files"].items())
            ],
            "clients": state["clients"],
        }

    def _ensure_git_repo(self, commit_existing=False):
        if shutil.which("git") is None:
            raise SyncError("git executable is required for file history.")

        if not (self.files_root / ".git").exists():
            self._run_git("init")

        self._run_git("config", "user.name", "Notedown Sync Server")
        self._run_git("config", "user.email", "notedown-sync@local")
        self._run_git("config", "core.quotePath", "false")
        if commit_existing:
            self._commit_existing_files_if_needed()

    def _commit_existing_files_if_needed(self):
        if self._has_git_head():
            return

        has_files = any(
            path.is_file() and ".git" not in path.relative_to(self.files_root).parts
            for path in self.files_root.rglob("*")
        )
        if not has_files:
            return

        self._run_git("add", "-A", "--", ".")
        if self._staged_changes_exist():
            self._run_git("commit", "-m", "Initialize Notedown file history")

    def _commit_file_change(self, relative_path, message):
        relative_path = normalize_relative_path(relative_path)
        self._ensure_git_repo()
        self._run_git("add", "-A", "--", relative_path)
        if self._staged_changes_exist():
            self._run_git("commit", "-m", message)
        return self._git_head()

    def _git_history(self, relative_path):
        relative_path = normalize_relative_path(relative_path)
        self._ensure_git_repo(commit_existing=True)
        if not self._has_git_head():
            return []

        output = self._run_git(
            "log",
            "--date=iso-strict",
            "--pretty=format:%H%x1f%h%x1f%aI%x1f%an%x1f%s",
            "--",
            relative_path,
        ).stdout.strip()
        if not output:
            return []

        commits = []
        for line in output.splitlines():
            parts = line.split("\x1f", 4)
            if len(parts) != 5:
                continue
            commit, short_commit, committed_at, author, message = parts
            content = self._git_file_content(relative_path, commit)
            commits.append(
                {
                    "commit": commit,
                    "shortCommit": short_commit,
                    "committedAt": committed_at,
                    "author": author,
                    "message": message,
                    "deleted": content is None,
                    "contentHash": sha256_bytes(content) if content is not None else None,
                    "size": len(content) if content is not None else 0,
                }
            )
        return commits

    def _resolve_history_commit(self, relative_path, commit):
        relative_path = normalize_relative_path(relative_path)
        if not isinstance(commit, str) or not COMMIT_RE.fullmatch(commit.strip()):
            raise SyncError("commit must be a git commit hash.")

        commit = commit.strip()
        resolved = self._run_git(
            "rev-parse",
            "--verify",
            f"{commit}^{{commit}}",
        ).stdout.strip()

        history_commits = {
            item["commit"]
            for item in self._git_history(relative_path)
        }
        if resolved not in history_commits:
            raise SyncError("commit is not part of the file history.")
        return resolved

    def _git_commit_info(self, commit):
        output = self._run_git(
            "show",
            "-s",
            "--date=iso-strict",
            "--pretty=format:%H%x1f%h%x1f%aI%x1f%an%x1f%s",
            commit,
        ).stdout.strip()
        parts = output.split("\x1f", 4)
        if len(parts) != 5:
            raise SyncError("Unable to read git commit information.")
        full_commit, short_commit, committed_at, author, message = parts
        return {
            "commit": full_commit,
            "shortCommit": short_commit,
            "committedAt": committed_at,
            "author": author,
            "message": message,
        }

    def _git_file_content(self, relative_path, commit):
        relative_path = normalize_relative_path(relative_path)
        result = self._run_git(
            "cat-file",
            "-e",
            f"{commit}:{relative_path}",
            check=False,
        )
        if result.returncode != 0:
            return None
        return self._run_git_bytes("show", f"{commit}:{relative_path}").stdout

    def _has_git_head(self):
        result = self._run_git(
            "rev-parse",
            "--verify",
            "HEAD",
            check=False,
        )
        return result.returncode == 0

    def _git_head(self):
        if not self._has_git_head():
            return None
        return self._run_git("rev-parse", "HEAD").stdout.strip()

    def _staged_changes_exist(self):
        result = self._run_git("diff", "--cached", "--quiet", check=False)
        if result.returncode not in (0, 1):
            message = result.stderr.strip() or result.stdout.strip() or "git diff failed"
            raise SyncError(message)
        return result.returncode == 1

    def _run_git(self, *args, check=True):
        try:
            result = subprocess.run(
                ["git", *args],
                cwd=self.files_root,
                check=False,
                capture_output=True,
                text=True,
            )
        except OSError as exc:
            raise SyncError("Unable to execute git.") from exc

        if check and result.returncode != 0:
            message = result.stderr.strip() or result.stdout.strip() or "git command failed"
            raise SyncError(message)
        return result

    def _run_git_bytes(self, *args, check=True):
        try:
            result = subprocess.run(
                ["git", *args],
                cwd=self.files_root,
                check=False,
                capture_output=True,
            )
        except OSError as exc:
            raise SyncError("Unable to execute git.") from exc

        if check and result.returncode != 0:
            message = (
                result.stderr.decode("utf-8", errors="replace").strip()
                or result.stdout.decode("utf-8", errors="replace").strip()
                or "git command failed"
            )
            raise SyncError(message)
        return result

    def _load_state(self):
        self.initialize()
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    def _read_metadata_body(self):
        if not self.metadata_path.exists():
            return None
        return json.loads(self.metadata_path.read_text(encoding="utf-8"))

    def _decode_content(self, item, relative_path):
        if "content" not in item:
            raise SyncError(f"{relative_path}.content is required unless deleted is true.")

        encoding = item.get("contentEncoding", "base64")
        content = item["content"]
        if encoding == "base64":
            if not isinstance(content, str):
                raise SyncError(f"{relative_path}.content must be a base64 string.")
            try:
                return base64.b64decode(content.encode("ascii"), validate=True)
            except (binascii.Error, UnicodeEncodeError) as exc:
                raise SyncError(f"{relative_path}.content is invalid base64.") from exc

        if encoding == "utf-8":
            if not isinstance(content, str):
                raise SyncError(f"{relative_path}.content must be a string.")
            return content.encode("utf-8")

        raise SyncError(f"{relative_path}.contentEncoding is unsupported.")

    def _file_path(self, relative_path):
        relative_path = normalize_relative_path(relative_path)
        target = (self.files_root / relative_path).resolve()
        files_root = self.files_root.resolve()
        if files_root not in target.parents and target != files_root:
            raise SyncError("Resolved file path escapes the storage directory.")
        return target

    def _to_int(self, value, field_name):
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise SyncError(f"{field_name} must be an integer.") from exc

    def _metadata_conflicted(self, metadata_result):
        return metadata_result is not None and metadata_result.get("status") == "conflict"
