import base64
import binascii
import copy
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import threading
import unicodedata
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath


class SyncError(ValueError):
    pass


COMMIT_RE = re.compile(r"^[0-9a-fA-F]{7,40}$")
CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
UNSAFE_FILENAME_RE = re.compile(r'[<>:"\\|?*]+')
MULTISPACE_RE = re.compile(r"\s+")
MAX_STORAGE_SEGMENT_LENGTH = 120
METADATA_DB_SCHEMA_VERSION = 1


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


def compact_json(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def metadata_json(value):
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def atomic_write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.tmp")
    temp_path.write_text(metadata_json(value), encoding="utf-8")
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


def safe_storage_segment(value, fallback):
    text = str(value or "").strip()
    if not text:
        text = fallback

    text = unicodedata.normalize("NFC", text)
    text = text.replace("/", " ").replace("\\", " ")
    text = CONTROL_RE.sub(" ", text)
    text = UNSAFE_FILENAME_RE.sub(" ", text)
    text = MULTISPACE_RE.sub(" ", text).strip(" .")
    if not text or text in (".", ".."):
        text = fallback

    if len(text) > MAX_STORAGE_SEGMENT_LENGTH:
        text = text[:MAX_STORAGE_SEGMENT_LENGTH].rstrip(" .")

    return text or fallback


def safe_storage_file_name(name, fallback, default_suffix=""):
    source = str(name or "").strip()
    suffix = PurePosixPath(source).suffix
    stem = source[: -len(suffix)] if suffix else source
    if not suffix and default_suffix:
        suffix = default_suffix

    safe_stem = safe_storage_segment(stem, fallback)
    safe_suffix = safe_storage_segment(suffix, default_suffix).replace(" ", "")
    if safe_suffix and not safe_suffix.startswith("."):
        safe_suffix = f".{safe_suffix}"
    return f"{safe_stem}{safe_suffix}"


class SyncStore:
    def __init__(self, root):
        self.root = Path(root)
        self.files_root = self.root / "files"
        self.state_path = self.root / "state.json"
        self.metadata_db_path = self.root / "metadata.db"
        self.lock = threading.Lock()

    def initialize(self):
        self.root.mkdir(parents=True, exist_ok=True)
        self.files_root.mkdir(parents=True, exist_ok=True)
        self._ensure_git_repo(commit_existing=True)
        self._ensure_metadata_store()
        if not self.state_path.exists():
            now = utc_now()
            atomic_write_json(self.state_path, self._initial_state(now))

    def reset_repository(self, user=None):
        with self.lock:
            now = utc_now()
            if self.files_root.exists():
                shutil.rmtree(self.files_root)
            for path in (
                self.metadata_db_path,
                self.metadata_db_path.with_name(f"{self.metadata_db_path.name}-wal"),
                self.metadata_db_path.with_name(f"{self.metadata_db_path.name}-shm"),
            ):
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass

            self.files_root.mkdir(parents=True, exist_ok=True)
            self._ensure_git_repo(commit_existing=False)
            self._ensure_metadata_store()
            state = self._initial_state(now)
            if user:
                state["resetBy"] = user
            state["resetAt"] = now
            atomic_write_json(self.state_path, state)
            return {
                "status": "reset",
                "resetAt": now,
                "resetBy": user,
                "manifest": self._manifest_from_state(state),
            }

    def _initial_state(self, now):
        return {
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
        }

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
            if self._is_attachment_record(record):
                raise SyncError("Use the attachment endpoint for note attachments.")
            return self._file_change(relative_path, record)

    def attachment_payload(self, relative_path):
        with self.lock:
            relative_path = normalize_relative_path(relative_path)
            state = self._load_state()
            record = state["files"].get(relative_path)
            if (
                not record
                or record.get("deleted")
                or not self._is_attachment_record(record)
            ):
                raise SyncError("Attachment does not exist on the server.")
            return self._file_change(relative_path, record)

    def file_history(self, relative_path):
        with self.lock:
            relative_path = normalize_relative_path(relative_path)
            state = self._load_state()
            record = state["files"].get(relative_path)
            commits = self._git_history(relative_path, record)
            return {
                "relativePath": relative_path,
                "repoPath": str(self.files_root),
                "commits": commits,
            }

    def file_version_payload(self, relative_path, commit):
        with self.lock:
            relative_path = normalize_relative_path(relative_path)
            state = self._load_state()
            record = state["files"].get(relative_path)
            commit = self._resolve_history_commit(relative_path, commit, record)
            content = self._git_file_content(relative_path, commit, record)
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
            state = self._load_state()
            current = state["files"].get(relative_path)
            commit = self._resolve_history_commit(relative_path, commit, current)
            content = self._git_file_content(relative_path, commit, current)
            is_attachment = self._is_attachment_record(current)
            current_storage_path = (
                self._storage_path_for_record(relative_path, current)
                if current
                else None
            )
            metadata_snapshot = self._metadata_snapshot_for_commit(current, commit)
            current_revision = current.get("revision", 0) if current else 0
            current_deleted = bool(current and current.get("deleted"))
            current_hash = current.get("contentHash") if current else None
            attachment_snapshot = None
            if is_attachment:
                attachment_snapshot = metadata_snapshot.get("attachment")
                if attachment_snapshot is None:
                    attachment_snapshot = self._attachment_snapshot_from_record(
                        current,
                        relative_path,
                    )
                note_snapshot = metadata_snapshot.get("note")
                if note_snapshot is None and current:
                    note_snapshot = current.get("noteSnapshot")
            else:
                note_snapshot = metadata_snapshot.get("note")
                if note_snapshot is None and current:
                    note_snapshot = current.get("deletedNote") or current.get(
                        "noteSnapshot"
                    )
                if note_snapshot is None:
                    note_snapshot = self._note_snapshot_from_metadata(relative_path)
            target_hash = sha256_bytes(content) if content is not None else None

            now = utc_now()
            updated_at_ms = utc_now_ms()
            rollback_storage_path = current_storage_path
            if content is None:
                if current_deleted:
                    if is_attachment:
                        metadata_result = self._sync_rollback_attachment_metadata(
                            state,
                            relative_path,
                            deleted=True,
                            now=now,
                            updated_at_ms=updated_at_ms,
                            attachment_snapshot=attachment_snapshot,
                            note_snapshot=note_snapshot,
                        )
                    else:
                        metadata_result = self._sync_rollback_metadata(
                            state,
                            relative_path,
                            deleted=True,
                            now=now,
                            updated_at_ms=updated_at_ms,
                        )
                    if metadata_result["status"] != "unchanged":
                        state["updatedAt"] = now
                        atomic_write_json(self.state_path, state)
                        return {
                            "status": "accepted",
                            "relativePath": relative_path,
                            "revision": current_revision,
                            "serverRevision": state["serverRevision"],
                            "deleted": True,
                            "rolledBackToCommit": commit,
                            "metadata": metadata_result,
                            "manifest": self._manifest_from_state(state),
                        }
                    return {
                        "status": "unchanged",
                        "relativePath": relative_path,
                        "revision": current_revision,
                        "deleted": True,
                        "rolledBackToCommit": commit,
                    }
                file_path = self._file_path(relative_path, current)
                if file_path.exists():
                    file_path.unlink()
            else:
                if not current_deleted and current_hash == target_hash:
                    restored_attachments = []
                    if is_attachment:
                        metadata_result = self._sync_rollback_attachment_metadata(
                            state,
                            relative_path,
                            deleted=False,
                            now=now,
                            updated_at_ms=updated_at_ms,
                            attachment_snapshot=attachment_snapshot,
                            note_snapshot=note_snapshot,
                        )
                    else:
                        restored_attachments = self._restore_note_snapshot_attachments(
                            state,
                            relative_path,
                            note_snapshot,
                            now,
                        )
                        metadata_result = self._sync_rollback_metadata(
                            state,
                            relative_path,
                            deleted=False,
                            now=now,
                            updated_at_ms=updated_at_ms,
                            note_snapshot=note_snapshot,
                            restored_content=content,
                        )
                    if (
                        metadata_result["status"] != "unchanged"
                        or restored_attachments
                    ):
                        state["updatedAt"] = now
                        atomic_write_json(self.state_path, state)
                        return {
                            "status": "accepted",
                            "relativePath": relative_path,
                            "revision": current_revision,
                            "serverRevision": state["serverRevision"],
                            "contentHash": target_hash,
                            "deleted": False,
                            "gitCommit": (
                                current.get("gitCommit") if current else None
                            ),
                            "rolledBackToCommit": commit,
                            "metadata": metadata_result,
                            "restoredAttachments": restored_attachments,
                            "manifest": self._manifest_from_state(state),
                        }
                    return {
                        "status": "unchanged",
                        "relativePath": relative_path,
                        "revision": current_revision,
                        "contentHash": target_hash,
                        "deleted": False,
                        "rolledBackToCommit": commit,
                    }
                rollback_storage_path = (
                    current_storage_path
                    or self._desired_storage_path(
                        state,
                        relative_path,
                        {
                            "note": note_snapshot,
                            "attachment": attachment_snapshot,
                            "noteRelativePath": (
                                attachment_snapshot or {}
                            ).get("noteRelativePath")
                            if isinstance(attachment_snapshot, dict)
                            else None,
                        },
                        "attachment" if is_attachment else "file",
                        current=current,
                    )
                )
                atomic_write_bytes(
                    self._file_path(
                        relative_path,
                        storage_path=rollback_storage_path,
                    ),
                    content,
                )

            state["serverRevision"] += 1
            file_revision = state["serverRevision"]
            message = f"Rollback {relative_path} to {commit[:12]}"
            rollback_storage_path = rollback_storage_path or self._storage_path_for_record(
                relative_path,
                current,
            )
            git_commit = self._commit_file_change(
                relative_path,
                message,
                storage_path=rollback_storage_path,
            )
            history_fields = self._record_history_fields(current)
            if content is None:
                next_record = {
                    "revision": file_revision,
                    "storagePath": rollback_storage_path,
                    "contentHash": current_hash,
                    "size": current.get("size", 0) if current else 0,
                    "deleted": True,
                    "serverUpdatedAt": now,
                    "clientUpdatedAtMs": None,
                    "gitCommit": git_commit,
                    "rolledBackToGitCommit": commit,
                }
                next_record.update(history_fields)
                if is_attachment:
                    next_record.update(
                        self._attachment_rollback_record_metadata(
                            attachment_snapshot,
                            current,
                            relative_path,
                            deleted=True,
                        )
                    )
                elif note_snapshot:
                    next_record["deletedNote"] = note_snapshot
                state["files"][relative_path] = next_record
            else:
                next_record = {
                    "revision": file_revision,
                    "storagePath": rollback_storage_path,
                    "contentHash": target_hash,
                    "size": len(content),
                    "deleted": False,
                    "serverUpdatedAt": now,
                    "clientUpdatedAtMs": None,
                    "gitCommit": git_commit,
                    "rolledBackToGitCommit": commit,
                }
                next_record.update(history_fields)
                if is_attachment:
                    next_record.update(
                        self._attachment_rollback_record_metadata(
                            attachment_snapshot,
                            current,
                            relative_path,
                            deleted=False,
                            content_hash=target_hash,
                            size=len(content),
                            updated_at_ms=updated_at_ms,
                        )
                    )
                state["files"][relative_path] = next_record

            restored_attachments = []
            if is_attachment:
                metadata_result = self._sync_rollback_attachment_metadata(
                    state,
                    relative_path,
                    deleted=content is None,
                    now=now,
                    updated_at_ms=updated_at_ms,
                    attachment_snapshot=attachment_snapshot,
                    note_snapshot=note_snapshot,
                )
            else:
                if content is not None:
                    restored_attachments = self._restore_note_snapshot_attachments(
                        state,
                        relative_path,
                        note_snapshot,
                        now,
                    )
                metadata_result = self._sync_rollback_metadata(
                    state,
                    relative_path,
                    deleted=content is None,
                    now=now,
                    updated_at_ms=updated_at_ms,
                    note_snapshot=note_snapshot,
                    restored_content=content,
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
                "restoredAttachments": restored_attachments,
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
            "knownFiles",
        )
        known_attachments = self._known_files_by_path(
            payload.get("knownAttachments", []),
            base_revision,
            "knownAttachments",
        )

        with self.lock:
            state = self._load_state()
            now = utc_now()
            server_metadata = self._metadata_index()
            plan = self._build_sync_plan(
                state,
                metadata_body,
                server_metadata,
                known_files,
                known_attachments,
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
                        "relativePath": "metadata",
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

    def sync_attachment_upload(self, payload, user, connection_info=None):
        client_id = payload.get("clientId")
        if not isinstance(client_id, str) or not client_id.strip():
            raise SyncError("clientId is required.")
        client_id = client_id.strip()

        base_revision = self._to_int(payload.get("baseRevision", 0), "baseRevision")

        with self.lock:
            state = self._load_state()
            now = utc_now()
            attachment_result = self._sync_attachment(state, payload, base_revision, now)
            metadata_result = None

            if attachment_result["status"] != "conflict":
                metadata_result = self._sync_attachment_metadata(
                    state,
                    payload,
                    attachment_result,
                    now,
                )

            self._record_client(
                state,
                client_id,
                user,
                payload,
                connection_info,
                now,
                "sync_attachment",
            )
            state["updatedAt"] = now
            atomic_write_json(self.state_path, state)

            return {
                "status": (
                    "conflict"
                    if attachment_result["status"] == "conflict"
                    else "ok"
                ),
                "serverRevision": state["serverRevision"],
                "syncedAt": now,
                "attachment": attachment_result,
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
        attachments = payload.get("attachments", [])
        if not isinstance(attachments, list):
            raise SyncError("attachments must be a list.")

        with self.lock:
            state = self._load_state()
            accepted = []
            conflicts = []
            accepted_attachments = []
            attachment_conflicts = []
            accepted_paths = set()
            accepted_attachment_paths = set()
            metadata_result = None
            now = utc_now()

            metadata_deleted_file_paths = set()
            for item in files:
                if not isinstance(item, dict):
                    continue

                relative_path = normalize_relative_path(item.get("relativePath"))
                deleted = self._to_bool(
                    item.get("deleted"),
                    f"{relative_path}.deleted",
                )
                if not deleted or "lastKnownRevision" not in item:
                    continue

                current = state["files"].get(relative_path)
                last_known_revision = self._to_int(
                    item.get("lastKnownRevision"),
                    f"{relative_path}.lastKnownRevision",
                )
                if current and current.get("revision", 0) == last_known_revision:
                    metadata_deleted_file_paths.add(relative_path)
            if "metadata" in payload:
                metadata_result = self._sync_metadata(
                    state,
                    payload["metadata"],
                    base_revision,
                    now,
                    allowed_removed_file_paths=metadata_deleted_file_paths,
                )

            for item in files:
                result = self._sync_file(state, item, base_revision, now)
                if result["status"] == "conflict":
                    conflicts.append(result)
                else:
                    accepted.append(result)
                    accepted_paths.add(result["relativePath"])

            for item in attachments:
                result = self._sync_attachment(state, item, base_revision, now)
                if result["status"] == "conflict":
                    attachment_conflicts.append(result)
                else:
                    accepted_attachments.append(result)
                    accepted_attachment_paths.add(result["relativePath"])

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
                attachment=False,
            )
            remote_attachment_changes = self._remote_changes_since(
                state,
                base_revision,
                accepted_attachment_paths,
                attachment=True,
            )

            has_conflict = (
                conflicts
                or attachment_conflicts
                or self._metadata_conflicted(metadata_result)
            )
            return {
                "status": "conflict" if has_conflict else "ok",
                "serverRevision": state["serverRevision"],
                "syncedAt": now,
                "metadata": metadata_result,
                "accepted": accepted,
                "conflicts": conflicts,
                "acceptedAttachments": accepted_attachments,
                "attachmentConflicts": attachment_conflicts,
                "remoteChanges": remote_changes,
                "remoteAttachmentChanges": remote_attachment_changes,
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

    def _sync_metadata(
        self,
        state,
        metadata_payload,
        base_revision,
        now,
        allowed_removed_file_paths=None,
    ):
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

        orphaned_paths = self._metadata_orphaned_active_file_paths(
            state,
            body,
            allowed_removed_file_paths,
        )
        if orphaned_paths:
            return {
                "status": "conflict",
                "reason": "metadata_removes_existing_server_files",
                "revision": current.get("revision", 0),
                "contentHash": current.get("contentHash"),
                "orphanedFiles": orphaned_paths,
                "serverMetadata": self._read_metadata_body(),
            }

        state["serverRevision"] += 1
        self._write_metadata_body(body)
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
        return self._sync_file_item(
            state,
            item,
            base_revision,
            now,
            kind="file",
            allow_hash_only=False,
        )

    def _sync_attachment(self, state, item, base_revision, now):
        return self._sync_file_item(
            state,
            item,
            base_revision,
            now,
            kind="attachment",
            allow_hash_only=True,
        )

    def _sync_file_item(
        self,
        state,
        item,
        base_revision,
        now,
        kind,
        allow_hash_only=False,
    ):
        if not isinstance(item, dict):
            raise SyncError("Each file sync item must be an object.")

        relative_path = normalize_relative_path(item.get("relativePath"))
        deleted = self._to_bool(item.get("deleted"), f"{relative_path}.deleted")
        if deleted and "lastKnownRevision" not in item:
            raise SyncError(
                f"{relative_path}.lastKnownRevision is required when deleted is true."
            )
        last_known_revision = self._to_int(
            item.get("lastKnownRevision", base_revision),
            f"{relative_path}.lastKnownRevision",
        )
        current = state["files"].get(relative_path)
        current_revision = current.get("revision", 0) if current else 0
        current_storage_path = (
            self._storage_path_for_record(relative_path, current)
            if current
            else None
        )
        if current and not current.get("deleted") and self._record_kind(current) != kind:
            return {
                "status": "conflict",
                "relativePath": relative_path,
                "clientRevision": last_known_revision,
                "serverRevision": current_revision,
                "serverFile": self._file_change(relative_path, current),
            }

        content = b""
        content_hash = None
        hash_only = False

        if not deleted:
            declared_hash = self._declared_content_hash(item)
            hash_only = (
                allow_hash_only
                and "content" not in item
                and isinstance(declared_hash, str)
                and bool(declared_hash)
            )
            if hash_only:
                content_hash = declared_hash
                if not (
                    current
                    and not current.get("deleted")
                    and current.get("contentHash") == content_hash
                ):
                    raise SyncError(
                        f"{relative_path}.content is required because the attachment "
                        "is not already stored with the supplied contentHash."
                    )
            else:
                content = self._decode_content(item, relative_path)
                content_hash = sha256_bytes(content)
                if declared_hash and declared_hash != content_hash:
                    raise SyncError(
                        f"{relative_path}.contentHash does not match content."
                    )

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

        if (
            deleted
            and current
            and not current.get("deleted")
            and current_revision != last_known_revision
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

            file_path = self._file_path(relative_path, current)
            if file_path.exists():
                file_path.unlink()
            state["serverRevision"] += 1
            git_commit = self._commit_file_change(
                relative_path,
                f"Delete {relative_path}",
                storage_path=current_storage_path,
            )
            next_record = {
                "revision": state["serverRevision"],
                "storagePath": current_storage_path,
                "contentHash": current.get("contentHash"),
                "size": current.get("size", 0),
                "deleted": True,
                "serverUpdatedAt": now,
                "clientUpdatedAtMs": item.get("updatedAtMs"),
                "gitCommit": git_commit,
            }
            next_record.update(self._record_history_fields(current))
            if kind == "attachment":
                next_record.update(
                    self._attachment_record_metadata(
                        item,
                        relative_path,
                        current=current,
                        deleted=True,
                    )
                )
            state["files"][relative_path] = next_record
            result = {
                "status": "accepted",
                "relativePath": relative_path,
                "revision": state["serverRevision"],
                "storagePath": current_storage_path,
                "deleted": True,
                "gitCommit": git_commit,
            }
            if kind == "attachment":
                result.update(self._attachment_result_metadata(next_record))
            return result

        if current and self._same_file_state(current, deleted, content_hash):
            result = {
                "status": "unchanged",
                "relativePath": relative_path,
                "revision": current_revision,
                "storagePath": self._storage_path_for_record(relative_path, current),
                "contentHash": content_hash,
                "deleted": False,
            }
            if kind == "attachment":
                result.update(self._attachment_result_metadata(current))
            return result

        if not hash_only:
            next_storage_path = self._desired_storage_path(
                state,
                relative_path,
                item,
                kind,
                current=current,
            )
            if current_storage_path:
                self._move_storage_file_if_needed(
                    current_storage_path,
                    next_storage_path,
                )
            atomic_write_bytes(
                self._file_path(relative_path, storage_path=next_storage_path),
                content,
            )
            state["serverRevision"] += 1
            git_commit = self._commit_file_change(
                relative_path,
                f"Update {relative_path}",
                storage_path=next_storage_path,
                previous_storage_path=current_storage_path,
            )
        else:
            next_storage_path = current_storage_path
            git_commit = current.get("gitCommit")

        next_record = {
            "revision": state["serverRevision"],
            "storagePath": next_storage_path,
            "contentHash": content_hash,
            "size": current.get("size", 0) if hash_only else len(content),
            "deleted": False,
            "serverUpdatedAt": now,
            "clientUpdatedAtMs": item.get("updatedAtMs"),
            "gitCommit": git_commit,
        }
        next_record.update(self._record_history_fields(current))
        if kind == "attachment":
            next_record.update(
                self._attachment_record_metadata(
                    item,
                    relative_path,
                    current=current,
                    deleted=False,
                )
            )
        state["files"][relative_path] = next_record
        result = {
            "status": "accepted",
            "relativePath": relative_path,
            "revision": state["serverRevision"],
            "storagePath": next_storage_path,
            "contentHash": content_hash,
            "deleted": False,
            "gitCommit": git_commit,
        }
        if kind == "attachment":
            result.update(self._attachment_result_metadata(next_record))
        return result

    def _declared_content_hash(self, item):
        value = item.get("contentHash")
        if isinstance(value, str) and value.strip():
            return value.strip()

        attachment = item.get("attachment")
        if isinstance(attachment, dict):
            for field in ("contentHash", "sha256", "checksum"):
                value = attachment.get(field)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        return None

    def _attachment_record_metadata(
        self,
        item,
        relative_path,
        current=None,
        deleted=False,
    ):
        attachment = item.get("attachment")
        if attachment is not None and not isinstance(attachment, dict):
            raise SyncError("attachment must be an object.")

        note = item.get("note")
        if note is not None and not isinstance(note, dict):
            raise SyncError("note must be an object.")

        attachment = attachment or {}
        current = current or {}
        note_relative_path = (
            item.get("noteRelativePath")
            or attachment.get("noteRelativePath")
            or (note.get("relativePath") if isinstance(note, dict) else None)
            or current.get("noteRelativePath")
        )
        if note_relative_path:
            note_relative_path = normalize_relative_path(note_relative_path)
        elif not deleted:
            raise SyncError("noteRelativePath is required for attachment upload.")

        return {
            "kind": "attachment",
            "noteRelativePath": note_relative_path,
            "noteId": (
                item.get("noteId")
                or attachment.get("noteId")
                or (note.get("id") if isinstance(note, dict) else None)
                or current.get("noteId")
            ),
            "attachmentId": (
                item.get("attachmentId")
                or attachment.get("id")
                or current.get("attachmentId")
            ),
            "fileName": (
                item.get("fileName")
                or attachment.get("fileName")
                or current.get("fileName")
                or PurePosixPath(relative_path).name
            ),
            "mimeType": (
                item.get("mimeType")
                or attachment.get("mimeType")
                or current.get("mimeType")
            ),
        }

    def _attachment_result_metadata(self, record):
        result = {
            "kind": "attachment",
            "noteRelativePath": record.get("noteRelativePath"),
            "noteId": record.get("noteId"),
            "attachmentId": record.get("attachmentId"),
            "fileName": record.get("fileName"),
            "mimeType": record.get("mimeType"),
        }
        return {key: value for key, value in result.items() if value is not None}

    def _record_history_fields(self, record):
        if not isinstance(record, dict):
            return {}
        snapshots = record.get("metadataSnapshots")
        if not isinstance(snapshots, dict):
            return {}
        return {"metadataSnapshots": copy.deepcopy(snapshots)}

    def _metadata_snapshot_for_commit(self, record, commit):
        if not isinstance(record, dict):
            return {}

        snapshots = record.get("metadataSnapshots")
        if isinstance(snapshots, dict):
            snapshot = snapshots.get(commit)
            if isinstance(snapshot, dict):
                return copy.deepcopy(snapshot)

        if record.get("gitCommit") == commit:
            snapshot = {}
            if isinstance(record.get("noteSnapshot"), dict):
                snapshot["note"] = copy.deepcopy(record["noteSnapshot"])
            if isinstance(record.get("attachmentSnapshot"), dict):
                snapshot["attachment"] = copy.deepcopy(record["attachmentSnapshot"])
            return snapshot

        return {}

    def _store_metadata_snapshot(
        self,
        record,
        git_commit,
        note_snapshot=None,
        attachment_snapshot=None,
    ):
        if not isinstance(record, dict) or not git_commit:
            return False

        snapshot = {}
        snapshots = record.setdefault("metadataSnapshots", {})
        if isinstance(snapshots.get(git_commit), dict):
            snapshot.update(copy.deepcopy(snapshots[git_commit]))

        if isinstance(note_snapshot, dict):
            note_copy = copy.deepcopy(note_snapshot)
            record["noteSnapshot"] = note_copy
            snapshot.setdefault("note", note_copy)

        if isinstance(attachment_snapshot, dict):
            attachment_copy = copy.deepcopy(attachment_snapshot)
            record["attachmentSnapshot"] = attachment_copy
            snapshot.setdefault("attachment", attachment_copy)

        if not snapshot:
            return False

        snapshots[git_commit] = snapshot
        return True

    def _store_note_metadata_snapshot(self, state, relative_path, metadata):
        record = state["files"].get(relative_path)
        if not isinstance(record, dict) or self._is_attachment_record(record):
            return False

        note = self._notes_by_path(metadata).get(relative_path)
        if not isinstance(note, dict):
            return False

        return self._store_metadata_snapshot(
            record,
            record.get("gitCommit"),
            note_snapshot=note,
        )

    def _store_attachment_metadata_snapshot(
        self,
        state,
        relative_path,
        note_relative_path,
        metadata,
    ):
        record = state["files"].get(relative_path)
        if not isinstance(record, dict) or not self._is_attachment_record(record):
            return False

        note = self._notes_by_path(metadata).get(note_relative_path)
        attachment_entry = self._attachments_by_path(metadata).get(relative_path)
        attachment = (
            attachment_entry.get("attachment") if attachment_entry else None
        ) or self._attachment_snapshot_from_record(record, relative_path)

        return self._store_metadata_snapshot(
            record,
            record.get("gitCommit"),
            note_snapshot=note,
            attachment_snapshot=attachment,
        )

    def _normalized_attachment_snapshot(
        self,
        attachment,
        relative_path,
        note_relative_path=None,
        content_hash=None,
        size=None,
        updated_at_ms=None,
    ):
        snapshot = dict(attachment) if isinstance(attachment, dict) else {}
        relative_path = normalize_relative_path(relative_path)
        snapshot["relativePath"] = relative_path

        if note_relative_path:
            snapshot["noteRelativePath"] = normalize_relative_path(note_relative_path)
        elif snapshot.get("noteRelativePath"):
            snapshot["noteRelativePath"] = normalize_relative_path(
                snapshot.get("noteRelativePath")
            )

        if content_hash is not None:
            snapshot["contentHash"] = content_hash
        if size is not None:
            snapshot["size"] = size
        if updated_at_ms is not None:
            snapshot["updatedAtMs"] = updated_at_ms
        snapshot["deleted"] = False
        return {key: value for key, value in snapshot.items() if value is not None}

    def _attachment_snapshot_from_record(
        self,
        record,
        relative_path,
        content_hash=None,
        size=None,
        updated_at_ms=None,
    ):
        record = record or {}
        snapshot = {
            "id": record.get("attachmentId"),
            "fileName": record.get("fileName") or PurePosixPath(relative_path).name,
            "relativePath": relative_path,
            "mimeType": record.get("mimeType"),
            "contentHash": (
                content_hash if content_hash is not None else record.get("contentHash")
            ),
            "size": size if size is not None else record.get("size", 0),
            "updatedAtMs": (
                updated_at_ms
                if updated_at_ms is not None
                else record.get("clientUpdatedAtMs")
            ),
            "noteRelativePath": record.get("noteRelativePath"),
            "noteId": record.get("noteId"),
        }
        return {key: value for key, value in snapshot.items() if value is not None}

    def _attachment_rollback_record_metadata(
        self,
        attachment_snapshot,
        current,
        relative_path,
        deleted,
        content_hash=None,
        size=None,
        updated_at_ms=None,
    ):
        current = current or {}
        attachment = self._normalized_attachment_snapshot(
            attachment_snapshot,
            relative_path,
            note_relative_path=(
                (attachment_snapshot or {}).get("noteRelativePath")
                if isinstance(attachment_snapshot, dict)
                else None
            )
            or current.get("noteRelativePath"),
            content_hash=content_hash,
            size=size,
            updated_at_ms=updated_at_ms,
        )
        note_relative_path = attachment.get("noteRelativePath")
        if not note_relative_path and not deleted:
            raise SyncError("noteRelativePath is required for attachment rollback.")

        return {
            key: value
            for key, value in {
                "kind": "attachment",
                "noteRelativePath": note_relative_path,
                "noteId": attachment.get("noteId") or current.get("noteId"),
                "attachmentId": attachment.get("id")
                or attachment.get("attachmentId")
                or current.get("attachmentId"),
                "fileName": attachment.get("fileName")
                or current.get("fileName")
                or PurePosixPath(relative_path).name,
                "mimeType": attachment.get("mimeType") or current.get("mimeType"),
            }.items()
            if value is not None
        }

    def _attachment_content_from_history(self, relative_path, content_hash=None):
        record = self._load_state()["files"].get(relative_path)
        for item in self._git_history(relative_path, record):
            if item.get("deleted"):
                continue
            if content_hash and item.get("contentHash") != content_hash:
                continue
            content = self._git_file_content(relative_path, item["commit"], record)
            if content is not None:
                return content, item["commit"]
        return None, None

    def _restore_note_snapshot_attachments(
        self,
        state,
        note_relative_path,
        note_snapshot,
        now,
    ):
        if not isinstance(note_snapshot, dict):
            return []

        attachments = note_snapshot.get("attachments", [])
        if not isinstance(attachments, list):
            return []

        restored = []
        for attachment in attachments:
            if not isinstance(attachment, dict):
                continue
            relative_path = attachment.get("relativePath")
            if not relative_path:
                continue
            relative_path = normalize_relative_path(relative_path)
            target_hash = self._attachment_metadata_hash(attachment)
            current = state["files"].get(relative_path)
            if (
                current
                and self._is_attachment_record(current)
                and not current.get("deleted")
                and (not target_hash or current.get("contentHash") == target_hash)
            ):
                continue

            content, source_commit = self._attachment_content_from_history(
                relative_path,
                target_hash,
            )
            if content is None:
                continue

            storage_path = (
                self._storage_path_for_record(relative_path, current)
                if current
                else self._desired_storage_path(
                    state,
                    relative_path,
                    {
                        "attachment": attachment,
                        "note": note_snapshot,
                        "noteRelativePath": note_relative_path,
                    },
                    "attachment",
                    current=current,
                )
            )
            atomic_write_bytes(
                self._file_path(relative_path, storage_path=storage_path),
                content,
            )
            state["serverRevision"] += 1
            git_commit = self._commit_file_change(
                relative_path,
                f"Restore attachment {relative_path} for rollback",
                storage_path=storage_path,
            )
            attachment_snapshot = self._normalized_attachment_snapshot(
                attachment,
                relative_path,
                note_relative_path=note_relative_path,
                content_hash=sha256_bytes(content),
                size=len(content),
            )
            next_record = {
                "revision": state["serverRevision"],
                "storagePath": storage_path,
                "contentHash": sha256_bytes(content),
                "size": len(content),
                "deleted": False,
                "serverUpdatedAt": now,
                "clientUpdatedAtMs": attachment_snapshot.get("updatedAtMs"),
                "gitCommit": git_commit,
                "restoredFromGitCommit": source_commit,
            }
            next_record.update(self._record_history_fields(current))
            next_record.update(
                self._attachment_rollback_record_metadata(
                    attachment_snapshot,
                    current,
                    relative_path,
                    deleted=False,
                    content_hash=sha256_bytes(content),
                    size=len(content),
                    updated_at_ms=attachment_snapshot.get("updatedAtMs"),
                )
            )
            state["files"][relative_path] = next_record
            self._store_metadata_snapshot(
                next_record,
                git_commit,
                note_snapshot=note_snapshot,
                attachment_snapshot=attachment_snapshot,
            )
            restored.append(self._file_record(relative_path, next_record))

        return restored

    def _sync_file_metadata(self, state, payload, now):
        relative_path = normalize_relative_path(payload.get("relativePath"))
        deleted = self._to_bool(payload.get("deleted"), f"{relative_path}.deleted")
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
            deleted_note = self._note_for_update(
                metadata,
                relative_path,
                create=False,
            )
            record = state["files"].get(relative_path)
            if deleted_note and isinstance(record, dict):
                record["deletedNote"] = dict(deleted_note)
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
            if not deleted:
                self._store_note_metadata_snapshot(state, relative_path, metadata)
            return {
                "status": "unchanged",
                "revision": state["metadata"].get("revision", 0),
                "contentHash": state["metadata"].get("contentHash"),
            }

        metadata["generatedAt"] = now
        content_hash = sha256_json(metadata)
        state["serverRevision"] += 1
        self._write_metadata_body(metadata)
        state["metadata"] = {
            "revision": state["serverRevision"],
            "contentHash": content_hash,
            "updatedAt": now,
        }
        if not deleted:
            self._store_note_metadata_snapshot(state, relative_path, metadata)
        return {
            "status": "accepted",
            "revision": state["serverRevision"],
            "contentHash": content_hash,
        }

    def _sync_attachment_metadata(self, state, payload, attachment_result, now):
        relative_path = normalize_relative_path(payload.get("relativePath"))
        deleted = bool(payload.get("deleted", False))
        attachment = payload.get("attachment")
        note = payload.get("note")
        workspace = payload.get("workspace")

        if attachment is not None and not isinstance(attachment, dict):
            raise SyncError("attachment must be an object.")
        if note is not None and not isinstance(note, dict):
            raise SyncError("note must be an object.")
        if workspace is not None and not isinstance(workspace, dict):
            raise SyncError("workspace must be an object.")

        record = state["files"].get(relative_path) or {}
        note_relative_path = (
            payload.get("noteRelativePath")
            or (attachment.get("noteRelativePath") if isinstance(attachment, dict) else None)
            or (note.get("relativePath") if isinstance(note, dict) else None)
            or record.get("noteRelativePath")
            or attachment_result.get("noteRelativePath")
        )
        if not note_relative_path:
            return {
                "status": "unchanged",
                "revision": state["metadata"].get("revision", 0),
                "contentHash": state["metadata"].get("contentHash"),
            }
        note_relative_path = normalize_relative_path(note_relative_path)

        metadata = self._read_metadata_body() or self._empty_metadata()
        changed = False

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
            normalized_note.setdefault("relativePath", note_relative_path)
            normalized_note.setdefault(
                "fileName",
                PurePosixPath(note_relative_path).name,
            )
            if "attachments" not in normalized_note:
                current_note = self._note_for_update(
                    metadata,
                    note_relative_path,
                    create=False,
                )
                if current_note and isinstance(current_note.get("attachments"), list):
                    normalized_note["attachments"] = current_note["attachments"]
            changed = self._upsert_note(metadata, normalized_note) or changed
        else:
            changed = self._ensure_note(metadata, note_relative_path) or changed

        if deleted:
            changed = (
                self._remove_note_attachment(metadata, note_relative_path, relative_path)
                or changed
            )
        else:
            normalized_attachment = self._normalized_attachment_metadata(
                payload,
                attachment_result,
                record,
            )
            changed = (
                self._upsert_note_attachment(
                    metadata,
                    note_relative_path,
                    normalized_attachment,
                )
                or changed
            )

        if not changed:
            self._store_attachment_metadata_snapshot(
                state,
                relative_path,
                note_relative_path,
                metadata,
            )
            self._store_note_metadata_snapshot(state, note_relative_path, metadata)
            return {
                "status": "unchanged",
                "revision": state["metadata"].get("revision", 0),
                "contentHash": state["metadata"].get("contentHash"),
            }

        metadata["generatedAt"] = now
        content_hash = sha256_json(metadata)
        state["serverRevision"] += 1
        self._write_metadata_body(metadata)
        state["metadata"] = {
            "revision": state["serverRevision"],
            "contentHash": content_hash,
            "updatedAt": now,
        }
        self._store_attachment_metadata_snapshot(
            state,
            relative_path,
            note_relative_path,
            metadata,
        )
        self._store_note_metadata_snapshot(state, note_relative_path, metadata)
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
        note_snapshot=None,
        restored_content=None,
    ):
        metadata = self._read_metadata_body() or self._empty_metadata()
        if deleted:
            changed = self._remove_note(metadata, relative_path)
        else:
            changed = self._restore_note_metadata(
                metadata,
                relative_path,
                updated_at_ms,
                note_snapshot=note_snapshot,
                restored_content=restored_content,
            )
            self._store_note_metadata_snapshot(state, relative_path, metadata)

        if not changed:
            return {
                "status": "unchanged",
                "revision": state["metadata"].get("revision", 0),
                "contentHash": state["metadata"].get("contentHash"),
            }

        metadata["generatedAt"] = now
        content_hash = sha256_json(metadata)
        state["serverRevision"] += 1
        self._write_metadata_body(metadata)
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

    def _sync_rollback_attachment_metadata(
        self,
        state,
        relative_path,
        deleted,
        now,
        updated_at_ms,
        attachment_snapshot=None,
        note_snapshot=None,
    ):
        record = state["files"].get(relative_path) or {}
        attachment = self._normalized_attachment_snapshot(
            attachment_snapshot,
            relative_path,
            note_relative_path=(
                (attachment_snapshot or {}).get("noteRelativePath")
                if isinstance(attachment_snapshot, dict)
                else None
            )
            or record.get("noteRelativePath"),
            content_hash=record.get("contentHash"),
            size=record.get("size"),
            updated_at_ms=updated_at_ms,
        )
        note_relative_path = attachment.get("noteRelativePath") or record.get(
            "noteRelativePath"
        )
        if not note_relative_path:
            return {
                "status": "unchanged",
                "revision": state["metadata"].get("revision", 0),
                "contentHash": state["metadata"].get("contentHash"),
            }
        note_relative_path = normalize_relative_path(note_relative_path)

        metadata = self._read_metadata_body() or self._empty_metadata()
        changed = False
        current_note = self._note_for_update(
            metadata,
            note_relative_path,
            create=False,
        )
        if current_note is None and isinstance(note_snapshot, dict):
            restored_note = self._note_from_snapshot_or_content(
                note_relative_path,
                updated_at_ms,
                note_snapshot=note_snapshot,
            )
            changed = self._upsert_note(metadata, restored_note) or changed
        else:
            changed = self._ensure_note(metadata, note_relative_path) or changed

        if deleted:
            changed = (
                self._remove_note_attachment(metadata, note_relative_path, relative_path)
                or changed
            )
        else:
            changed = (
                self._upsert_note_attachment(
                    metadata,
                    note_relative_path,
                    attachment,
                )
                or changed
            )

        self._store_note_metadata_snapshot(state, note_relative_path, metadata)
        if not deleted:
            self._store_attachment_metadata_snapshot(
                state,
                relative_path,
                note_relative_path,
                metadata,
            )

        if not changed:
            return {
                "status": "unchanged",
                "revision": state["metadata"].get("revision", 0),
                "contentHash": state["metadata"].get("contentHash"),
            }

        metadata["generatedAt"] = now
        content_hash = sha256_json(metadata)
        state["serverRevision"] += 1
        self._write_metadata_body(metadata)
        state["metadata"] = {
            "revision": state["serverRevision"],
            "contentHash": content_hash,
            "updatedAt": now,
        }
        self._store_note_metadata_snapshot(state, note_relative_path, metadata)
        if not deleted:
            self._store_attachment_metadata_snapshot(
                state,
                relative_path,
                note_relative_path,
                metadata,
            )
        return {
            "status": "accepted",
            "revision": state["serverRevision"],
            "contentHash": content_hash,
        }

    def _restore_note_metadata(
        self,
        metadata,
        relative_path,
        updated_at_ms,
        note_snapshot=None,
        restored_content=None,
    ):
        notes = metadata.setdefault("notes", [])
        existing_index = None
        existing_note = None
        for index, note in enumerate(notes):
            if not isinstance(note, dict):
                continue
            try:
                note_path = normalize_relative_path(note.get("relativePath"))
            except SyncError:
                continue
            if note_path != relative_path:
                continue
            existing_index = index
            existing_note = note
            break

        if isinstance(note_snapshot, dict):
            restored_note = self._note_from_snapshot_or_content(
                relative_path,
                updated_at_ms,
                note_snapshot=note_snapshot,
                content=restored_content,
            )
        elif existing_note is not None:
            restored_note = dict(existing_note)
            restored_note["relativePath"] = relative_path
            restored_note.setdefault("fileName", PurePosixPath(relative_path).name)
            restored_note["updatedAtMs"] = updated_at_ms
            if not restored_note.get("title"):
                title = self._markdown_title(restored_content)
                if title:
                    restored_note["title"] = title
        else:
            restored_note = self._note_from_snapshot_or_content(
                relative_path,
                updated_at_ms,
                content=restored_content,
            )

        workspace_id = restored_note.get("workspace")
        if workspace_id and workspace_id not in self._workspaces_by_id(metadata):
            self._upsert_workspace(
                metadata,
                {
                    "id": workspace_id,
                    "name": restored_note.get("workspaceName") or workspace_id,
                },
            )

        if existing_index is not None:
            if existing_note == restored_note:
                return False
            notes[existing_index] = restored_note
            return True

        notes.append(restored_note)
        return True

    def _note_from_snapshot_or_content(
        self,
        relative_path,
        updated_at_ms=None,
        note_snapshot=None,
        content=None,
    ):
        note = dict(note_snapshot) if isinstance(note_snapshot, dict) else {}
        note["relativePath"] = relative_path
        note.setdefault("fileName", PurePosixPath(relative_path).name)

        folder = str(PurePosixPath(relative_path).parent)
        if folder != ".":
            note.setdefault("folder", folder)
            workspace_id = folder.split("/", 1)[0]
            note.setdefault("workspace", workspace_id)
            note.setdefault("workspaceName", workspace_id)

        if updated_at_ms is not None:
            note["updatedAtMs"] = updated_at_ms

        if not note.get("title"):
            note["title"] = (
                self._markdown_title(content)
                or PurePosixPath(relative_path).stem
            )

        if note.get("workspace") and not note.get("workspaceName"):
            note["workspaceName"] = note.get("workspace")

        return {key: value for key, value in note.items() if value is not None}

    def _note_snapshot_from_metadata(self, relative_path):
        metadata = self._read_metadata_body() or self._empty_metadata()
        note = self._notes_by_path(metadata).get(relative_path)
        return dict(note) if isinstance(note, dict) else None

    def _markdown_title(self, content):
        if not content:
            return None
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            return None
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped.startswith("#"):
                continue
            title = stripped.lstrip("#").strip()
            if title:
                return title
        return None

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

    def _known_files_by_path(self, known_files, base_revision, field_name):
        if known_files is None:
            return {}
        if not isinstance(known_files, list):
            raise SyncError(f"{field_name} must be a list.")

        by_path = {}
        for item in known_files:
            if not isinstance(item, dict):
                raise SyncError(f"Each {field_name} item must be an object.")
            relative_path = normalize_relative_path(item.get("relativePath"))
            deleted = self._to_bool(item.get("deleted"), f"{relative_path}.deleted")
            by_path[relative_path] = {
                "lastKnownRevision": self._to_int(
                    item.get("lastKnownRevision", base_revision),
                    f"{relative_path}.lastKnownRevision",
                ),
                "contentHash": item.get("contentHash"),
                "updatedAtMs": item.get("updatedAtMs"),
                "deleted": deleted,
            }
        return by_path

    def _build_sync_plan(
        self,
        state,
        client_metadata,
        server_metadata,
        known_files,
        known_attachments,
        base_revision,
    ):
        client_notes = self._notes_by_path(client_metadata)
        if self._is_metadata_index(server_metadata):
            server_notes = server_metadata["notes"]
            server_workspaces = server_metadata["workspaces"]
        else:
            server_notes = self._notes_by_path(server_metadata)
            server_workspaces = self._workspaces_by_id(server_metadata)
        client_workspaces = self._workspaces_by_id(client_metadata)
        note_file_paths = {
            relative_path
            for relative_path, record in state["files"].items()
            if not self._is_attachment_record(record)
        }
        paths = sorted(
            set(client_notes)
            | set(server_notes)
            | note_file_paths
        )
        plan = {
            "uploadFiles": [],
            "downloadFiles": [],
            "deleteServerFiles": [],
            "deleteLocalFiles": [],
            "uploadAttachments": [],
            "downloadAttachments": [],
            "deleteServerAttachments": [],
            "deleteLocalAttachments": [],
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
            client_delete_requested = self._known_delete_requested(known)

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
                    if client_delete_requested and server_revision == last_known_revision:
                        plan["deleteServerFiles"].append(
                            self._delete_plan_item(
                                relative_path,
                                "client_deleted_file",
                                record,
                            )
                        )
                    elif client_delete_requested:
                        plan["conflicts"].append(
                            self._conflict_plan_item(
                                relative_path,
                                "server_file_changed_after_client_delete",
                                None,
                                None,
                                record,
                                client_workspaces,
                                server_workspaces,
                            )
                        )
                    else:
                        plan["downloadFiles"].append(
                            self._download_plan_item(
                                relative_path,
                                "server_file_without_metadata",
                                None,
                                record,
                                server_workspaces,
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

                if client_delete_requested and server_revision == last_known_revision:
                    plan["deleteServerFiles"].append(
                        self._delete_plan_item(
                            relative_path,
                            "client_deleted_file",
                            record,
                            server_note,
                        )
                    )
                elif client_delete_requested:
                    plan["conflicts"].append(
                        self._conflict_plan_item(
                            relative_path,
                            "server_file_changed_after_client_delete",
                            None,
                            server_note,
                            record,
                            client_workspaces,
                            server_workspaces,
                        )
                    )
                else:
                    plan["downloadFiles"].append(
                        self._download_plan_item(
                            relative_path,
                            "missing_on_client",
                            server_note,
                            record,
                            server_workspaces,
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

        attachment_plan = self._build_attachment_plan(
            state,
            client_metadata,
            server_metadata,
            known_attachments,
            base_revision,
        )
        for key, values in attachment_plan.items():
            plan[key].extend(values)

        return plan

    def _build_attachment_plan(
        self,
        state,
        client_metadata,
        server_metadata,
        known_attachments,
        base_revision,
    ):
        client_attachments = self._attachments_by_path(client_metadata)
        if self._is_metadata_index(server_metadata):
            server_attachments = server_metadata["attachments"]
        else:
            server_attachments = self._attachments_by_path(server_metadata)
        server_records = {
            relative_path: record
            for relative_path, record in state["files"].items()
            if self._is_attachment_record(record)
        }
        paths = sorted(
            set(client_attachments)
            | set(server_attachments)
            | set(server_records)
        )
        plan = {
            "uploadAttachments": [],
            "downloadAttachments": [],
            "deleteServerAttachments": [],
            "deleteLocalAttachments": [],
            "conflicts": [],
        }

        for relative_path in paths:
            client_entry = client_attachments.get(relative_path)
            server_entry = server_attachments.get(relative_path)
            client_attachment = (
                client_entry.get("attachment") if client_entry else None
            )
            server_attachment = (
                server_entry.get("attachment") if server_entry else None
            )
            record = server_records.get(relative_path)
            known = known_attachments.get(relative_path, {})
            last_known_revision = known.get("lastKnownRevision", base_revision)
            server_revision = record.get("revision", 0) if record else 0
            server_deleted = bool(record and record.get("deleted"))
            client_hash = self._attachment_metadata_hash(client_attachment)
            known_hash = known.get("contentHash")
            client_delete_requested = self._known_delete_requested(known)

            if not client_attachment and not server_attachment:
                if server_deleted and server_revision > last_known_revision:
                    plan["deleteLocalAttachments"].append(
                        self._attachment_delete_plan_item(
                            relative_path,
                            "server_deleted_after_client_base",
                            record,
                        )
                    )
                elif record and not server_deleted:
                    if client_delete_requested and server_revision == last_known_revision:
                        plan["deleteServerAttachments"].append(
                            self._attachment_delete_plan_item(
                                relative_path,
                                "client_deleted_attachment",
                                record,
                            )
                        )
                    elif client_delete_requested:
                        plan["conflicts"].append(
                            self._attachment_conflict_plan_item(
                                relative_path,
                                "server_attachment_changed_after_client_delete",
                                client_entry,
                                server_entry,
                                record,
                            )
                        )
                    else:
                        plan["downloadAttachments"].append(
                            self._attachment_download_plan_item(
                                relative_path,
                                "server_attachment_without_metadata",
                                server_entry,
                                record,
                            )
                        )
                continue

            if client_attachment and not server_attachment:
                if (
                    record
                    and not server_deleted
                    and server_revision > last_known_revision
                    and record.get("contentHash") != client_hash
                ):
                    plan["conflicts"].append(
                        self._attachment_conflict_plan_item(
                            relative_path,
                            "server_attachment_exists_without_metadata",
                            client_entry,
                            server_entry,
                            record,
                        )
                    )
                elif server_deleted and server_revision > last_known_revision:
                    plan["conflicts"].append(
                        self._attachment_conflict_plan_item(
                            relative_path,
                            "server_deleted_after_client_base",
                            client_entry,
                            server_entry,
                            record,
                        )
                    )
                elif record and record.get("contentHash") == client_hash:
                    plan["uploadAttachments"].append(
                        self._attachment_upload_plan_item(
                            relative_path,
                            "missing_server_attachment_metadata",
                            client_entry,
                            record,
                            content_required=False,
                        )
                    )
                else:
                    plan["uploadAttachments"].append(
                        self._attachment_upload_plan_item(
                            relative_path,
                            "missing_on_server",
                            client_entry,
                            record,
                            content_required=True,
                        )
                    )
                continue

            if server_attachment and not client_attachment:
                if server_deleted:
                    if server_revision > last_known_revision:
                        plan["deleteLocalAttachments"].append(
                            self._attachment_delete_plan_item(
                                relative_path,
                                "server_deleted_after_client_base",
                                record,
                                server_entry,
                            )
                        )
                    continue

                if client_delete_requested and server_revision == last_known_revision:
                    plan["deleteServerAttachments"].append(
                        self._attachment_delete_plan_item(
                            relative_path,
                            "client_deleted_attachment",
                            record,
                            server_entry,
                        )
                    )
                elif client_delete_requested:
                    plan["conflicts"].append(
                        self._attachment_conflict_plan_item(
                            relative_path,
                            "server_attachment_changed_after_client_delete",
                            client_entry,
                            server_entry,
                            record,
                        )
                    )
                else:
                    plan["downloadAttachments"].append(
                        self._attachment_download_plan_item(
                            relative_path,
                            "missing_on_client",
                            server_entry,
                            record,
                        )
                    )
                continue

            if server_deleted:
                if server_revision > last_known_revision:
                    plan["conflicts"].append(
                        self._attachment_conflict_plan_item(
                            relative_path,
                            "server_deleted_after_client_base",
                            client_entry,
                            server_entry,
                            record,
                        )
                    )
                else:
                    plan["uploadAttachments"].append(
                        self._attachment_upload_plan_item(
                            relative_path,
                            "client_has_attachment_after_server_delete",
                            client_entry,
                            record,
                            content_required=True,
                        )
                    )
                continue

            if record and client_hash and record.get("contentHash") == client_hash:
                continue

            server_changed = (
                record
                and server_revision > last_known_revision
                and self._known_file_changed(record, known)
            )
            client_changed = bool(
                known_hash and client_hash and known_hash != client_hash
            )

            if server_changed and client_changed:
                plan["conflicts"].append(
                    self._attachment_conflict_plan_item(
                        relative_path,
                        "both_sides_changed",
                        client_entry,
                        server_entry,
                        record,
                    )
                )
                continue

            if server_changed:
                plan["downloadAttachments"].append(
                    self._attachment_download_plan_item(
                        relative_path,
                        "server_attachment_changed",
                        server_entry,
                        record,
                    )
                )
                continue

            plan["uploadAttachments"].append(
                self._attachment_upload_plan_item(
                    relative_path,
                    "client_attachment_changed",
                    client_entry,
                    record,
                    content_required=True,
                )
            )

        return plan

    def _metadata_orphaned_active_file_paths(
        self,
        state,
        metadata,
        allowed_removed_file_paths=None,
    ):
        active_file_paths = {
            relative_path
            for relative_path, record in state["files"].items()
            if not record.get("deleted") and not self._is_attachment_record(record)
        }
        if not active_file_paths:
            return []

        current_metadata = self._read_metadata_body() or self._empty_metadata()
        current_note_paths = set(self._notes_by_path(current_metadata))
        next_note_paths = set(self._notes_by_path(metadata))
        allowed_removed_file_paths = set(allowed_removed_file_paths or [])
        orphaned_paths = (
            (current_note_paths & active_file_paths)
            - next_note_paths
            - allowed_removed_file_paths
        )
        return sorted(orphaned_paths)

    def _known_delete_requested(self, known):
        return bool(known and known.get("deleted"))

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
            result["serverMetadata"] = (
                self._read_metadata_body()
                if self._is_metadata_index(server_metadata)
                else server_metadata
            )
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

    def _attachments_by_path(self, metadata):
        notes = metadata.get("notes", []) if isinstance(metadata, dict) else []
        if not isinstance(notes, list):
            raise SyncError("metadata.notes must be a list.")

        by_path = {}
        for note in notes:
            if not isinstance(note, dict):
                raise SyncError("Each metadata note must be an object.")

            note_relative_path = note.get("relativePath")
            if note_relative_path:
                note_relative_path = normalize_relative_path(note_relative_path)

            attachments = note.get("attachments", [])
            if attachments is None:
                continue
            if not isinstance(attachments, list):
                raise SyncError("note.attachments must be a list.")

            for attachment in attachments:
                if not isinstance(attachment, dict):
                    raise SyncError("Each note attachment must be an object.")
                relative_path = attachment.get("relativePath")
                if not relative_path:
                    continue
                relative_path = normalize_relative_path(relative_path)
                normalized = dict(attachment)
                normalized["relativePath"] = relative_path
                if note_relative_path:
                    normalized.setdefault("noteRelativePath", note_relative_path)
                if note.get("id"):
                    normalized.setdefault("noteId", note.get("id"))
                by_path[relative_path] = {
                    "attachment": normalized,
                    "note": note,
                }
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

    def _attachment_metadata_hash(self, attachment):
        if not isinstance(attachment, dict):
            return None
        for field in ("contentHash", "sha256", "checksum"):
            value = attachment.get(field)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

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

    def _attachment_upload_plan_item(
        self,
        relative_path,
        reason,
        entry,
        record,
        content_required,
    ):
        attachment = entry.get("attachment") if entry else None
        return {
            "type": "attachment",
            "relativePath": relative_path,
            "reason": reason,
            "contentRequired": content_required,
            "noteRelativePath": (
                attachment.get("noteRelativePath") if attachment else None
            ),
            "note": entry.get("note") if entry else None,
            "attachment": attachment,
            "serverAttachment": (
                self._file_record(relative_path, record) if record else None
            ),
        }

    def _attachment_download_plan_item(self, relative_path, reason, entry, record):
        attachment = entry.get("attachment") if entry else None
        return {
            "type": "attachment",
            "relativePath": relative_path,
            "reason": reason,
            "noteRelativePath": (
                attachment.get("noteRelativePath")
                if attachment
                else record.get("noteRelativePath") if record else None
            ),
            "note": entry.get("note") if entry else None,
            "attachment": attachment,
            "serverAttachment": (
                self._file_record(relative_path, record) if record else None
            ),
        }

    def _attachment_delete_plan_item(
        self,
        relative_path,
        reason,
        record,
        entry=None,
    ):
        attachment = entry.get("attachment") if entry else None
        return {
            "type": "attachment",
            "relativePath": relative_path,
            "reason": reason,
            "noteRelativePath": (
                attachment.get("noteRelativePath")
                if attachment
                else record.get("noteRelativePath") if record else None
            ),
            "attachment": attachment,
            "serverAttachment": (
                self._file_record(relative_path, record) if record else None
            ),
        }

    def _attachment_conflict_plan_item(
        self,
        relative_path,
        reason,
        client_entry,
        server_entry,
        record,
    ):
        client_attachment = client_entry.get("attachment") if client_entry else None
        server_attachment = server_entry.get("attachment") if server_entry else None
        return {
            "type": "attachment",
            "relativePath": relative_path,
            "reason": reason,
            "noteRelativePath": (
                (client_attachment or {}).get("noteRelativePath")
                or (server_attachment or {}).get("noteRelativePath")
                or (record.get("noteRelativePath") if record else None)
            ),
            "clientNote": client_entry.get("note") if client_entry else None,
            "serverNote": server_entry.get("note") if server_entry else None,
            "clientAttachment": client_attachment,
            "serverAttachmentMetadata": server_attachment,
            "serverAttachment": (
                self._file_record(relative_path, record) if record else None
            ),
        }

    def _file_record(self, relative_path, record):
        if not record:
            return None
        payload = {
            "relativePath": relative_path,
            "storagePath": self._storage_path_for_record(relative_path, record),
            "revision": record.get("revision", 0),
            "contentHash": record.get("contentHash"),
            "size": record.get("size", 0),
            "deleted": bool(record.get("deleted")),
            "serverUpdatedAt": record.get("serverUpdatedAt"),
            "clientUpdatedAtMs": record.get("clientUpdatedAtMs"),
            "gitCommit": record.get("gitCommit"),
            "rolledBackToGitCommit": record.get("rolledBackToGitCommit"),
        }
        if self._is_attachment_record(record):
            payload.update(self._attachment_result_metadata(record))
        return payload

    def _manifest_note_record(self, note):
        if not note:
            return None

        fields = ("title", "folder", "workspace", "workspaceName", "fileName")
        return {
            field: note.get(field)
            for field in fields
            if note.get(field) is not None
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

    def _ensure_note(self, metadata, relative_path):
        relative_path = normalize_relative_path(relative_path)
        notes = metadata.setdefault("notes", [])
        for note in notes:
            if note.get("relativePath") == relative_path:
                return False

        notes.append(
            {
                "relativePath": relative_path,
                "fileName": PurePosixPath(relative_path).name,
                "attachments": [],
            }
        )
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

    def _normalized_attachment_metadata(self, payload, attachment_result, record):
        attachment = payload.get("attachment")
        if attachment is not None and not isinstance(attachment, dict):
            raise SyncError("attachment must be an object.")
        attachment = dict(attachment or {})
        relative_path = normalize_relative_path(payload.get("relativePath"))

        attachment["relativePath"] = relative_path
        attachment.setdefault(
            "id",
            payload.get("attachmentId")
            or attachment_result.get("attachmentId")
            or record.get("attachmentId"),
        )
        attachment.setdefault(
            "fileName",
            payload.get("fileName")
            or attachment_result.get("fileName")
            or record.get("fileName")
            or PurePosixPath(relative_path).name,
        )
        attachment.setdefault(
            "mimeType",
            payload.get("mimeType")
            or attachment_result.get("mimeType")
            or record.get("mimeType"),
        )
        attachment.setdefault(
            "contentHash",
            payload.get("contentHash")
            or attachment_result.get("contentHash")
            or record.get("contentHash"),
        )
        attachment.setdefault(
            "size",
            attachment_result.get("size") or record.get("size", 0),
        )
        if payload.get("updatedAtMs") is not None:
            attachment["updatedAtMs"] = payload.get("updatedAtMs")
        attachment["deleted"] = False
        return {key: value for key, value in attachment.items() if value is not None}

    def _upsert_note_attachment(self, metadata, note_relative_path, attachment):
        note = self._note_for_update(metadata, note_relative_path)
        attachments = note.setdefault("attachments", [])
        if not isinstance(attachments, list):
            raise SyncError("note.attachments must be a list.")

        relative_path = normalize_relative_path(attachment.get("relativePath"))
        next_attachment = dict(attachment)
        next_attachment["relativePath"] = relative_path
        for index, current in enumerate(attachments):
            same_path = current.get("relativePath") == relative_path
            same_id = attachment.get("id") and current.get("id") == attachment.get("id")
            if same_path or same_id:
                if current == next_attachment:
                    return False
                attachments[index] = next_attachment
                return True

        attachments.append(next_attachment)
        return True

    def _remove_note_attachment(self, metadata, note_relative_path, relative_path):
        note = self._note_for_update(metadata, note_relative_path, create=False)
        if note is None:
            return False
        attachments = note.setdefault("attachments", [])
        if not isinstance(attachments, list):
            raise SyncError("note.attachments must be a list.")

        relative_path = normalize_relative_path(relative_path)
        next_attachments = [
            attachment
            for attachment in attachments
            if attachment.get("relativePath") != relative_path
        ]
        if len(next_attachments) == len(attachments):
            return False
        note["attachments"] = next_attachments
        return True

    def _note_for_update(self, metadata, relative_path, create=True):
        relative_path = normalize_relative_path(relative_path)
        notes = metadata.setdefault("notes", [])
        for note in notes:
            if note.get("relativePath") == relative_path:
                return note
        if not create:
            return None
        note = {
            "relativePath": relative_path,
            "fileName": PurePosixPath(relative_path).name,
            "attachments": [],
        }
        notes.append(note)
        return note

    def _same_file_state(self, record, deleted, content_hash):
        if deleted:
            return bool(record.get("deleted"))
        return not record.get("deleted") and record.get("contentHash") == content_hash

    def _record_kind(self, record):
        if self._is_attachment_record(record):
            return "attachment"
        return "file"

    def _is_attachment_record(self, record):
        return bool(record and record.get("kind") == "attachment")

    def _storage_path_for_record(self, relative_path, record=None):
        storage_path = record.get("storagePath") if isinstance(record, dict) else None
        return normalize_relative_path(storage_path or relative_path)

    def _desired_storage_path(self, state, relative_path, item, kind, current=None):
        if kind == "attachment":
            desired = self._desired_attachment_storage_path(relative_path, item, current)
        else:
            desired = self._desired_note_storage_path(relative_path, item, current)
        return self._unique_storage_path(state, relative_path, desired)

    def _desired_note_storage_path(self, relative_path, item, current=None):
        note = item.get("note") if isinstance(item.get("note"), dict) else {}
        path = PurePosixPath(relative_path)
        parts = self._storage_folder_parts_for_note(note, relative_path)
        fallback_stem = path.stem or "note"
        title = note.get("title") or fallback_stem
        suffix = path.suffix or ".md"
        filename = safe_storage_file_name(title, fallback_stem, suffix)
        return normalize_relative_path(str(PurePosixPath(*parts, filename)))

    def _storage_folder_parts_for_note(self, note, relative_path):
        note = note if isinstance(note, dict) else {}
        path = PurePosixPath(relative_path)
        folder = note.get("folder")
        if not folder:
            parent = str(path.parent)
            folder = "" if parent == "." else parent

        raw_parts = [
            part
            for part in str(folder).replace("\\", "/").split("/")
            if part and part not in (".", "..")
        ]
        workspace_id = note.get("workspace")
        workspace_name = note.get("workspaceName")
        workspace_first = (
            str(workspace_id).replace("\\", "/").split("/", 1)[0]
            if workspace_id
            else None
        )
        if workspace_name:
            if raw_parts:
                first = raw_parts[0]
                if (
                    not workspace_id
                    or first == workspace_id
                    or first == workspace_first
                    or first in ("unfiled", "미지정 워크스페이스")
                ):
                    raw_parts[0] = workspace_name
            else:
                raw_parts = [workspace_name]

        return [
            safe_storage_segment(part, "folder")
            for part in raw_parts
            if part and part not in (".", "..")
        ]

    def _desired_attachment_storage_path(self, relative_path, item, current=None):
        attachment = (
            item.get("attachment") if isinstance(item.get("attachment"), dict) else {}
        )
        note = item.get("note") if isinstance(item.get("note"), dict) else {}
        note_relative_path = (
            item.get("noteRelativePath")
            or attachment.get("noteRelativePath")
            or note.get("relativePath")
            or (current or {}).get("noteRelativePath")
        )
        note_relative_path = (
            normalize_relative_path(note_relative_path)
            if note_relative_path
            else str(PurePosixPath(relative_path).parent)
        )
        note_path = PurePosixPath(note_relative_path)
        parts = self._storage_folder_parts_for_note(note, note_relative_path)
        if not parts:
            note_parent = str(note_path.parent)
            parts = [
                safe_storage_segment(part, "folder")
                for part in note_parent.replace("\\", "/").split("/")
                if part and part not in (".", "..")
            ]
        note_label = note.get("title") or note_path.stem or "note"
        file_name = (
            attachment.get("fileName")
            or item.get("fileName")
            or PurePosixPath(relative_path).name
            or "attachment"
        )
        parts.extend(["attachments", safe_storage_segment(note_label, "note")])
        filename = safe_storage_file_name(file_name, "attachment")
        return normalize_relative_path(str(PurePosixPath(*parts, filename)))

    def _unique_storage_path(self, state, relative_path, desired_path):
        desired_path = normalize_relative_path(desired_path)
        used_paths = {
            self._storage_path_for_record(path, record)
            for path, record in state["files"].items()
            if path != relative_path and not record.get("deleted")
        }
        if desired_path not in used_paths:
            return desired_path

        path = PurePosixPath(desired_path)
        suffix = path.suffix
        stem = path.name[: -len(suffix)] if suffix else path.name
        digest = hashlib.sha1(relative_path.encode("utf-8")).hexdigest()[:8]
        for index in range(1000):
            marker = digest if index == 0 else f"{digest}-{index + 1}"
            candidate = str(path.with_name(f"{stem}-{marker}{suffix}"))
            if candidate not in used_paths:
                return normalize_relative_path(candidate)
        raise SyncError("Unable to allocate a unique storage path.")

    def _move_storage_file_if_needed(self, previous_storage_path, next_storage_path):
        previous_storage_path = normalize_relative_path(previous_storage_path)
        next_storage_path = normalize_relative_path(next_storage_path)
        if previous_storage_path == next_storage_path:
            return

        previous_path = self._file_path_from_storage_path(previous_storage_path)
        next_path = self._file_path_from_storage_path(next_storage_path)
        if not previous_path.exists():
            return

        next_path.parent.mkdir(parents=True, exist_ok=True)
        if next_path.exists():
            raise SyncError("Target storage path already exists.")
        previous_path.rename(next_path)
        self._prune_empty_dirs(previous_path.parent)

    def _prune_empty_dirs(self, start):
        current = Path(start)
        root = self.files_root.resolve()
        while current.exists() and current.resolve() != root:
            try:
                current.rmdir()
            except OSError:
                break
            current = current.parent

    def _remote_changes_since(
        self,
        state,
        base_revision,
        accepted_paths,
        attachment=False,
    ):
        changes = []
        for relative_path, record in sorted(state["files"].items()):
            if self._is_attachment_record(record) != attachment:
                continue
            if record.get("revision", 0) <= base_revision:
                continue
            if relative_path in accepted_paths:
                continue
            changes.append(self._file_change(relative_path, record))
        return changes

    def _file_change(self, relative_path, record):
        payload = self._file_record(relative_path, record)

        if not payload["deleted"]:
            payload["contentEncoding"] = "base64"
            payload["content"] = base64.b64encode(
                self._file_path(relative_path, record).read_bytes()
            ).decode("ascii")

        return payload

    def _manifest_from_state(self, state):
        notes_by_path = self._metadata_notes_by_path()
        files = []
        attachments = []
        for relative_path, record in sorted(state["files"].items()):
            if self._is_attachment_record(record):
                attachment_record = self._file_record(relative_path, record)
                note_record = self._manifest_note_record(
                    notes_by_path.get(record.get("noteRelativePath"))
                )
                if note_record:
                    attachment_record["note"] = note_record
                attachments.append(attachment_record)
            else:
                file_record = self._file_record(relative_path, record)
                note = notes_by_path.get(relative_path) or record.get("deletedNote")
                if not note and not record.get("deleted"):
                    content = None
                    file_path = self._file_path(relative_path, record)
                    if file_path.exists():
                        try:
                            content = file_path.read_bytes()
                        except OSError:
                            content = None
                    note = self._note_from_snapshot_or_content(
                        relative_path,
                        record.get("clientUpdatedAtMs"),
                        content=content,
                    )
                note_record = self._manifest_note_record(note)
                if note_record:
                    file_record["note"] = note_record
                files.append(file_record)

        return {
            "schemaVersion": state["schemaVersion"],
            "serverRevision": state["serverRevision"],
            "updatedAt": state["updatedAt"],
            "metadata": state["metadata"],
            "files": files,
            "attachments": attachments,
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

    def _commit_file_change(
        self,
        relative_path,
        message,
        storage_path=None,
        previous_storage_path=None,
    ):
        relative_path = normalize_relative_path(relative_path)
        storage_path = normalize_relative_path(storage_path or relative_path)
        paths = [storage_path]
        if previous_storage_path:
            previous_storage_path = normalize_relative_path(previous_storage_path)
            if previous_storage_path not in paths:
                paths.append(previous_storage_path)
        self._ensure_git_repo()
        self._run_git("add", "-A", "--", *paths)
        if self._staged_changes_exist():
            self._run_git("commit", "-m", message)
        return self._git_head()

    def _git_history(self, relative_path, record=None):
        relative_path = normalize_relative_path(relative_path)
        storage_path = self._storage_path_for_record(relative_path, record)
        self._ensure_git_repo(commit_existing=True)
        if not self._has_git_head():
            return []

        output = self._run_git(
            "log",
            "--follow",
            "--date=iso-strict",
            "--pretty=format:%H%x1f%h%x1f%aI%x1f%an%x1f%s",
            "--",
            storage_path,
        ).stdout.strip()
        if not output:
            return []

        commits = []
        for line in output.splitlines():
            parts = line.split("\x1f", 4)
            if len(parts) != 5:
                continue
            commit, short_commit, committed_at, author, message = parts
            content = self._git_file_content(
                relative_path,
                commit,
                record,
                storage_path=storage_path,
            )
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

    def _resolve_history_commit(self, relative_path, commit, record=None):
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
            for item in self._git_history(relative_path, record)
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

    def _git_file_content(self, relative_path, commit, record=None, storage_path=None):
        relative_path = normalize_relative_path(relative_path)
        storage_path = normalize_relative_path(
            storage_path or self._storage_path_for_record(relative_path, record)
        )
        candidate_paths = [storage_path]
        if relative_path not in candidate_paths:
            candidate_paths.append(relative_path)

        for candidate in candidate_paths:
            result = self._run_git(
                "cat-file",
                "-e",
                f"{commit}:{candidate}",
                check=False,
            )
            if result.returncode == 0:
                return self._run_git_bytes("show", f"{commit}:{candidate}").stdout
        return None

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

    @contextmanager
    def _metadata_connection(self):
        self.root.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.metadata_db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _ensure_metadata_store(self):
        with self._metadata_connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS metadata_document (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    schema_version INTEGER NOT NULL,
                    version INTEGER NOT NULL,
                    generated_at TEXT,
                    extra_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS metadata_workspaces (
                    id TEXT PRIMARY KEY,
                    position INTEGER NOT NULL,
                    body_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS metadata_notes (
                    relative_path TEXT PRIMARY KEY,
                    note_id TEXT,
                    workspace_id TEXT,
                    title TEXT,
                    updated_at_ms INTEGER,
                    position INTEGER NOT NULL,
                    body_json TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_metadata_notes_workspace
                    ON metadata_notes(workspace_id);
                CREATE INDEX IF NOT EXISTS idx_metadata_notes_updated
                    ON metadata_notes(updated_at_ms);

                CREATE TABLE IF NOT EXISTS metadata_attachments (
                    relative_path TEXT PRIMARY KEY,
                    note_relative_path TEXT NOT NULL,
                    attachment_id TEXT,
                    position INTEGER NOT NULL,
                    body_json TEXT NOT NULL,
                    FOREIGN KEY(note_relative_path)
                        REFERENCES metadata_notes(relative_path)
                        ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_metadata_attachments_note
                    ON metadata_attachments(note_relative_path);
                """
            )
            document = connection.execute(
                "SELECT schema_version FROM metadata_document WHERE id = 1"
            ).fetchone()
            if document is None:
                self._replace_metadata_body(connection, self._empty_metadata())

    def _metadata_has_rows(self, connection):
        for table in (
            "metadata_workspaces",
            "metadata_notes",
            "metadata_attachments",
        ):
            row = connection.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()
            if row and row["count"]:
                return True
        return False

    def _replace_metadata_body(self, connection, body):
        if not isinstance(body, dict):
            raise SyncError("metadata body must be an object.")

        workspaces = body.get("workspaces", [])
        notes = body.get("notes", [])
        if not isinstance(workspaces, list):
            raise SyncError("metadata.workspaces must be a list.")
        if not isinstance(notes, list):
            raise SyncError("metadata.notes must be a list.")

        extra = {
            key: copy.deepcopy(value)
            for key, value in body.items()
            if key not in ("version", "generatedAt", "workspaces", "notes")
        }
        version = self._to_int(body.get("version", 1), "metadata.version")
        generated_at = body.get("generatedAt")

        connection.execute("DELETE FROM metadata_attachments")
        connection.execute("DELETE FROM metadata_notes")
        connection.execute("DELETE FROM metadata_workspaces")
        connection.execute("DELETE FROM metadata_document")
        connection.execute(
            """
            INSERT INTO metadata_document (
                id,
                schema_version,
                version,
                generated_at,
                extra_json
            )
            VALUES (1, ?, ?, ?, ?)
            """,
            (
                METADATA_DB_SCHEMA_VERSION,
                version,
                generated_at,
                compact_json(extra),
            ),
        )

        for position, workspace in enumerate(workspaces):
            if not isinstance(workspace, dict):
                raise SyncError("Each metadata workspace must be an object.")
            workspace_id = workspace.get("id")
            if not workspace_id:
                continue
            connection.execute(
                """
                INSERT OR REPLACE INTO metadata_workspaces (
                    id,
                    position,
                    body_json
                )
                VALUES (?, ?, ?)
                """,
                (workspace_id, position, compact_json(workspace)),
            )

        for position, note in enumerate(notes):
            if not isinstance(note, dict):
                raise SyncError("Each metadata note must be an object.")
            relative_path = note.get("relativePath")
            if not relative_path:
                continue
            relative_path = normalize_relative_path(relative_path)
            note_body = dict(note)
            note_body["relativePath"] = relative_path
            had_attachments = "attachments" in note_body
            attachments = note_body.get("attachments", [])
            if attachments is None:
                attachments = []
            if not isinstance(attachments, list):
                raise SyncError("note.attachments must be a list.")
            if had_attachments:
                note_body["attachments"] = []
            else:
                note_body.pop("attachments", None)
            updated_at_ms = note_body.get("updatedAtMs")
            updated_at_ms = (
                self._to_int(updated_at_ms, "note.updatedAtMs")
                if updated_at_ms is not None
                else None
            )
            connection.execute(
                """
                INSERT OR REPLACE INTO metadata_notes (
                    relative_path,
                    note_id,
                    workspace_id,
                    title,
                    updated_at_ms,
                    position,
                    body_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    relative_path,
                    note_body.get("id"),
                    note_body.get("workspace"),
                    note_body.get("title"),
                    updated_at_ms,
                    position,
                    compact_json(note_body),
                ),
            )

            for attachment_position, attachment in enumerate(attachments):
                if not isinstance(attachment, dict):
                    raise SyncError("Each note attachment must be an object.")
                attachment_relative_path = attachment.get("relativePath")
                if not attachment_relative_path:
                    continue
                attachment_relative_path = normalize_relative_path(
                    attachment_relative_path
                )
                attachment_body = dict(attachment)
                attachment_body["relativePath"] = attachment_relative_path
                attachment_body.setdefault("noteRelativePath", relative_path)
                connection.execute(
                    """
                    INSERT OR REPLACE INTO metadata_attachments (
                        relative_path,
                        note_relative_path,
                        attachment_id,
                        position,
                        body_json
                    )
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        attachment_relative_path,
                        relative_path,
                        attachment_body.get("id")
                        or attachment_body.get("attachmentId"),
                        attachment_position,
                        compact_json(attachment_body),
                    ),
                )

    def _write_metadata_body(self, body):
        self._ensure_metadata_store()
        with self._metadata_connection() as connection:
            self._replace_metadata_body(connection, body)

    def _is_metadata_index(self, value):
        return isinstance(value, dict) and value.get("__metadataIndex") is True

    def _metadata_index(self):
        self._ensure_metadata_store()
        with self._metadata_connection() as connection:
            notes = self._metadata_notes_by_path(connection)
            workspaces = {
                workspace.get("id"): workspace
                for workspace in (
                    json.loads(row["body_json"])
                    for row in connection.execute(
                        """
                        SELECT body_json
                        FROM metadata_workspaces
                        ORDER BY position, id
                        """
                    )
                )
                if workspace.get("id")
            }
            attachments = {}
            for row in connection.execute(
                """
                SELECT relative_path, note_relative_path, body_json
                FROM metadata_attachments
                ORDER BY note_relative_path, position, relative_path
                """
            ):
                attachment = json.loads(row["body_json"])
                attachment["relativePath"] = row["relative_path"]
                attachment.setdefault("noteRelativePath", row["note_relative_path"])
                note = notes.get(row["note_relative_path"])
                if note and note.get("id"):
                    attachment.setdefault("noteId", note.get("id"))
                attachments[row["relative_path"]] = {
                    "attachment": attachment,
                    "note": note,
                }

            return {
                "__metadataIndex": True,
                "notes": notes,
                "workspaces": workspaces,
                "attachments": attachments,
            }

    def _metadata_notes_by_path(self, connection=None):
        if connection is None:
            self._ensure_metadata_store()
            with self._metadata_connection() as next_connection:
                return self._metadata_notes_by_path(next_connection)

        notes = {}
        for row in connection.execute(
            """
            SELECT relative_path, body_json
            FROM metadata_notes
            ORDER BY position, relative_path
            """
        ):
            note = json.loads(row["body_json"])
            note["relativePath"] = row["relative_path"]
            notes[row["relative_path"]] = note
        return notes

    def _read_metadata_body(self):
        self._ensure_metadata_store()
        with self._metadata_connection() as connection:
            document = connection.execute(
                """
                SELECT version, generated_at, extra_json
                FROM metadata_document
                WHERE id = 1
                """
            ).fetchone()
            if document is None:
                return None

            try:
                body = json.loads(document["extra_json"] or "{}")
            except json.JSONDecodeError as exc:
                raise SyncError("Stored metadata is invalid.") from exc

            body["version"] = document["version"]
            body["generatedAt"] = document["generated_at"]
            body["workspaces"] = [
                json.loads(row["body_json"])
                for row in connection.execute(
                    """
                    SELECT body_json
                    FROM metadata_workspaces
                    ORDER BY position, id
                    """
                )
            ]

            notes = []
            note_rows = connection.execute(
                """
                SELECT relative_path, body_json
                FROM metadata_notes
                ORDER BY position, relative_path
                """
            ).fetchall()
            for note_row in note_rows:
                note = json.loads(note_row["body_json"])
                note["relativePath"] = note_row["relative_path"]
                attachments = [
                    json.loads(row["body_json"])
                    for row in connection.execute(
                        """
                        SELECT body_json
                        FROM metadata_attachments
                        WHERE note_relative_path = ?
                        ORDER BY position, relative_path
                        """,
                        (note_row["relative_path"],),
                    )
                ]
                if attachments or "attachments" in note:
                    note["attachments"] = attachments
                notes.append(note)
            body["notes"] = notes
            return body

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

    def _file_path(self, relative_path, record=None, storage_path=None):
        storage_path = storage_path or self._storage_path_for_record(relative_path, record)
        return self._file_path_from_storage_path(storage_path)

    def _file_path_from_storage_path(self, storage_path):
        storage_path = normalize_relative_path(storage_path)
        target = (self.files_root / storage_path).resolve()
        files_root = self.files_root.resolve()
        if files_root not in target.parents and target != files_root:
            raise SyncError("Resolved file path escapes the storage directory.")
        return target

    def _to_int(self, value, field_name):
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise SyncError(f"{field_name} must be an integer.") from exc

    def _to_bool(self, value, field_name, default=False):
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        raise SyncError(f"{field_name} must be a boolean.")

    def _metadata_conflicted(self, metadata_result):
        return metadata_result is not None and metadata_result.get("status") == "conflict"
