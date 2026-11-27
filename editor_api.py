"""Browser editing over the existing transactional desktop sync repository.

Logical paths and IDs never change on a title edit. All writes go through
SyncStore so desktop synchronization, tombstones, and Git history stay intact.
"""

import base64
import copy
import hashlib
import html
import mimetypes
import posixpath
import re
import unicodedata
import uuid
from datetime import datetime
from pathlib import PurePosixPath
from urllib.parse import quote, unquote, urlsplit

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from sync_store import safe_storage_file_name, safe_storage_segment, utc_now, utc_now_ms


MAX_NOTE_BYTES = 2 * 1024 * 1024
MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024
CLIENT_ID = "notedown-web-editor"


class EditorBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class WorkspaceCreate(EditorBody):
    name: str = Field(min_length=1, max_length=120)


class NoteCreate(EditorBody):
    workspaceId: str = Field(min_length=1, max_length=512)
    title: str = Field(default="제목 없는 문서", min_length=1, max_length=200)
    content: str = Field(default="", max_length=MAX_NOTE_BYTES)


class NoteSave(EditorBody):
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(max_length=MAX_NOTE_BYTES)
    revision: int = Field(ge=0)


class AttachmentCleanup(EditorBody):
    serverRevision: int = Field(ge=0)
    attachmentIds: list[str] = Field(min_length=1, max_length=10000)


def _error(status, error, message, **extra):
    # The application error handler flattens this detail into the JSON response.
    raise HTTPException(status_code=status, detail={"error": error, "message": message, **extra})


def _title(value):
    value = value.strip()
    if not value or any(ord(char) < 32 for char in value):
        _error(422, "invalid_title", "제목은 공백이나 제어 문자만으로 만들 수 없습니다.")
    if len(safe_storage_segment(value, "note").encode("utf-8")) > 240:
        _error(422, "title_too_long", "제목이 너무 깁니다. 파일 이름으로 저장할 수 있도록 줄여 주세요.")
    return value


def _content(value):
    encoded = value.encode("utf-8")
    if len(encoded) > MAX_NOTE_BYTES:
        _error(413, "note_too_large", "문서는 UTF-8 기준 2 MB까지 저장할 수 있습니다.")
    return encoded


def _fallback_id(prefix, path):
    return prefix + hashlib.sha256(path.encode("utf-8")).hexdigest()[:24]


def _inline_image_type(content):
    # Never trust uploaded MIME types. SVG and HTML must remain downloads.
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if content.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "image/webp"
    if len(content) >= 16 and content[4:8] == b"ftyp" and content[8:12] in (b"avif", b"avis"):
        return "image/avif"
    return None


def _markdown_excerpt(content):
    text = content.decode("utf-8", errors="ignore").lstrip("\ufeff")
    # YAML front matter is metadata, and may consume the entire bounded sample.
    front_matter_start = re.match(r"\A---\r?\n", text)
    if front_matter_start:
        remainder = text[front_matter_start.end():]
        front_matter_end = re.search(r"(?m)^(?:---|\.\.\.)[ \t]*\r?(?:\n|$)", remainder)
        text = remainder[front_matter_end.end():] if front_matter_end else ""
    text = re.sub(r"(?m)^\s*(?:#{1,6}\s+|>\s*|[-*+]\s+(?:\[[ xX]\]\s*)?|\d+[.)]\s+)", "", text)
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"(?m)^\s*(?:`{3,}|~{3,}).*$", "", text)
    text = re.sub(r"<!--.*?(?:-->|$)", "", text, flags=re.DOTALL)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"[*_`~]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= 100 else text[:99].rstrip() + "…"


def _created_at_ms(note, record):
    """Use actual creation metadata only; modification dates are not creation dates."""
    for source in (note, record.get("noteSnapshot") or {}):
        for key in ("createdAtMs", "createdAt"):
            value = source.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                if 0 <= value <= 8640000000000000:
                    return int(value)
            if isinstance(value, str) and key == "createdAt":
                try:
                    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                    # A localized label or timezone-free date cannot identify an instant.
                    if parsed.tzinfo is not None:
                        timestamp = int(parsed.timestamp() * 1000)
                        if timestamp >= 0:
                            return timestamp
                except (ValueError, OverflowError, OSError):
                    pass
    return None


def _reference_text(value):
    # Decoding more than once intentionally retains uncertain references rather
    # than making percent-encoded/HTML-escaped links eligible for cleanup.
    for _ in range(3):
        decoded = html.unescape(unquote(value))
        if decoded == value:
            break
        value = decoded
    value = re.sub(r"\\([!\"#$%&'()*+,\-./:;<=>?@\[\]\\^_`{|}~])", r"\1", value)
    return unicodedata.normalize("NFC", value).replace("\\", "/")


def _referenced_attachment_paths(content, note_path, attachments):
    """Conservative Markdown/HTML matching, including code and bare path mentions.

    Cleanup must never depend on a renderer discarding an unusual link syntax.
    Keep literal path mentions (also in fenced code, reference definitions and
    HTML), then normalize URL tokens for links containing dot segments.
    """
    text = _reference_text(content)
    parent = posixpath.dirname(_reference_text(note_path))
    candidates = set()
    # Quotes, parentheses and whitespace delimit normal link destinations. The
    # literal matching below additionally handles paths containing those chars.
    tokens = re.findall(r"[^\s<>\"'`\[\]()]+", text)
    # Angle-wrapped Markdown destinations and quoted HTML attributes can also
    # contain spaces and parentheses, including paths with dot segments.
    tokens.extend(re.findall(r"<([^<>\r\n]+)>", text))
    tokens.extend(match.group(2) for match in re.finditer(r"(?:src|href|poster|data)\s*=\s*([\"'])(.*?)\1", text, re.IGNORECASE | re.DOTALL))
    # Preserve parentheses inside filenames. A link's surrounding parentheses
    # may otherwise split the destination before dot segments are resolved.
    for token in re.findall(r"[^\s<>\"'`\[\]]+", text):
        for candidate in (token, token.rstrip(".,;!?*_")):
            candidate = candidate.removeprefix("(")
            tokens.extend((candidate, candidate.removesuffix(")")))
    for token in tokens:
        try:
            path = urlsplit(token).path
        except ValueError:
            continue
        for candidate in (path, token):
            if not candidate:
                continue
            candidates.add(posixpath.normpath(candidate.lstrip("/")))
            candidates.add(posixpath.normpath("/" + candidate.lstrip("/")).lstrip("/"))
            if not candidate.startswith("/"):
                candidates.add(posixpath.normpath(posixpath.join(parent, candidate)))
                # Browser URL resolution clamps traversal at the storage root.
                candidates.add(posixpath.normpath("/" + posixpath.join(parent, candidate)).lstrip("/"))
    referenced = set()
    for attachment in attachments:
        path = _reference_text(attachment["relativePath"])
        relative = posixpath.relpath(path, parent or ".")
        aliases = (path, relative, _reference_text(attachment["url"]))
        # Unusual filenames can overlap Markdown delimiters or span URL tokens.
        # A filename mention is enough to retain these ambiguous cases; cleanup
        # should prefer keeping a duplicate over deleting a possible reference.
        filename = posixpath.basename(path)
        if re.search(r"[\s()\[\]<>\"'`]", filename):
            aliases += (filename,)
        if path in candidates or any(alias in text for alias in aliases):
            referenced.add(attachment["relativePath"])
    return referenced


def register_editor_routes(app):
    store = app.state.store
    router = APIRouter(prefix="/api/editor", tags=["Editor"], dependencies=[Depends(app.state.authenticate)])

    def snapshot():
        """Called under the repository lock to keep content and revisions coherent."""
        state = store._load_state()
        metadata = store._read_metadata_body() or store._empty_metadata()
        notes = {note["relativePath"]: copy.deepcopy(note) for note in metadata.get("notes", []) if note.get("relativePath")}
        active = {}
        for path, record in state["files"].items():
            if record.get("deleted") or store._is_attachment_record(record):
                continue
            note = notes.get(path)
            if note is None:
                note = store._note_from_snapshot_or_content(path, record.get("clientUpdatedAtMs"), note_snapshot=record.get("noteSnapshot"))
                note = store._normalize_metadata_note(note)
            note = dict(note)
            created_at_ms = _created_at_ms(note, record)
            if "createdAtMs" not in note and created_at_ms is not None:
                note["createdAtMs"] = created_at_ms
            note.setdefault("relativePath", path)
            note["id"] = note.get("id") or _fallback_id("note-", path)
            note.setdefault("title", PurePosixPath(path).stem)
            note.setdefault("workspace", str(PurePosixPath(path).parent) if str(PurePosixPath(path).parent) != "." else "unfiled")
            note.setdefault("workspaceName", note["workspace"])
            active[note["id"]] = note
        by_path = {note["relativePath"]: note for note in active.values()}
        for path, record in state["files"].items():
            if record.get("deleted") or not store._is_attachment_record(record):
                continue
            note = by_path.get(record.get("noteRelativePath"))
            if note is not None and not any(item.get("relativePath") == path for item in note.get("attachments", [])):
                note.setdefault("attachments", []).append(store._attachment_snapshot_from_record(record, path))
        return state, metadata, active

    def find_note(note_id, state=None, notes=None):
        if state is None or notes is None:
            state, _, notes = snapshot()
        note = notes.get(note_id)
        if note is None:
            _error(404, "note_not_found", "문서를 찾을 수 없습니다.")
        return note, state["files"][note["relativePath"]]

    def note_attachments(note, state):
        metadata_attachments = {item["relativePath"]: item for item in note.get("attachments", []) if item.get("relativePath")}
        attachments = []
        for path, record in state["files"].items():
            if record.get("deleted") or not store._is_attachment_record(record):
                continue
            if record.get("noteRelativePath") != note["relativePath"]:
                continue
            attachment = dict(metadata_attachments.get(path) or {})
            attachment.update({
                "id": attachment.get("id") or record.get("attachmentId") or _fallback_id("att-", path),
                "relativePath": path,
                "fileName": attachment.get("fileName") or record.get("fileName") or PurePosixPath(path).name,
                "mimeType": attachment.get("mimeType") or record.get("mimeType") or "application/octet-stream",
                "size": record.get("size", 0),
                "revision": record.get("revision", 0),
                "markdownPath": quote(path, safe="/"),
            })
            attachment["url"] = "/api/editor/attachments/" + quote(attachment["id"], safe="")
            attachments.append(attachment)
        return attachments

    def present_note(note, state):
        result = dict(note)
        record = state["files"][note["relativePath"]]
        try:
            with store._file_path(note["relativePath"], record).open("rb") as document:
                excerpt = _markdown_excerpt(document.read(512))
        except OSError:
            excerpt = ""
        result.update({
            "revision": record.get("revision", 0),
            "size": record.get("size", 0),
            "updatedAt": record.get("serverUpdatedAt"),
            "updatedAtMs": note.get("updatedAtMs") or record.get("clientUpdatedAtMs") or 0,
            "createdAtMs": _created_at_ms(note, record),
            "attachments": note_attachments(note, state),
            "excerpt": excerpt,
        })
        return result

    def note_reference(note):
        return {key: note.get(key) for key in ("id", "title", "relativePath", "workspaceName")}

    def all_attachments(state, metadata, notes):
        metadata_attachments = {
            item["relativePath"]: item
            for note in metadata.get("notes", [])
            for item in note.get("attachments", [])
            if item.get("relativePath")
        }
        notes_by_path = {note["relativePath"]: note for note in notes.values()}
        attachments = []
        for path, record in state["files"].items():
            if record.get("deleted") or not store._is_attachment_record(record):
                continue
            attachment = dict(metadata_attachments.get(path) or {})
            attachment.update({
                "id": attachment.get("id") or record.get("attachmentId") or _fallback_id("att-", path),
                "relativePath": path,
                "fileName": attachment.get("fileName") or record.get("fileName") or PurePosixPath(path).name,
                "mimeType": attachment.get("mimeType") or record.get("mimeType") or "application/octet-stream",
                "size": record.get("size", 0),
                "revision": record.get("revision", 0),
                "markdownPath": quote(path, safe="/"),
            })
            attachment["url"] = "/api/editor/attachments/" + quote(attachment["id"], safe="")
            owner = notes_by_path.get(record.get("noteRelativePath"))
            attachment["ownerNote"] = note_reference(owner) if owner else None
            attachments.append(attachment)
        return attachments

    def attachment_library_snapshot(state, metadata, notes):
        attachments = all_attachments(state, metadata, notes)
        references = {item["relativePath"]: [] for item in attachments}
        warnings = []
        # This runs while holding the same repository lock used by sync writers.
        # Read every active saved document, including documents in other workspaces.
        for note in notes.values():
            path = note["relativePath"]
            try:
                content = store._file_path(path, state["files"][path]).read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                warnings.append({"noteId": note["id"], "relativePath": path, "message": "문서를 읽을 수 없어 첨부 파일 참조를 확인하지 못했습니다."})
                continue
            for referenced_path in _referenced_attachment_paths(content, path, attachments):
                references[referenced_path].append(note_reference(note))
        for attachment in attachments:
            attachment["references"] = references[attachment["relativePath"]]
            attachment["referenced"] = bool(attachment["references"])
            try:
                with store._file_path(attachment["relativePath"], state["files"][attachment["relativePath"]]).open("rb") as source:
                    image_type = _inline_image_type(source.read(32))
            except OSError:
                image_type = None
            attachment["isImage"] = bool(image_type)
        attachments.sort(key=lambda item: (item["fileName"].casefold(), item["relativePath"]))
        return {
            "attachments": attachments,
            "serverRevision": state["serverRevision"],
            "cleanupAllowed": not warnings,
            "scanWarnings": warnings,
            "totalBytes": sum(item["size"] for item in attachments),
        }

    def detail(note_id):
        state, _, notes = snapshot()
        note, record = find_note(note_id, state, notes)
        if record.get("size", 0) > MAX_NOTE_BYTES:
            _error(413, "note_too_large", "2 MB보다 큰 문서는 웹 편집기로 열 수 없습니다.")
        payload = store.file_payload(note["relativePath"])
        try:
            content = base64.b64decode(payload["content"]).decode("utf-8")
        except UnicodeDecodeError:
            _error(415, "not_text_document", "UTF-8 텍스트 문서만 편집할 수 있습니다.")
        return {"note": present_note(note, state), "content": content, "revision": record["revision"], "serverRevision": state["serverRevision"]}

    def check_revision(record, revision):
        if record.get("revision", 0) != revision:
            _error(409, "revision_conflict", "다른 곳에서 문서가 변경되었습니다. 작성한 내용을 보관하고 최신 버전을 확인하세요.", revision=record.get("revision", 0))

    def context(state):
        return {"clientId": CLIENT_ID, "clientInfo": {"platform": "web", "app": "Notedown editor"}, "baseRevision": state["serverRevision"]}

    def metadata_with_active_notes(metadata, notes):
        body = copy.deepcopy(metadata)
        active_by_path = {note["relativePath"]: note for note in notes.values()}
        # A history rollback can restore an older note snapshot while newer
        # attachment records remain active. Carry the hydrated attachment index
        # into batch metadata, including for notes already present in the DB.
        body["notes"] = [
            copy.deepcopy(active_by_path.get(note.get("relativePath"), note))
            for note in body.get("notes", [])
        ]
        existing_paths = {note.get("relativePath") for note in body.get("notes", [])}
        for note in notes.values():
            if note["relativePath"] not in existing_paths:
                body.setdefault("notes", []).append(copy.deepcopy(note))
        return body

    @router.get("/library")
    def library():
        with store._store_lock():
            state, metadata, notes = snapshot()
            presented = [present_note(note, state) for note in notes.values()]
            presented.sort(key=lambda note: (note["updatedAtMs"], note["title"]), reverse=True)
            workspaces = {item["id"]: dict(item, count=0) for item in metadata.get("workspaces", []) if item.get("id")}
            for note in presented:
                workspace_id = note["workspace"]
                workspaces.setdefault(workspace_id, {"id": workspace_id, "name": note.get("workspaceName") or workspace_id, "count": 0})["count"] += 1
            return {"workspaces": list(workspaces.values()), "notes": presented, "attachments": all_attachments(state, metadata, notes), "serverRevision": state["serverRevision"], "limits": {"noteBytes": MAX_NOTE_BYTES, "attachmentBytes": MAX_ATTACHMENT_BYTES}}

    @router.get("/attachment-library")
    def attachment_library():
        with store._store_lock():
            return attachment_library_snapshot(*snapshot())

    @router.post("/attachments/cleanup")
    def cleanup_attachments(body: AttachmentCleanup, request: Request):
        with store._store_lock():
            state, metadata, notes = snapshot()
            if body.serverRevision != state["serverRevision"]:
                _error(409, "attachment_snapshot_stale", "저장소가 변경되었습니다. 첨부 파일 목록을 새로고침한 뒤 다시 확인해 주세요.")
            library = attachment_library_snapshot(state, metadata, notes)
            if not library["cleanupAllowed"]:
                _error(409, "reference_scan_incomplete", "일부 문서의 참조를 확인할 수 없어 파일을 정리하지 않았습니다.", scanWarnings=library["scanWarnings"])
            by_id = {item["id"]: item for item in library["attachments"]}
            if len(set(body.attachmentIds)) != len(body.attachmentIds) or any(attachment_id not in by_id for attachment_id in body.attachmentIds):
                _error(422, "invalid_attachment_selection", "선택한 첨부 파일을 확인할 수 없습니다. 목록을 새로고침해 주세요.")
            selected = [by_id[attachment_id] for attachment_id in body.attachmentIds]
            if any(item["referenced"] for item in selected):
                _error(409, "attachment_referenced", "문서에서 참조하는 파일이 있어 정리하지 않았습니다. 목록을 새로고침해 주세요.")
            removed_paths = {item["relativePath"] for item in selected}
            metadata = metadata_with_active_notes(metadata, notes)
            for note in metadata.get("notes", []):
                if "attachments" in note:
                    note["attachments"] = [item for item in note["attachments"] if item.get("relativePath") not in removed_paths]
            metadata["generatedAt"] = utc_now()
            # SyncStore preflights the complete batch and rolls metadata, files,
            # tombstones and Git history back together if any mutation fails.
            result = store.sync({
                **context(state),
                "metadata": {"body": metadata, "lastKnownRevision": state["metadata"].get("revision", 0)},
                "attachments": [{"relativePath": item["relativePath"], "lastKnownRevision": item["revision"], "deleted": True} for item in selected],
            }, request.state.current_user)
            if result.get("attachmentConflicts") or (result.get("metadata") or {}).get("status") == "conflict":
                _error(409, "attachment_snapshot_stale", "저장소가 변경되어 정리하지 못했습니다. 목록을 새로고침해 주세요.")
            return {"status": "deleted", "deletedCount": len(selected), "deletedBytes": sum(item["size"] for item in selected), "serverRevision": result["serverRevision"]}

    @router.post("/workspaces", status_code=201)
    def create_workspace(body: WorkspaceCreate, request: Request):
        name = _title(body.name)
        with store._store_lock():
            state, metadata, notes = snapshot()
            if any(item.get("name", "").casefold() == name.casefold() for item in metadata.get("workspaces", [])):
                _error(409, "workspace_exists", "같은 이름의 워크스페이스가 이미 있습니다.")
            workspace_id = safe_storage_segment(name, "workspace")
            occupied = {item.get("id") for item in metadata.get("workspaces", [])}
            occupied.update(note.get("workspace") for note in notes.values())
            if workspace_id in occupied:
                workspace_id += "-" + uuid.uuid4().hex[:8]
            workspace = {"id": workspace_id, "name": name}
            metadata = metadata_with_active_notes(metadata, notes)
            metadata.setdefault("workspaces", []).append(workspace)
            metadata["generatedAt"] = utc_now()
            result = store.sync({**context(state), "metadata": {"body": metadata, "lastKnownRevision": state["metadata"].get("revision", 0)}}, request.state.current_user)
            if (result.get("metadata") or {}).get("status") == "conflict":
                _error(409, "revision_conflict", "저장소가 변경되어 워크스페이스를 만들지 못했습니다.")
            return {"workspace": {**workspace, "count": 0}}

    @router.post("/notes", status_code=201)
    def create_note(body: NoteCreate, request: Request):
        title, content = _title(body.title), _content(body.content)
        with store._store_lock():
            state, metadata, notes = snapshot()
            workspace = next((item for item in metadata.get("workspaces", []) if item.get("id") == body.workspaceId), None)
            if workspace is None:
                existing = next((note for note in notes.values() if note.get("workspace") == body.workspaceId), None)
                if existing:
                    workspace = {"id": existing["workspace"], "name": existing.get("workspaceName") or existing["workspace"]}
            if workspace is None:
                _error(404, "workspace_not_found", "워크스페이스를 찾을 수 없습니다.")
            note_id = "note-" + uuid.uuid4().hex
            path = f"{workspace['id']}/{note_id}.md"
            note = {"id": note_id, "title": title, "workspace": workspace["id"], "workspaceName": workspace.get("name") or workspace["id"], "folder": workspace["id"], "fileName": note_id + ".md", "relativePath": path, "createdAtMs": utc_now_ms(), "updatedAtMs": utc_now_ms(), "attachments": []}
            store.sync_file_upload({**context(state), "relativePath": path, "lastKnownRevision": 0, "contentEncoding": "base64", "content": base64.b64encode(content).decode("ascii"), "updatedAtMs": note["updatedAtMs"], "note": note, "workspace": workspace}, request.state.current_user)
            return detail(note_id)

    @router.get("/notes/{note_id}")
    def get_note(note_id: str):
        with store._store_lock():
            return detail(note_id)

    @router.put("/notes/{note_id}")
    def save_note(note_id: str, body: NoteSave, request: Request):
        title, content = _title(body.title), _content(body.content)
        with store._store_lock():
            state, _, notes = snapshot()
            note, record = find_note(note_id, state, notes)
            check_revision(record, body.revision)
            note.update(title=title, updatedAtMs=utc_now_ms())
            result = store.sync_file_upload({**context(state), "relativePath": note["relativePath"], "lastKnownRevision": body.revision, "contentEncoding": "base64", "content": base64.b64encode(content).decode("ascii"), "updatedAtMs": note["updatedAtMs"], "note": note}, request.state.current_user)
            if result["status"] == "conflict":
                _error(409, "revision_conflict", "문서가 변경되어 저장하지 못했습니다.")
            return detail(note_id)

    @router.delete("/notes/{note_id}")
    def delete_note(note_id: str, request: Request, revision: int = Query(ge=0)):
        with store._store_lock():
            state, metadata, notes = snapshot()
            note, record = find_note(note_id, state, notes)
            check_revision(record, revision)
            metadata = metadata_with_active_notes(metadata, notes)
            metadata["notes"] = [item for item in metadata["notes"] if item.get("relativePath") != note["relativePath"]]
            metadata["generatedAt"] = utc_now()
            attachments = [{"relativePath": path, "noteRelativePath": note["relativePath"], "deleted": True, "lastKnownRevision": item["revision"]} for path, item in state["files"].items() if store._is_attachment_record(item) and item.get("noteRelativePath") == note["relativePath"] and not item.get("deleted")]
            result = store.sync({**context(state), "metadata": {"body": metadata, "lastKnownRevision": state["metadata"].get("revision", 0)}, "files": [{"relativePath": note["relativePath"], "deleted": True, "lastKnownRevision": revision}], "attachments": attachments}, request.state.current_user)
            if result.get("conflicts") or result.get("attachmentConflicts") or (result.get("metadata") or {}).get("status") == "conflict":
                _error(409, "revision_conflict", "문서가 변경되어 삭제하지 못했습니다.")
            return {"status": "deleted", "serverRevision": result["serverRevision"]}

    @router.post("/notes/{note_id}/attachments", status_code=201)
    def upload_attachment(note_id: str, request: Request, file: UploadFile = File(...)):
        try:
            content = file.file.read(MAX_ATTACHMENT_BYTES + 1)
        finally:
            file.file.close()
        if len(content) > MAX_ATTACHMENT_BYTES:
            _error(413, "attachment_too_large", "첨부 파일은 20 MB까지 업로드할 수 있습니다.")
        original = (file.filename or "attachment").replace("\\", "/").rsplit("/", 1)[-1]
        filename = safe_storage_file_name(original, "attachment")
        # Keep names short enough for all storage filesystems, including UTF-8.
        while len(filename.encode("utf-8")) > 200:
            stem, suffix = PurePosixPath(filename).stem, PurePosixPath(filename).suffix
            filename = (stem[:-1] + suffix) if len(stem) > 1 else filename[:-1]
        mime_type = _inline_image_type(content) or mimetypes.guess_type(filename)[0] or "application/octet-stream"
        with store._store_lock():
            state, _, notes = snapshot()
            note, record = find_note(note_id, state, notes)
            attachment_id = "att-" + uuid.uuid4().hex
            # Stable identities also avoid replacing an existing same-name upload.
            parent = str(PurePosixPath(note["relativePath"]).parent)
            path = str(PurePosixPath(parent, ".attachments", safe_storage_segment(note_id, "note"), attachment_id, filename))
            attachment = {"id": attachment_id, "fileName": filename, "relativePath": path, "noteRelativePath": note["relativePath"], "mimeType": mime_type, "size": len(content), "contentHash": hashlib.sha256(content).hexdigest(), "updatedAtMs": utc_now_ms()}
            result = store.sync_attachment_upload({**context(state), "relativePath": path, "noteRelativePath": note["relativePath"], "lastKnownRevision": 0, "contentEncoding": "base64", "content": base64.b64encode(content).decode("ascii"), "updatedAtMs": attachment["updatedAtMs"], "note": note, "attachment": attachment}, request.state.current_user)
            if result["status"] == "conflict":
                _error(409, "revision_conflict", "첨부 파일이 변경되었습니다.")
            current = detail(note_id)
            presented = next(item for item in current["note"]["attachments"] if item["id"] == attachment_id)
            label = re.sub(r"([\\\[\]])", r"\\\1", filename)
            markdown = ("!" if _inline_image_type(content) else "") + f"[{label}]({presented['markdownPath']})"
            return {"attachment": presented, "markdown": markdown, "revision": current["revision"], "serverRevision": current["serverRevision"]}

    @router.delete("/notes/{note_id}/attachments/{attachment_id}")
    def delete_attachment(note_id: str, attachment_id: str, request: Request, revision: int = Query(ge=0)):
        with store._store_lock():
            state, _, notes = snapshot()
            note, _ = find_note(note_id, state, notes)
            attachment = next((item for item in note_attachments(note, state) if item["id"] == attachment_id), None)
            if attachment is None:
                _error(404, "attachment_not_found", "첨부 파일을 찾을 수 없습니다.")
            check_revision(state["files"][attachment["relativePath"]], revision)
            store.sync_attachment_upload({**context(state), "relativePath": attachment["relativePath"], "noteRelativePath": note["relativePath"], "lastKnownRevision": revision, "deleted": True, "note": note, "attachment": attachment}, request.state.current_user)
            return {"status": "deleted", **detail(note_id)}

    @router.get("/attachments/{attachment_id}")
    def download_attachment(attachment_id: str, download: bool = False):
        with store._store_lock():
            state, metadata, notes = snapshot()
            attachment = next((attachment for attachment in all_attachments(state, metadata, notes) if attachment["id"] == attachment_id), None)
            if attachment is None:
                _error(404, "attachment_not_found", "첨부 파일을 찾을 수 없습니다.")
            payload = store.attachment_payload(attachment["relativePath"])
            content = base64.b64decode(payload["content"])
        mime_type = _inline_image_type(content)
        disposition = "inline" if mime_type and not download else "attachment"
        return Response(content, media_type=mime_type or "application/octet-stream", headers={
            "Content-Disposition": f"{disposition}; filename*=UTF-8''{quote(attachment['fileName'], safe='')}",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox",
            "Cache-Control": "private, no-store",
        })

    app.include_router(router)
