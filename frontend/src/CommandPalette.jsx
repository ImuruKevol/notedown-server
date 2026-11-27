import React, { useEffect, useId, useMemo, useRef, useState } from "react";
import { ArrowDown, ArrowUp, CornerDownLeft, FileText, Folder, Search } from "lucide-react";
import { matchingSegments, searchDocuments } from "./document-search";
import "./command-palette.css";

function Highlight({ text, query }) {
  return matchingSegments(text, query).map((part, index) =>
    part.match ? <mark key={index}>{part.text}</mark> : part.text,
  );
}

export default function CommandPalette({ notes, currentNoteId, onOpen, onClose }) {
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const dialogRef = useRef(null), inputRef = useRef(null), selectedRef = useRef(null);
  const id = useId();
  const results = useMemo(() => searchDocuments(notes, query), [notes, query]);
  const selected = Math.min(active, Math.max(0, results.length - 1));
  useEffect(() => {
    const previous = document.activeElement;
    const dialog = dialogRef.current;
    dialog.showModal();
    inputRef.current.focus();
    return () => {
      dialog.close();
      if (previous?.isConnected) previous.focus();
    };
  }, []);
  useEffect(() => {
    selectedRef.current?.scrollIntoView({ block: "nearest" });
  }, [selected, results]);

  return (
    <dialog
      ref={dialogRef}
      className="command-palette"
      aria-labelledby={`${id}-title`}
      onCancel={(event) => { event.preventDefault(); onClose(); }}
      onClick={(event) => { if (event.target === event.currentTarget) onClose(); }}
    >
      <div className="palette-content">
        <div className="palette-heading">
          <h2 id={`${id}-title`}>문서 빠른 열기</h2>
          <span>모든 워크스페이스</span>
        </div>
        <div className="palette-search">
          <Search size={20} aria-hidden="true" />
          <input
            ref={inputRef}
            role="combobox"
            aria-label="워크스페이스 또는 문서 이름 검색"
            aria-expanded="true"
            aria-autocomplete="list"
            aria-controls={`${id}-results`}
            aria-activedescendant={results.length ? `${id}-option-${selected}` : undefined}
            aria-describedby={`${id}-hint`}
            autoComplete="off"
            spellCheck={false}
            placeholder="문서·워크스페이스 검색…"
            value={query}
            onChange={(event) => { setQuery(event.target.value); setActive(0); }}
            onKeyDown={(event) => {
              if (event.nativeEvent.isComposing || event.keyCode === 229) return;
              if (event.key === "ArrowDown" || event.key === "ArrowUp") {
                event.preventDefault();
                if (results.length)
                  setActive((selected + (event.key === "ArrowDown" ? 1 : -1) + results.length) % results.length);
              } else if (event.key === "Enter") {
                event.preventDefault();
                if (results[selected]) onOpen(results[selected]);
              }
            }}
          />
          <button className="palette-dismiss" onClick={onClose} aria-label="검색 닫기">
            <kbd>Esc</kbd>
          </button>
        </div>
        <div className="palette-summary" role="status" aria-live="polite" aria-atomic="true">
          <span>{query.trim() ? "검색 결과 · 일치도순" : "최근 수정한 문서"}</span>
          <span>{results.length}개 문서</span>
        </div>
        <div className="palette-results" id={`${id}-results`} role="listbox" aria-label="문서 검색 결과">
          {results.map((note, index) => (
            <div
              key={note.id}
              ref={index === selected ? selectedRef : null}
              id={`${id}-option-${index}`}
              role="option"
              aria-selected={index === selected}
              data-note-id={note.id}
              className={`palette-option ${index === selected ? "active" : ""}`}
              onPointerMove={() => setActive(index)}
              onMouseDown={(event) => event.preventDefault()}
              onClick={() => onOpen(note)}
            >
              <span className="palette-file-icon"><FileText size={19} aria-hidden="true" /></span>
              <div className="palette-note">
                <strong title={note.title}><Highlight text={note.title} query={query} /></strong>
                <span title={note.workspaceName}><Folder size={12} aria-hidden="true" /><span><Highlight text={note.workspaceName || "워크스페이스"} query={query} /></span></span>
              </div>
              {note.id === currentNoteId && <span className="palette-current">현재 문서</span>}
              <CornerDownLeft className="palette-enter" size={15} aria-hidden="true" />
            </div>
          ))}
        </div>
        {!results.length && (
          <div className="palette-empty">
            <Search size={27} aria-hidden="true" />
            <strong>{query.trim() ? "일치하는 문서가 없어요" : "아직 문서가 없어요"}</strong>
            <p>{query.trim() ? "워크스페이스나 문서 이름을 바꿔 검색해 보세요." : "문서를 만들면 이곳에서 바로 찾을 수 있어요."}</p>
          </div>
        )}
        <footer className="palette-footer">
          <span id={`${id}-hint`}>공백으로 구분하면 모두 포함된 문서를 찾아요</span>
          <div className="palette-keys" aria-hidden="true">
            <span><kbd><ArrowUp size={11} /><ArrowDown size={11} /></kbd> 이동</span>
            <span><kbd><CornerDownLeft size={12} /></kbd> 열기</span>
          </div>
        </footer>
      </div>
    </dialog>
  );
}
