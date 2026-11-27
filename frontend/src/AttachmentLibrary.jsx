import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  ArrowDownToLine,
  ArrowUpRight,
  Check,
  File,
  FileImage,
  FileText,
  HardDrive,
  Image,
  Info,
  Link2,
  LoaderCircle,
  Paperclip,
  RefreshCw,
  Search,
  Trash2,
  TriangleAlert,
  X,
} from "lucide-react";
import "./attachment-library.css";

const formatSize = (value) => {
  const amount = Math.max(0, Number(value) || 0);
  if (amount < 1024) return `${amount.toLocaleString("ko-KR")} B`;
  if (amount < 1024 ** 2) return `${(amount / 1024).toFixed(1)} KB`;
  if (amount < 1024 ** 3) return `${(amount / 1024 ** 2).toFixed(1)} MB`;
  return `${(amount / 1024 ** 3).toFixed(1)} GB`;
};
const isImage = (file) =>
  file.isImage === true || /^image\//i.test(file.mimeType || "");
// The server sniffs the actual bytes and serves only safe raster formats inline.
const hasThumbnail = (file) => file.isImage === true;
const fileExtension = (file) =>
  file.fileName?.split(".").slice(1).pop()?.toUpperCase().slice(0, 9) || "FILE";
const fileSizeSum = (files) =>
  files.reduce((total, file) => total + (Number(file.size) || 0), 0);
const failureMessage = (error) =>
  error?.data?.message ||
  error?.message ||
  "첨부 파일을 불러오지 못했습니다. 다시 시도해 주세요.";

function Dialog({
  title,
  description,
  onClose,
  busy,
  children,
  actions,
  wide = false,
}) {
  const ref = useRef(null);
  const titleId = React.useId();
  const descriptionId = React.useId();
  useEffect(() => {
    const previous = document.activeElement;
    ref.current.showModal();
    const element = ref.current;
    return () => {
      element.close();
      if (previous instanceof HTMLElement && previous.isConnected)
        previous.focus();
    };
  }, []);
  return (
    <dialog
      ref={ref}
      className={`al-dialog${wide ? " al-dialog-wide" : ""}${actions ? " has-fixed-actions" : ""}`}
      aria-labelledby={titleId}
      aria-describedby={description ? descriptionId : undefined}
      onCancel={(event) => {
        event.preventDefault();
        if (!busy) onClose();
      }}
      onClick={(event) => {
        if (event.target === event.currentTarget && !busy) onClose();
      }}
    >
      <div className="al-dialog-inner">
        <header className="al-dialog-heading">
          <div>
            <h2 id={titleId}>{title}</h2>
            {description && <p id={descriptionId}>{description}</p>}
          </div>
          <button
            className="icon-btn"
            type="button"
            onClick={onClose}
            disabled={busy}
            aria-label="닫기"
          >
            <X size={19} />
          </button>
        </header>
        {actions ? <div className="al-dialog-body">{children}</div> : children}
        {actions}
      </div>
    </dialog>
  );
}

function Thumbnail({ file, large = false }) {
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    setFailed(false);
  }, [file.id, file.url, file.revision]);
  const Icon = isImage(file)
    ? FileImage
    : /^(text\/|application\/(json|pdf))/.test(file.mimeType || "")
      ? FileText
      : File;
  return hasThumbnail(file) && file.url && !failed ? (
    <img
      className={large ? "al-image-large" : "al-image"}
      src={file.url}
      alt={large ? file.fileName : ""}
      loading="lazy"
      onError={() => setFailed(true)}
    />
  ) : (
    <span className={`al-file-art${isImage(file) ? " al-file-art-image" : ""}`}>
      <span>
        <Icon size={large ? 45 : 34} strokeWidth={1.3} />
      </span>
      <small>{failed ? "미리보기 없음" : fileExtension(file)}</small>
    </span>
  );
}

function NoteLink({ note, onOpenNote }) {
  return (
    <button
      className="al-note-link"
      type="button"
      title={`${note.workspaceName || "문서"} · ${note.title || "제목 없는 문서"}`}
      onClick={() => onOpenNote?.(note.id)}
    >
      <FileText size={13} />
      <span>{note.title || "제목 없는 문서"}</span>
      <ArrowUpRight size={12} />
    </button>
  );
}

export default function AttachmentLibrary({
  api,
  notify,
  onOpenNote,
  onDocumentsChanged,
}) {
  const apiRef = useRef(api);
  const notifyRef = useRef(notify);
  apiRef.current = api;
  notifyRef.current = notify;
  const requestId = useRef(0);
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("all");
  const [selected, setSelected] = useState(new Set());
  const [detail, setDetail] = useState(null);
  const [cleanup, setCleanup] = useState(null);
  const [busy, setBusy] = useState(false);
  const [cleanupError, setCleanupError] = useState("");
  const [cleanupStale, setCleanupStale] = useState(false);

  const loadData = useCallback(async () => {
    const request = ++requestId.current;
    setLoading(true);
    setLoadError("");
    try {
      const result = await apiRef.current("/api/editor/attachment-library");
      if (request !== requestId.current) return;
      setData(result);
      setSelected(new Set());
    } catch (error) {
      if (request === requestId.current) setLoadError(failureMessage(error));
    } finally {
      if (request === requestId.current) setLoading(false);
    }
  }, []);
  useEffect(() => {
    loadData();
    return () => {
      requestId.current++;
    };
  }, [loadData]);

  const attachments = data?.attachments || [];
  const unreferenced = attachments.filter((file) => !file.referenced);
  const imageCount = attachments.filter(isImage).length;
  const cleanupAllowed =
    data?.cleanupAllowed === true && !loadError && !loading;
  const visibleFiles = useMemo(() => {
    const normalizedQuery = query.trim().toLocaleLowerCase();
    return (data?.attachments || [])
      .filter((file) => {
        const matchesFilter =
          filter === "all" ||
          (filter === "images" && isImage(file)) ||
          (filter === "files" && !isImage(file)) ||
          (filter === "unused" && !file.referenced);
        const searchable = [
          file.fileName,
          file.relativePath,
          file.ownerNote?.title,
          file.ownerNote?.workspaceName,
          ...(file.references || []).map(
            (note) => `${note.title} ${note.workspaceName || ""}`,
          ),
        ]
          .filter(Boolean)
          .join(" ")
          .toLocaleLowerCase();
        return (
          matchesFilter &&
          (!normalizedQuery || searchable.includes(normalizedQuery))
        );
      })
      .sort((a, b) => (a.fileName || "").localeCompare(b.fileName || "", "ko"));
  }, [data, query, filter]);
  const selectedFiles = attachments.filter(
    (file) => selected.has(file.id) && !file.referenced,
  );
  const filters = [
    ["all", "전체", attachments.length],
    ["images", "이미지", imageCount],
    ["files", "파일", attachments.length - imageCount],
    ["unused", "미사용", unreferenced.length],
  ];

  function toggleSelection(id) {
    setSelected((previous) => {
      const next = new Set(previous);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }
  function openCleanup(mode = "selected") {
    const files = mode === "all" ? unreferenced : selectedFiles;
    if (!cleanupAllowed || !files.length || busy) return;
    setCleanupError("");
    setCleanupStale(false);
    setCleanup({
      mode,
      files: [...files].sort((a, b) =>
        (a.fileName || "").localeCompare(b.fileName || "", "ko"),
      ),
      serverRevision: data.serverRevision,
    });
  }
  function openNote(noteId) {
    setDetail(null);
    onOpenNote?.(noteId);
  }
  async function runCleanup() {
    if (!cleanup || busy || cleanupStale) return;
    setBusy(true);
    setCleanupError("");
    try {
      const result = await apiRef.current("/api/editor/attachments/cleanup", {
        method: "POST",
        body: {
          serverRevision: cleanup.serverRevision,
          attachmentIds: cleanup.files.map((file) => file.id),
        },
      });
      setCleanup(null);
      setSelected(new Set());
      notifyRef.current?.(
        `미사용 첨부 파일 ${result.deletedCount ?? cleanup.files.length}개를 정리했습니다.`,
      );
      await loadData();
      try {
        await onDocumentsChanged?.();
      } catch (error) {
        notifyRef.current?.(
          `파일은 정리되었지만 문서 목록을 새로고침하지 못했습니다. ${failureMessage(error)}`,
          "error",
        );
      }
    } catch (error) {
      setCleanupError(failureMessage(error));
      if (error.status === 409 || error.status === 422) setCleanupStale(true);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section
      className="attachment-library"
      aria-labelledby="attachment-library-title"
    >
      <header className="al-page-heading">
        <div>
          <span className="al-eyebrow">ATTACHMENT LIBRARY</span>
          <h1 id="attachment-library-title">첨부 파일</h1>
          <p>문서에 담긴 이미지와 파일을 한곳에서 관리하세요.</p>
        </div>
        <button
          type="button"
          className="btn"
          onClick={loadData}
          disabled={loading || busy}
          aria-label="첨부 파일 새로고침"
        >
          <RefreshCw size={14} className={loading ? "al-spin" : ""} />
          <span>새로고침</span>
        </button>
      </header>

      {loadError && (
        <div className="al-alert al-error" role="alert">
          <TriangleAlert size={17} />
          <span>{loadError}</span>
          <button type="button" onClick={loadData} disabled={loading}>
            다시 시도
          </button>
        </div>
      )}

      {loading && !data ? (
        <div className="al-empty" role="status">
          <LoaderCircle size={25} className="al-spin" />
          <strong>첨부 파일을 불러오는 중입니다</strong>
          <p>저장된 문서의 파일 참조도 함께 확인합니다.</p>
        </div>
      ) : (
        data && (
          <>
            <div className="al-stats" aria-label="첨부 파일 현황">
              <div className="al-stat">
                <span className="al-stat-icon">
                  <Paperclip size={19} />
                </span>
                <div>
                  <span>전체 첨부 파일</span>
                  <strong>
                    {attachments.length.toLocaleString("ko-KR")}
                    <small>개</small>
                  </strong>
                </div>
              </div>
              <div className="al-stat">
                <span className="al-stat-icon al-stat-blue">
                  <HardDrive size={19} />
                </span>
                <div>
                  <span>사용 중인 용량</span>
                  <strong>{formatSize(fileSizeSum(attachments))}</strong>
                </div>
              </div>
              <div className="al-stat">
                <span className="al-stat-icon al-stat-amber">
                  <Trash2 size={18} />
                </span>
                <div>
                  <span>미사용 파일</span>
                  <strong>
                    {unreferenced.length.toLocaleString("ko-KR")}
                    <small>개 · {formatSize(fileSizeSum(unreferenced))}</small>
                  </strong>
                </div>
                <button
                  type="button"
                  className="al-stat-link"
                  onClick={() => {
                    setFilter("unused");
                    setQuery("");
                  }}
                  aria-label="미사용 파일만 보기"
                >
                  <ArrowUpRight size={17} />
                </button>
              </div>
            </div>

            {data.cleanupAllowed === false && (
              <div className="al-alert" role="status">
                <TriangleAlert size={17} />
                <div>
                  <strong>일부 문서의 참조 상태를 확인하지 못했습니다.</strong>
                  <span>
                    파일 정리를 잠시 사용할 수 없습니다. 문서 상태를 확인한 뒤
                    새로고침해 주세요.
                  </span>
                  {data.scanWarnings?.length > 0 && (
                    <details>
                      <summary>
                        확인하지 못한 문서 {data.scanWarnings.length}개
                      </summary>
                      <ul>
                        {data.scanWarnings.map((warning, index) => (
                          <li
                            key={`${warning.noteId || warning.relativePath}-${index}`}
                          >
                            {typeof warning === "string"
                              ? warning
                              : `${warning.relativePath || warning.noteId || "문서"} · ${warning.message || "확인할 수 없음"}`}
                          </li>
                        ))}
                      </ul>
                    </details>
                  )}
                </div>
              </div>
            )}

            <div className="al-toolbar">
              <div
                className="al-filters"
                role="group"
                aria-label="첨부 파일 종류"
              >
                {filters.map(([key, label, count]) => (
                  <button
                    type="button"
                    key={key}
                    className={filter === key ? "is-active" : ""}
                    aria-pressed={filter === key}
                    onClick={() => setFilter(key)}
                  >
                    {label}
                    <span>{count}</span>
                  </button>
                ))}
              </div>
            </div>

            <div className="al-selection-bar">
              <label className="al-search">
                <Search size={16} />
                <span className="al-sr-only">첨부 파일 검색</span>
                <input
                  aria-label="첨부 파일 검색"
                  type="search"
                  placeholder="파일명, 문서명 검색"
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                />
                {query && (
                  <button
                    type="button"
                    className="icon-btn"
                    aria-label="첨부 파일 검색어 지우기"
                    onClick={() => setQuery("")}
                  >
                    <X size={14} />
                  </button>
                )}
              </label>
              <div className="al-cleanup-actions">
                <button
                  type="button"
                  className="btn al-cleanup-button al-cleanup-all"
                  onClick={() => openCleanup("all")}
                  disabled={!cleanupAllowed || !unreferenced.length || busy}
                >
                  <Trash2 size={14} />
                  미사용 파일 정리
                  {unreferenced.length > 0 && (
                    <span>{unreferenced.length}</span>
                  )}
                </button>
                <button
                  type="button"
                  className="btn al-cleanup-button"
                  onClick={() => openCleanup()}
                  disabled={!cleanupAllowed || !selectedFiles.length || busy}
                >
                  <Trash2 size={14} />
                  선택한 파일 정리
                  {selectedFiles.length > 0 && (
                    <span>{selectedFiles.length}</span>
                  )}
                </button>
              </div>
              {selectedFiles.length > 0 && (
                <span className="al-selected-count" aria-live="polite">
                  {selectedFiles.length}개 선택 ·{" "}
                  {formatSize(fileSizeSum(selectedFiles))}
                  <button
                    type="button"
                    onClick={() => setSelected(new Set())}
                    disabled={busy}
                  >
                    선택 해제
                  </button>
                </span>
              )}
            </div>

            {loading && (
              <div className="al-refresh-status" role="status">
                <LoaderCircle size={13} className="al-spin" />
                파일과 참조 상태를 새로 확인하고 있습니다.
              </div>
            )}

            {visibleFiles.length ? (
              <div className="al-file-grid" aria-busy={loading}>
                {visibleFiles.map((file) => (
                  <article
                    key={file.id}
                    className={`al-file-card${selected.has(file.id) ? " is-selected" : ""}`}
                  >
                    <div
                      className={`al-file-preview${isImage(file) ? " is-image" : ""}`}
                    >
                      <button
                        type="button"
                        className="al-preview-button"
                        aria-label={`첨부 파일 자세히 보기: ${file.fileName}`}
                        onClick={() => setDetail(file)}
                      >
                        <Thumbnail file={file} />
                        <span className="al-preview-overlay">
                          자세히 보기
                          <ArrowUpRight size={14} />
                        </span>
                      </button>
                      {!file.referenced && (
                        <label className="al-file-selection">
                          <input
                            type="checkbox"
                            aria-label={`${file.fileName} 정리 대상으로 선택`}
                            checked={selected.has(file.id)}
                            onChange={() => toggleSelection(file.id)}
                            disabled={!cleanupAllowed || busy}
                          />
                        </label>
                      )}
                      <span
                        className={`al-reference-badge${file.referenced ? " is-used" : ""}`}
                      >
                        {file.referenced ? (
                          <>
                            <Link2 size={11} />
                            사용 중
                          </>
                        ) : (
                          "미사용"
                        )}
                      </span>
                    </div>
                    <div className="al-file-content">
                      <button
                        type="button"
                        className="al-file-title"
                        title={file.fileName}
                        onClick={() => setDetail(file)}
                      >
                        {file.fileName}
                      </button>
                      <div className="al-file-metadata">
                        <span>{fileExtension(file)}</span>
                        <span>{formatSize(file.size)}</span>
                        {file.referenced && (
                          <span>
                            {file.references?.length || 1}개 문서에서 참조
                          </span>
                        )}
                      </div>
                      <div className="al-file-owner">
                        <span>원본 문서</span>
                        {file.ownerNote ? (
                          <NoteLink
                            note={file.ownerNote}
                            onOpenNote={openNote}
                          />
                        ) : (
                          <span className="al-owner-missing">
                            원본 문서 없음
                          </span>
                        )}
                      </div>
                    </div>
                    <footer className="al-file-footer">
                      <span>
                        {file.ownerNote?.workspaceName || "첨부 파일"}
                      </span>
                      <a
                        href={file.url}
                        download={file.fileName}
                        className="al-download"
                        aria-label={`${file.fileName} 다운로드`}
                      >
                        <ArrowDownToLine size={14} />
                        다운로드
                      </a>
                    </footer>
                  </article>
                ))}
              </div>
            ) : (
              <div className="al-empty">
                <span className="al-empty-icon">
                  {filter === "unused" && !query ? (
                    <Check size={25} />
                  ) : query ? (
                    <Search size={25} />
                  ) : (
                    <Image size={25} />
                  )}
                </span>
                <strong>
                  {query
                    ? "검색 결과가 없습니다"
                    : filter === "unused"
                      ? "정리할 미사용 파일이 없습니다"
                      : attachments.length
                        ? "이 종류의 첨부 파일이 없습니다"
                        : "아직 첨부 파일이 없습니다"}
                </strong>
                <p>
                  {query
                    ? "다른 파일명이나 문서명으로 검색해 보세요."
                    : filter === "unused"
                      ? "모든 첨부 파일이 저장된 문서에서 사용되고 있습니다."
                      : "문서 편집 화면에서 이미지나 파일을 업로드할 수 있습니다."}
                </p>
                {query && (
                  <button
                    type="button"
                    className="btn"
                    onClick={() => setQuery("")}
                  >
                    검색어 지우기
                  </button>
                )}
              </div>
            )}
            <p className="al-results-count" aria-live="polite">
              전체 {attachments.length}개 중 {visibleFiles.length}개 표시
            </p>
          </>
        )
      )}

      {detail && (
        <Dialog
          title={detail.fileName}
          description={`${formatSize(detail.size)} · ${detail.mimeType || "첨부 파일"}`}
          onClose={() => setDetail(null)}
          wide
        >
          <div
            className={`al-detail-preview${hasThumbnail(detail) ? " is-image" : ""}`}
          >
            <Thumbnail file={detail} large />
          </div>
          <dl className="al-detail-metadata">
            <div>
              <dt>파일 경로</dt>
              <dd className="al-file-path">{detail.relativePath}</dd>
            </div>
            <div>
              <dt>원본 문서</dt>
              <dd>
                {detail.ownerNote ? (
                  <NoteLink note={detail.ownerNote} onOpenNote={openNote} />
                ) : (
                  "원본 문서 없음"
                )}
              </dd>
            </div>
            <div>
              <dt>참조 문서</dt>
              <dd>
                {detail.references?.length ? (
                  <div className="al-reference-list">
                    {detail.references.map((note) => (
                      <NoteLink
                        key={note.id}
                        note={note}
                        onOpenNote={openNote}
                      />
                    ))}
                  </div>
                ) : (
                  <span className="al-unused-description">
                    저장된 문서에서 사용하지 않는 파일입니다.
                  </span>
                )}
              </dd>
            </div>
          </dl>
          <footer className="al-dialog-actions">
            <button
              type="button"
              className="btn"
              onClick={() => setDetail(null)}
            >
              닫기
            </button>
            <a
              href={detail.url}
              download={detail.fileName}
              className="btn primary"
            >
              <ArrowDownToLine size={14} />
              다운로드
            </a>
          </footer>
        </Dialog>
      )}

      {cleanup && (
        <Dialog
          title={
            cleanup.mode === "all"
              ? "미사용 파일을 정리할까요?"
              : "선택한 미사용 파일을 정리할까요?"
          }
          description={
            cleanup.mode === "all"
              ? "검색·종류 필터와 관계없이 전체 미사용 파일을 확인합니다."
              : "선택한 미사용 파일을 확인한 뒤 정리해 주세요."
          }
          onClose={() => {
            if (!busy) setCleanup(null);
          }}
          busy={busy}
          actions={
            <footer className="al-dialog-actions">
              <button
                type="button"
                className="btn"
                disabled={busy}
                onClick={() => setCleanup(null)}
              >
                취소
              </button>
              {cleanupStale ? (
                <button
                  type="button"
                  className="btn primary"
                  onClick={() => {
                    setCleanup(null);
                    loadData();
                  }}
                >
                  <RefreshCw size={14} />
                  목록 다시 확인
                </button>
              ) : (
                <button
                  type="button"
                  className="btn danger"
                  disabled={busy}
                  onClick={runCleanup}
                >
                  {busy ? (
                    <LoaderCircle className="al-spin" size={14} />
                  ) : (
                    <Trash2 size={14} />
                  )}
                  {busy ? "정리 중…" : `${cleanup.files.length}개 파일 삭제`}
                </button>
              )}
            </footer>
          }
        >
          <div className="al-cleanup-summary" aria-label="정리 대상 요약">
            <div>
              <span>정리 대상</span>
              <strong>
                {cleanup.files.length.toLocaleString("ko-KR")}개 파일
              </strong>
            </div>
            <div>
              <span>총용량</span>
              <strong>{formatSize(fileSizeSum(cleanup.files))}</strong>
            </div>
          </div>
          <div className="al-cleanup-notice">
            <Info size={18} />
            <p>
              아래 파일은 <strong>저장된 문서에서 참조하지 않습니다.</strong>{" "}
              다른 편집 화면에 저장하지 않은 첨부 링크가 있다면 먼저 저장해
              주세요.
            </p>
          </div>
          <ul className="al-cleanup-list">
            {cleanup.files.map((file) => (
              <li key={file.id}>
                <span className="al-cleanup-file-icon">
                  {isImage(file) ? (
                    <FileImage size={18} />
                  ) : (
                    <FileText size={18} />
                  )}
                </span>
                <div>
                  <strong>{file.fileName}</strong>
                  <span title={file.relativePath}>
                    {file.ownerNote
                      ? [file.ownerNote.workspaceName, file.ownerNote.title]
                          .filter(Boolean)
                          .join(" · ")
                      : "원본 문서 없음"}
                  </span>
                </div>
                <small>{formatSize(file.size)}</small>
              </li>
            ))}
          </ul>
          <p className="al-deletion-note">
            정리하면 위 목록의 파일이 저장소에서 삭제됩니다.
          </p>
          {cleanupError && (
            <div className="al-alert al-error al-cleanup-error" role="alert">
              <TriangleAlert size={17} />
              <div>
                <strong>파일 정리를 완료하지 못했습니다.</strong>
                <span>{cleanupError}</span>
                {cleanupStale && (
                  <span>
                    목록을 새로 불러와 참조 상태와 선택한 파일을 다시 확인해
                    주세요.
                  </span>
                )}
              </div>
            </div>
          )}
        </Dialog>
      )}
    </section>
  );
}
