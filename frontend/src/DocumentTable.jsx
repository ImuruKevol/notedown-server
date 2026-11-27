import React from "react";
import {
  ArrowDown,
  ArrowUp,
  ChevronsUpDown,
  FileText,
  Folder,
  Paperclip,
} from "lucide-react";
const timestamp = (value) =>
  value
    ? new Date(value).toLocaleString("ko-KR", {
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
      })
    : "정보 없음";
export default function DocumentTable({
  notes,
  onOpen,
  sort,
  direction,
  onSort,
}) {
  const heading = (key, label) => (
    <th
      scope="col"
      aria-sort={
        sort === key
          ? direction === "asc"
            ? "ascending"
            : "descending"
          : "none"
      }
    >
      <button onClick={() => onSort(key)} aria-label={`${label}으로 정렬`}>
        {label}
        {sort === key ? (
          direction === "asc" ? (
            <ArrowUp size={13} />
          ) : (
            <ArrowDown size={13} />
          )
        ) : (
          <ChevronsUpDown size={13} />
        )}
      </button>
    </th>
  );
  return (
    <div
      className="document-table-scroll"
      tabIndex={0}
      role="region"
      aria-label="문서 테이블"
    >
      <table className="document-table">
        <thead>
          <tr>
            {heading("name", "이름")}
            <th scope="col">워크스페이스</th>
            {heading("created", "생성일")}
            {heading("updated", "수정일")}
            <th scope="col">첨부</th>
          </tr>
        </thead>
        <tbody>
          {notes.map((note) => (
            <tr key={note.id} data-note-id={note.id}>
              <td>
                <button
                  className="table-document-link"
                  onClick={() => onOpen(note)}
                >
                  <span className="document-icon">
                    <FileText size={19} />
                  </span>
                  <span>
                    <strong>{note.title}</strong>
                    <small>{note.excerpt || "내용이 없는 문서"}</small>
                  </span>
                </button>
              </td>
              <td>
                <span className="table-workspace">
                  <Folder size={13} />
                  {note.workspaceName || "문서"}
                </span>
              </td>
              <td
                className={`table-date ${note.createdAtMs ? "" : "unknown"}`}
                title={
                  note.createdAtMs
                    ? timestamp(note.createdAtMs)
                    : "기존 문서에 생성일 정보가 저장되어 있지 않습니다."
                }
              >
                {timestamp(note.createdAtMs)}
              </td>
              <td className="table-date">{timestamp(note.updatedAtMs)}</td>
              <td>
                <span className="table-attachment-count">
                  <Paperclip size={13} />
                  {note.attachments?.length || 0}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
