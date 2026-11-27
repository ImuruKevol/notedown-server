import React, {
  useState,
  useEffect,
  useRef,
  useCallback,
  useMemo,
} from "react";
import { createRoot } from "react-dom/client";
import Editor, { loader } from "@monaco-editor/react";
import * as monaco from "monaco-editor/esm/vs/editor/editor.api";
import "monaco-editor/esm/vs/basic-languages/markdown/markdown.contribution";
import EditorWorker from "monaco-editor/esm/vs/editor/editor.worker?worker";
import MarkdownIt from "markdown-it";
import taskLists from "markdown-it-task-lists";
import DOMPurify from "dompurify";
import hljs from "highlight.js/lib/common";
import {
  BookOpen,
  FileText,
  Folder,
  Plus,
  Search,
  Settings2,
  LogOut,
  ArrowUpRight,
  ArrowUp,
  ArrowDown,
  ChevronRight,
  PanelLeft,
  Columns2,
  Code2,
  Eye,
  Save,
  Check,
  MoreHorizontal,
  Paperclip,
  ImagePlus,
  Download,
  Trash2,
  X,
  ArrowLeft,
  Bold,
  Italic,
  Heading2,
  Link,
  Quote,
  List,
  CheckSquare,
  Code,
  RefreshCw,
  Command,
  Cloud,
  Menu,
  TriangleAlert,
  Loader2,
  ArrowRight,
  FilePlus2,
  FolderPlus,
  LayoutGrid,
  Rows3,
  Clock3,
} from "lucide-react";
import AdminPanel from "./AdminPanel";
import AttachmentLibrary from "./AttachmentLibrary";
import DocumentTable from "./DocumentTable";
import PwaInstall from "./PwaInstall";
import CommandPalette from "./CommandPalette";
import { registerPwa } from "./pwa";
import { resolveAttachment } from "./attachment-paths";
import { searchTerms } from "./document-search";
import {
  compareNotes,
  readLibraryPreferences,
  saveLibraryPreferences,
} from "./library-preferences";
import { annotateMarkdownSource, useScrollSync } from "./scroll-sync";
import "./style.css";
self.MonacoEnvironment = { getWorker: () => new EditorWorker() };
loader.config({ monaco });
const md = new MarkdownIt({
  html: false,
  linkify: true,
  breaks: true,
  highlight: (str, lang) =>
    lang && hljs.getLanguage(lang)
      ? hljs.highlight(str, { language: lang }).value
      : "",
}).use(taskLists);
annotateMarkdownSource(md);
const errorText = {
  invalid_credentials: "아이디 또는 비밀번호를 확인해 주세요.",
  password_too_short: "비밀번호는 8자 이상으로 입력해 주세요.",
  revision_conflict: "다른 기기에서 문서가 변경되었습니다.",
  missing_bearer_token: "다시 로그인해 주세요.",
};
async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  let body = options.body;
  if (body && !(body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
    body = JSON.stringify(body);
  }
  const r = await fetch(path, {
    ...options,
    headers,
    body,
    credentials: "same-origin",
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) {
    const e = new Error(
      errorText[data.error] ||
        data.message ||
        data.error ||
        "요청을 처리하지 못했습니다.",
    );
    e.status = r.status;
    e.data = data;
    throw e;
  }
  return data;
}
const date = (v) =>
  v
    ? new Date(v).toLocaleDateString("ko-KR", {
        month: "short",
        day: "numeric",
      })
    : "방금 전";
const bytes = (v) =>
  v > 1048576
    ? `${(v / 1048576).toFixed(1)} MB`
    : v > 1024
      ? `${Math.round(v / 1024)} KB`
      : `${v || 0} B`;
const paletteShortcut = /Mac|iPhone|iPad/.test(navigator.platform) ? "⌘ P" : "Ctrl P";
function Logo() {
  return (
    <div className="brand">
      <span className="brand-mark">
        <img src="/icons/notedown.svg" alt="" width="34" height="34" />
      </span>
      <span>
        notedown<span className="brand-dot">.</span>
      </span>
    </div>
  );
}
function Modal({ title, children, onClose, wide = false }) {
  const ref = useRef();
  const titleId = React.useId();
  useEffect(() => {
    const previous = document.activeElement;
    const dialog = ref.current;
    dialog.showModal();
    const node = dialog.querySelector("input,textarea,button");
    node?.focus();
    return () => {
      dialog.close();
      previous?.focus();
    };
  }, []);
  return (
    <dialog
      ref={ref}
      aria-labelledby={titleId}
      className={`modal ${wide ? "wide" : ""}`}
      onCancel={(e) => {
        e.preventDefault();
        onClose();
      }}
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="modal-heading">
        <h2 id={titleId}>{title}</h2>
        <button className="icon-btn" aria-label="닫기" onClick={onClose}>
          <X size={19} />
        </button>
      </div>
      {children}
    </dialog>
  );
}
function Auth({ setup, onDone }) {
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  return (
    <div className="auth-page">
      <div className="auth-brand">
        <Logo />
      </div>
      <div className="auth-story">
        <span className="eyebrow">YOUR SPACE FOR THOUGHTS</span>
        <h1>
          생각을 기록하고,
          <br />
          기록을 이어가세요<span>.</span>
        </h1>
        <p>
          작은 메모부터 다음 프로젝트까지.
          <br />
          모든 문서가 한곳에서 연결되는 나만의 공간.
        </p>
        <div className="story-note">
          <div className="story-note-top">
            <span className="tiny-dot" /> my next idea.md <span>Markdown</span>
          </div>
          <h3>
            <span>#</span> 일단, 적어두기
          </h3>
          <p>좋은 생각은 기록하는 순간 시작되니까.</p>
          <div className="story-line">
            <CheckSquare size={17} /> 아이디어를 문서로 정리하기
          </div>
          <div className="story-line">
            <CheckSquare size={17} /> 이미지와 파일로 더 풍부하게
          </div>
          <div className="story-footer">
            <Cloud size={15} /> 어느 기기에서든 이어지는 기록
          </div>
        </div>
        <div className="auth-caption">WRITE. PREVIEW. CONNECT.</div>
      </div>
      <div className="auth-form-wrap">
        <form
          className="auth-form"
          onSubmit={async (e) => {
            e.preventDefault();
            setBusy(true);
            setError("");
            const values = Object.fromEntries(new FormData(e.currentTarget));
            if (setup && values.password !== values.confirm) {
              setError("비밀번호가 일치하지 않습니다.");
              setBusy(false);
              return;
            }
            try {
              await api(setup ? "/api/setup" : "/api/login", {
                method: "POST",
                body: values,
              });
              onDone();
            } catch (e) {
              setError(e.message);
            } finally {
              setBusy(false);
            }
          }}
        >
          <div className="auth-icon">
            <BookOpen size={25} />
          </div>
          <span className="eyebrow">NOTEDOWN WORKSPACE</span>
          <h2>{setup ? "나의 공간 시작하기" : "다시 만나 반가워요"}</h2>
          <p>
            {setup
              ? "관리자 계정을 만들고 첫 문서를 작성해 보세요."
              : "계정에 로그인하고, 쓰던 이야기를 이어가세요."}
          </p>
          <label>
            아이디
            <input
              name="username"
              autoComplete="username"
              required
              placeholder="아이디를 입력하세요"
              autoFocus
            />
          </label>
          <label>
            비밀번호
            <input
              name="password"
              type="password"
              autoComplete={setup ? "new-password" : "current-password"}
              minLength={setup ? 8 : undefined}
              required
              placeholder={
                setup ? "8자 이상 입력하세요" : "비밀번호를 입력하세요"
              }
            />
          </label>
          {setup && (
            <label>
              비밀번호 확인
              <input
                name="confirm"
                type="password"
                autoComplete="new-password"
                minLength={8}
                required
                placeholder="비밀번호를 한 번 더 입력하세요"
              />
            </label>
          )}
          {error && (
            <div className="form-error" role="alert">
              {error}
            </div>
          )}
          <button className="btn primary auth-submit" disabled={busy}>
            {busy ? (
              <Loader2 className="spin" size={18} />
            ) : (
              <>
                {setup ? "워크스페이스 만들기" : "로그인"}
                <ArrowRight size={17} />
              </>
            )}
          </button>
          <div className="auth-hint">
            <span className="status-dot" /> 나의 서버에 안전하게 보관되는 문서
          </div>
          <PwaInstall />
        </form>
      </div>
      <footer className="auth-footer">
        Notedown <span>생각이 머무는 공간</span>
      </footer>
    </div>
  );
}
function App() {
  const [auth, setAuth] = useState(null),
    [initialError, setInitialError] = useState(""),
    [library, setLibrary] = useState({ workspaces: [], notes: [] }),
    [view, setView] = useState("documents"),
    [workspace, setWorkspace] = useState("all"),
    [workspaceFilter, setWorkspaceFilter] = useState(""),
    [query, setQuery] = useState(""),
    [sort, setSort] = useState(() => readLibraryPreferences().sort),
    [sortDirection, setSortDirection] = useState(
      () => readLibraryPreferences().direction,
    ),
    [libraryLayout, setLibraryLayout] = useState(
      () => readLibraryPreferences().layout,
    ),
    [attachmentRefresh, setAttachmentRefresh] = useState(0),
    [scrollEditor, setScrollEditor] = useState(null),
    [detail, setDetail] = useState(null),
    [content, setContent] = useState(""),
    [title, setTitle] = useState(""),
    [mode, setMode] = useState("split"),
    [busy, setBusy] = useState(false),
    [loadingNote, setLoadingNote] = useState(false),
    [uploading, setUploading] = useState(false),
    [modal, setModal] = useState(null),
    [toast, setToast] = useState(null),
    [attachmentsOpen, setAttachmentsOpen] = useState(false),
    [sidebarOpen, setSidebarOpen] = useState(false),
    [paletteOpen, setPaletteOpen] = useState(false),
    [conflict, setConflict] = useState(null),
    [modalError, setModalError] = useState(""),
    [connected, setConnected] = useState(true);
  const editorRef = useRef(null),
    previewRef = useRef(null),
    fileRef = useRef(null),
    workspaceFilterRef = useRef(null),
    actionRef = useRef(null),
    requestId = useRef(0),
    toastTimer = useRef(),
    draftTimer = useRef();
  const dirty =
    !!detail && (content !== detail.content || title !== detail.note.title);
  const notify = useCallback((message, type = "success") => {
    setModalError(type === "error" ? message : "");
    setToast({ message, type });
    clearTimeout(toastTimer.current);
    toastTimer.current = setTimeout(() => setToast(null), 4800);
  }, []);
  const refresh = useCallback(async () => {
    try {
      const data = await api("/api/editor/library");
      setLibrary(data);
      setConnected(true);
      return data;
    } catch (e) {
      setConnected(false);
      throw e;
    }
  }, []);
  const boot = useCallback(async () => {
    try {
      const status = await api("/api/setup/status");
      if (!status.configured) {
        setAuth({ setup: true });
        return;
      }
      try {
        const account = await api("/api/admin/account");
        setAuth({ account });
        await refresh();
      } catch (e) {
        if (e.status === 401) setAuth({ setup: false });
        else throw e;
      }
    } catch (e) {
      setInitialError(e.message);
    }
  }, [refresh]);
  useEffect(() => {
    boot();
  }, [boot]);
  useEffect(() => {
    setModalError("");
  }, [modal?.kind]);
  useEffect(() => {
    const health = () =>
      api("/api/health")
        .then(() => setConnected(true))
        .catch(() => setConnected(false));
    const offline = () => setConnected(false);
    window.addEventListener("offline", offline);
    window.addEventListener("online", health);
    const timer = setInterval(health, 30000);
    return () => {
      clearInterval(timer);
      window.removeEventListener("offline", offline);
      window.removeEventListener("online", health);
    };
  }, []);
  useEffect(() => {
    const handler = (e) => {
      if (dirty) {
        e.preventDefault();
        e.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [dirty]);
  const draftKey = detail
    ? `notedown-draft:${auth?.account?.username}:${detail.note.id}`
    : null;
  useEffect(() => {
    if (!draftKey || !dirty) return;
    const id = setTimeout(() => {
      try {
        localStorage.setItem(
          draftKey,
          JSON.stringify({ title, content, revision: detail.revision }),
        );
      } catch {}
    }, 600);
    draftTimer.current = id;
    return () => clearTimeout(id);
  }, [content, title, dirty, draftKey, detail?.revision]);
  const guard = (fn) => {
    if (busy || uploading || loadingNote) {
      notify("문서를 불러오거나 저장하는 작업이 끝난 뒤 이동해 주세요.");
      return;
    }
    if (dirty) {
      actionRef.current = fn;
      setModal({ kind: "unsaved" });
    } else fn();
  };
  async function openNote(note) {
    const id = ++requestId.current;
    setLoadingNote(true);
    setSidebarOpen(false);
    setConflict(null);
    try {
      const d = await api(`/api/editor/notes/${encodeURIComponent(note.id)}`);
      if (id !== requestId.current) return;
      setDetail(d);
      setContent(d.content);
      setTitle(d.note.title);
      setView("documents");
      setAttachmentsOpen(false);
      try {
        const raw = localStorage.getItem(
          `notedown-draft:${auth.account.username}:${d.note.id}`,
        );
        if (raw) {
          const draft = JSON.parse(raw);
          if (draft.content !== d.content || draft.title !== d.note.title)
            setModal({ kind: "draft", draft });
        }
      } catch {}
    } catch (e) {
      notify(e.message, "error");
    } finally {
      if (id === requestId.current) setLoadingNote(false);
    }
  }
  const clearDraft = () => {
    clearTimeout(draftTimer.current);
    if (draftKey)
      try {
        localStorage.removeItem(draftKey);
      } catch {}
  };
  async function save() {
    if (!detail || busy || !dirty || uploading) return;
    if (!title.trim()) {
      notify("문서 제목을 입력해 주세요.", "error");
      return false;
    }
    const snapshot = { title, content };
    setBusy(true);
    try {
      const data = await api(
        `/api/editor/notes/${encodeURIComponent(detail.note.id)}`,
        { method: "PUT", body: { ...snapshot, revision: detail.revision } },
      );
      setDetail(data);
      setTitle(data.note.title);
      clearDraft();
      await refresh();
      notify("문서를 저장했습니다.");
      return true;
    } catch (e) {
      if (e.status === 409) {
        setConflict(e.data);
        notify(
          "저장 충돌이 발생했습니다. 작성 중인 내용은 유지됩니다.",
          "error",
        );
      } else notify(e.message, "error");
      return false;
    } finally {
      setBusy(false);
    }
  }
  useEffect(() => {
    const key = (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "s") {
        e.preventDefault();
        save();
      }
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  });
  const openPalette = useCallback(() => {
    if (!auth?.account || document.querySelector("dialog[open]:not(.command-palette)")) return;
    setPaletteOpen(true);
  }, [auth?.account]);
  useEffect(() => {
    const key = (event) => {
      if (!auth?.account || event.isComposing || event.altKey || event.shiftKey) return;
      if ((event.metaKey || event.ctrlKey) && (event.code === "KeyP" || event.key.toLowerCase() === "p")) {
        event.preventDefault();
        event.stopPropagation();
        openPalette();
      }
    };
    // Capture before Monaco so quick-open also works while editing Markdown.
    window.addEventListener("keydown", key, true);
    return () => window.removeEventListener("keydown", key, true);
  }, [auth?.account, openPalette]);
  useEffect(
    () =>
      saveLibraryPreferences({
        sort,
        direction: sortDirection,
        layout: libraryLayout,
      }),
    [sort, sortDirection, libraryLayout],
  );
  const changeTableSort = (key) => {
    if (key === sort)
      setSortDirection((value) => (value === "asc" ? "desc" : "asc"));
    else {
      setSort(key);
      setSortDirection(key === "name" ? "asc" : "desc");
    }
  };
  const filtered = useMemo(
    () =>
      library.notes
        .filter(
          (n) =>
            (workspace === "all" || n.workspace === workspace) &&
            `${n.title} ${n.workspaceName} ${n.relativePath}`
              .toLowerCase()
              .includes(query.toLowerCase()),
        )
        .sort((a, b) => compareNotes(a, b, sort, sortDirection)),
    [library, workspace, query, sort, sortDirection],
  );
  const matchingWorkspaces = useMemo(() => {
    const terms = searchTerms(workspaceFilter);
    return library.workspaces.flatMap((entry, index) => {
      const name = (entry.name || entry.id).normalize("NFC").toLowerCase();
      return terms.every((term) => name.includes(term))
        ? [{ entry, color: index % 4 }]
        : [];
    });
  }, [library.workspaces, workspaceFilter]);
  const workspaceName =
    workspace === "all"
      ? "모든 문서"
      : library.workspaces.find((w) => w.id === workspace)?.name ||
        "워크스페이스";
  function insert(before, after = "") {
    if (busy) return;
    const editor = editorRef.current;
    if (editor && mode !== "preview") {
      const selection = editor.getSelection();
      const selected = editor.getModel().getValueInRange(selection);
      editor.executeEdits("toolbar", [
        {
          range: selection,
          text: before + selected + after,
          forceMoveMarkers: true,
        },
      ]);
      editor.focus();
    } else setContent((c) => c + (c ? "\n" : "") + before + after);
  }
  async function upload(files) {
    if (!detail || uploading || busy) return;
    setUploading(true);
    try {
      for (const file of Array.from(files)) {
        if (file.size > 20 * 1024 * 1024)
          throw new Error("첨부 파일은 20 MB 이하로 업로드해 주세요.");
        const data = new FormData();
        data.append("file", file);
        const result = await api(
          `/api/editor/notes/${encodeURIComponent(detail.note.id)}/attachments`,
          { method: "POST", body: data },
        );
        setDetail((d) => ({
          ...d,
          note: {
            ...d.note,
            attachments: [...(d.note.attachments || []), result.attachment],
          },
        }));
        insert(`\n${result.markdown}\n`);
      }
      notify("첨부 파일을 추가했습니다. 문서를 저장해 링크를 반영하세요.");
      await refresh();
    } catch (e) {
      notify(e.message, "error");
    } finally {
      setUploading(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  }
  function exportMarkdown() {
    const blob = new Blob([content], { type: "text/markdown;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = (title || "제목 없는 문서") + ".md";
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  const rendered = useMemo(() => {
    let html = md.render(content);
    const doc = new DOMParser().parseFromString(
      DOMPurify.sanitize(html),
      "text/html",
    );
    for (const node of doc.querySelectorAll("img,a")) {
      const attr = node.tagName === "IMG" ? "src" : "href";
      let value = node.getAttribute(attr) || "";
      const attachment = resolveAttachment(
        value,
        detail?.note.relativePath || "",
        [
          ...(detail?.note.attachments || []),
          ...(library.attachments || []),
          ...library.notes.flatMap((note) => note.attachments || []),
        ],
      );
      if (attachment) node.setAttribute(attr, attachment.url);
      if (node.tagName === "A") {
        node.setAttribute("target", "_blank");
        node.setAttribute("rel", "noopener noreferrer");
      }
    }
    return DOMPurify.sanitize(doc.body.innerHTML, { ADD_ATTR: ["target"] });
  }, [
    content,
    detail?.note.attachments,
    detail?.note.relativePath,
    library.notes,
    library.attachments,
  ]);
  useScrollSync({
    editor: scrollEditor,
    previewRef,
    enabled: !!detail && view === "documents" && mode === "split",
    contentKey: rendered,
  });
  if (initialError)
    return (
      <div className="loading-screen">
        <TriangleAlert />
        <h2>서버에 연결하지 못했습니다</h2>
        <p>{initialError}</p>
        <button
          className="btn primary"
          onClick={() => {
            setInitialError("");
            boot();
          }}
        >
          다시 연결
        </button>
      </div>
    );
  if (!auth)
    return (
      <div className="loading-screen">
        <Logo />
        <Loader2 className="spin" />
        <p>나의 문서 공간을 열고 있어요.</p>
      </div>
    );
  if (!auth.account) return <Auth setup={auth.setup} onDone={boot} />;
  return (
    <div className="app-shell">
      {sidebarOpen && (
        <div className="sidebar-scrim" onClick={() => setSidebarOpen(false)} />
      )}
      <aside className={`sidebar ${sidebarOpen ? "open" : ""}`}>
        <Logo />
        <div className="workspace-label">PERSONAL WORKSPACE</div>
        <button
          className={`nav-item ${view === "documents" && workspace === "all" ? "active" : ""}`}
          onClick={() =>
            guard(() => {
              setView("documents");
              setWorkspace("all");
              setDetail(null);
              setSidebarOpen(false);
            })
          }
        >
          <BookOpen size={18} /> 문서 라이브러리{" "}
          <span className="count">{library.notes.length}</span>
        </button>
        <button
          className={`nav-item ${view === "attachments" ? "active" : ""}`}
          onClick={() =>
            guard(() => {
              setView("attachments");
              setDetail(null);
              setSidebarOpen(false);
            })
          }
        >
          <Paperclip size={18} /> 첨부 라이브러리
        </button>
        <div className="section-caption">
          워크스페이스
          <button
            className="icon-btn"
            aria-label="워크스페이스 추가"
            onClick={() => guard(() => setModal({ kind: "workspace" }))}
          >
            <Plus size={15} />
          </button>
        </div>
        <div className="workspace-filter">
          <Search size={15} aria-hidden="true" />
          <input
            ref={workspaceFilterRef}
            type="search"
            value={workspaceFilter}
            onChange={(event) => setWorkspaceFilter(event.target.value)}
            placeholder="워크스페이스 필터"
            aria-label="워크스페이스 필터"
            aria-controls="workspace-list"
          />
          {workspaceFilter && (
            <button
              type="button"
              className="icon-btn"
              aria-label="워크스페이스 필터 지우기"
              onClick={() => {
                setWorkspaceFilter("");
                workspaceFilterRef.current?.focus();
              }}
            >
              <X size={15} aria-hidden="true" />
            </button>
          )}
        </div>
        <div className="workspace-nav" id="workspace-list">
          {matchingWorkspaces.map(({ entry: w, color }) => (
            <button
              key={w.id}
              title={w.name}
              className={`nav-item ${workspace === w.id && view === "documents" ? "active" : ""}`}
              onClick={() =>
                guard(() => {
                  setWorkspace(w.id);
                  setView("documents");
                  setDetail(null);
                  setSidebarOpen(false);
                })
              }
            >
              <span className={`folder-icon color-${color}`}>
                <Folder size={16} />
              </span>
              <span className="truncate">{w.name}</span>
              <span className="workspace-count">
                {library.notes.filter((n) => n.workspace === w.id).length}
              </span>
            </button>
          ))}
          {!library.workspaces.length && (
            <p className="sidebar-empty">
              워크스페이스를 만들어
              <br />
              기록을 시작하세요.
            </p>
          )}
          {!!library.workspaces.length && !matchingWorkspaces.length && (
            <p className="sidebar-empty" role="status">
              일치하는 워크스페이스가 없습니다.
            </p>
          )}
        </div>
        <button
          className="add-workspace"
          onClick={() => guard(() => setModal({ kind: "workspace" }))}
        >
          <Plus size={16} /> 새 워크스페이스
        </button>
        <div className="sidebar-bottom">
          <button
            className={`nav-item ${view === "admin" ? "active" : ""}`}
            onClick={() =>
              guard(() => {
                setView("admin");
                setDetail(null);
                setSidebarOpen(false);
              })
            }
          >
            <Settings2 size={18} /> 서버 관리
            <ArrowUpRight size={14} className="push-right" />
          </button>
          <div className="account">
            <div className="avatar">
              {auth.account.username.slice(0, 1).toUpperCase()}
            </div>
            <div>
              <strong>{auth.account.username}</strong>
              <span>개인 워크스페이스</span>
            </div>
            <button
              className="icon-btn push-right"
              title="로그아웃"
              aria-label="로그아웃"
              onClick={() =>
                guard(async () => {
                  await api("/api/logout", { method: "POST" });
                  location.href = "/login";
                })
              }
            >
              <LogOut size={16} />
            </button>
          </div>
        </div>
      </aside>
      <main className="main">
        <header className="topbar">
          <div className="breadcrumb">
            <button
              className="icon-btn mobile-menu"
              aria-label="메뉴 열기"
              onClick={() => setSidebarOpen(true)}
            >
              <Menu size={20} />
            </button>
            <span className="breadcrumb-home">내 워크스페이스</span>
            <ChevronRight size={14} />
            <strong>
              {view === "admin"
                ? "서버 관리"
                : view === "attachments"
                  ? "첨부 라이브러리"
                  : detail
                    ? detail.note.workspaceName || workspaceName
                    : workspaceName}
            </strong>
            {detail && (
              <>
                <ChevronRight size={14} />
                <span className="breadcrumb-file truncate">
                  {title || "제목 없는 문서"}
                </span>
              </>
            )}
          </div>
          <div className="topbar-right">
            <button
              className="icon-btn palette-launch"
              aria-label="문서 빠른 열기"
              aria-keyshortcuts="Control+P Meta+P"
              title={`문서 빠른 열기 (${paletteShortcut})`}
              onClick={openPalette}
            >
              <Search size={17} />
            </button>
            <span className="connection">
              <span className={`status-dot ${connected ? "" : "offline"}`} />{" "}
              {connected ? "서버 연결됨" : "연결 확인 필요"}
            </span>
            <button
              className="icon-btn"
              aria-label="새로고침"
              title="목록 새로고침"
              onClick={() => {
                if (view === "attachments")
                  setAttachmentRefresh((value) => value + 1);
                return refresh()
                  .then(() => notify("문서 목록을 새로 불러왔습니다."))
                  .catch((e) => notify(e.message, "error"));
              }}
            >
              <RefreshCw size={16} />
            </button>
            <PwaInstall className="topbar-install" />
            <span className="topbar-divider" />
            <span className="avatar small">
              {auth.account.username.slice(0, 1).toUpperCase()}
            </span>
          </div>
        </header>
        {view === "attachments" ? (
          <AttachmentLibrary
            key={attachmentRefresh}
            api={api}
            notify={notify}
            onDocumentsChanged={refresh}
            onOpenNote={(id) =>
              guard(() => {
                setWorkspace("all");
                setQuery("");
                openNote({ id });
              })
            }
          />
        ) : view === "admin" ? (
          <div className="admin-wrap">
            <AdminPanel
              api={api}
              notify={notify}
              onDocumentsChanged={refresh}
              onAccountChanged={(account) => setAuth({ account })}
            />
          </div>
        ) : (
          <>
            {!detail ? (
              <section className="library-view">
                <div className="page-title">
                  <div>
                    <span className="eyebrow">YOUR KNOWLEDGE, CONNECTED</span>
                    <h1>
                      {workspaceName}
                      <span className="heading-count">{filtered.length}</span>
                    </h1>
                    <p>흩어진 생각을 모으고, 새로운 이야기를 시작하세요.</p>
                  </div>
                  <button
                    className="btn primary"
                    onClick={() => setModal({ kind: "note" })}
                  >
                    <Plus size={17} />새 문서
                  </button>
                </div>
                <div className="library-banner">
                  <div className="banner-icon">
                    <FileText size={30} />
                    <span className="banner-spark">✦</span>
                  </div>
                  <div>
                    <h2>생각이 문서가 되는 공간</h2>
                    <p>
                      Markdown으로 가볍게 쓰고, 미리보기로 한눈에 확인하세요.
                    </p>
                  </div>
                  <span className="banner-tag">
                    <Columns2 size={14} /> Write & preview
                  </span>
                </div>
                <div className="library-toolbar">
                  <div className="search-field">
                    <Search size={17} />
                    <input
                      value={query}
                      onChange={(e) => setQuery(e.target.value)}
                      placeholder="문서 검색..."
                      aria-label="문서 검색"
                    />
                    <button className="search-shortcut" aria-label="문서 빠른 열기" title="모든 워크스페이스에서 문서 찾기" onClick={openPalette}>
                      <kbd>{paletteShortcut}</kbd>
                    </button>
                  </div>
                  <div
                    className="library-sort-switch"
                    role="group"
                    aria-label="문서 정렬"
                  >
                    {[
                      ["updated", "수정일"],
                      ["created", "생성일"],
                      ["name", "이름"],
                    ].map(([key, label]) => (
                      <button
                        key={key}
                        type="button"
                        className={sort === key ? "active" : ""}
                        aria-pressed={sort === key}
                        onClick={() => setSort(key)}
                      >
                        {label}
                      </button>
                    ))}
                  </div>
                  <button
                    type="button"
                    className="sort-direction-button"
                    aria-label={`정렬 방향: ${sortDirection === "asc" ? "오름차순" : "내림차순"}`}
                    title={`${sortDirection === "asc" ? "내림차순" : "오름차순"}으로 전환`}
                    onClick={() =>
                      setSortDirection((previous) =>
                        previous === "asc" ? "desc" : "asc",
                      )
                    }
                  >
                    {sortDirection === "asc" ? (
                      <ArrowUp size={15} aria-hidden="true" />
                    ) : (
                      <ArrowDown size={15} aria-hidden="true" />
                    )}
                    <span>
                      {sortDirection === "asc" ? "오름차순" : "내림차순"}
                    </span>
                  </button>
                  <div
                    className="library-view-switch"
                    role="group"
                    aria-label="목록 보기 방식"
                  >
                    <button
                      className={libraryLayout === "cards" ? "active" : ""}
                      title="카드로 보기"
                      aria-label="카드로 보기"
                      aria-pressed={libraryLayout === "cards"}
                      onClick={() => setLibraryLayout("cards")}
                    >
                      <LayoutGrid size={17} />
                    </button>
                    <button
                      className={libraryLayout === "table" ? "active" : ""}
                      title="테이블로 보기"
                      aria-label="테이블로 보기"
                      aria-pressed={libraryLayout === "table"}
                      onClick={() => setLibraryLayout("table")}
                    >
                      <Rows3 size={17} />
                    </button>
                  </div>
                </div>
                <div className="library-section-label">
                  <span>{query ? "검색 결과" : "문서 목록"}</span>
                  <span>{filtered.length}개의 문서</span>
                </div>
                {filtered.length ? (
                  libraryLayout === "table" ? (
                    <DocumentTable
                      notes={filtered}
                      onOpen={openNote}
                      sort={sort}
                      direction={sortDirection}
                      onSort={changeTableSort}
                    />
                  ) : (
                    <div className="note-grid">
                      {filtered.map((n, i) => (
                        <button
                          key={n.id}
                          className="note-card"
                          data-note-id={n.id}
                          onClick={() => openNote(n)}
                        >
                          <div className="note-card-top">
                            <span className={`document-icon color-${i % 4}`}>
                              <FileText size={23} />
                            </span>
                            <span className="file-type">MD</span>
                          </div>
                          <h3>{n.title}</h3>
                          <p>{n.excerpt || "새로운 이야기를 이어가세요."}</p>
                          <div className="note-card-footer">
                            <span>
                              <Folder size={13} />
                              {n.workspaceName || "문서"}
                            </span>
                            <span
                              title={sort === "created" ? "생성일" : "수정일"}
                            >
                              {sort === "created"
                                ? n.createdAtMs
                                  ? date(n.createdAtMs)
                                  : "생성일 정보 없음"
                                : date(n.updatedAtMs)}
                            </span>
                          </div>
                        </button>
                      ))}
                      <button
                        className="note-card new-card"
                        onClick={() => setModal({ kind: "note" })}
                      >
                        <span>
                          <Plus size={24} />
                        </span>
                        <strong>새로운 생각 기록하기</strong>
                        <p>빈 문서에서 시작하세요</p>
                      </button>
                    </div>
                  )
                ) : (
                  <div className="empty-state">
                    <div className="empty-icon">
                      <FilePlus2 size={32} />
                    </div>
                    <h2>
                      {query
                        ? "검색 결과가 없어요"
                        : "첫 번째 문서를 작성해 보세요"}
                    </h2>
                    <p>
                      {query
                        ? "다른 제목이나 워크스페이스로 검색해 보세요."
                        : "메모, 아이디어, 프로젝트 기록을 나만의 공간에 모아보세요."}
                    </p>
                    <button
                      className="btn primary"
                      onClick={() =>
                        query ? setQuery("") : setModal({ kind: "note" })
                      }
                    >
                      {query ? "검색 초기화" : "새 문서 만들기"}
                      <Plus size={16} />
                    </button>
                  </div>
                )}
                <div className="library-bottom">
                  <span>
                    <FileText size={14} /> Markdown으로 남기는 나의 기록
                  </span>
                  <span>매일 조금씩, 차곡차곡.</span>
                </div>
              </section>
            ) : (
              <div className="document-layout">
                <aside className="document-list">
                  <div className="document-list-heading">
                    <button
                      className="icon-btn"
                      aria-label="문서 목록으로"
                      onClick={() => guard(() => setDetail(null))}
                    >
                      <ArrowLeft size={17} />
                    </button>
                    <strong>문서 목록</strong>
                    <button
                      className="icon-btn push-right"
                      aria-label="새 문서"
                      onClick={() => guard(() => setModal({ kind: "note" }))}
                    >
                      <Plus size={18} />
                    </button>
                  </div>
                  <div className="compact-search">
                    <Search size={15} />
                    <input
                      placeholder="문서 검색"
                      aria-label="문서 검색"
                      value={query}
                      onChange={(e) => setQuery(e.target.value)}
                    />
                  </div>
                  <div className="document-list-items">
                    {filtered.map((n) => (
                      <button
                        className={`document-list-item ${n.id === detail.note.id ? "selected" : ""}`}
                        key={n.id}
                        onClick={() => {
                          if (n.id !== detail.note.id) guard(() => openNote(n));
                        }}
                      >
                        <FileText size={17} />
                        <div>
                          <strong>{n.title}</strong>
                          <span>
                            {date(n.updatedAtMs)} <span>·</span>{" "}
                            {n.workspaceName}
                          </span>
                        </div>
                      </button>
                    ))}
                  </div>
                  <div className="list-bottom">{filtered.length}개의 문서</div>
                </aside>
                <section
                  className={`editor-workspace mode-${mode}`}
                  onDragOver={(e) => {
                    if (e.dataTransfer.types.includes("Files"))
                      e.preventDefault();
                  }}
                  onDrop={(e) => {
                    if (e.dataTransfer.files.length) {
                      e.preventDefault();
                      upload(e.dataTransfer.files);
                    }
                  }}
                  onPaste={(e) => {
                    const files = Array.from(e.clipboardData?.files || []);
                    if (files.length) {
                      e.preventDefault();
                      upload(files);
                    }
                  }}
                >
                  <div className="document-top">
                    <div className="document-title-row">
                      <button
                        className="icon-btn document-back"
                        aria-label="문서 목록으로"
                        onClick={() => guard(() => setDetail(null))}
                      >
                        <ArrowLeft size={18} />
                      </button>
                      <span className="document-title-icon">
                        <FileText size={22} />
                      </span>
                      <input
                        className="title-input"
                        aria-label="문서 제목"
                        disabled={busy}
                        value={title}
                        maxLength={200}
                        onChange={(e) => setTitle(e.target.value)}
                      />
                      <button
                        className="icon-btn"
                        title="Markdown 다운로드"
                        aria-label="Markdown 다운로드"
                        onClick={exportMarkdown}
                      >
                        <Download size={17} />
                      </button>
                      <button
                        className="icon-btn"
                        title="문서 삭제"
                        aria-label="문서 삭제"
                        onClick={() => setModal({ kind: "delete" })}
                      >
                        <Trash2 size={17} />
                      </button>
                    </div>
                    <div className="document-meta">
                      <span>
                        <Folder size={13} />
                        {detail.note.workspaceName}
                      </span>
                      <span className="meta-dot">·</span>
                      <span>{date(detail.note.updatedAtMs)} 수정</span>
                      <span className="meta-dot">·</span>
                      <span>Markdown</span>
                    </div>
                  </div>
                  <div className="editor-toolbar">
                    <div
                      className="mode-tabs"
                      role="group"
                      aria-label="편집 모드"
                    >
                      {[
                        ["write", Code2, "편집"],
                        ["split", Columns2, "분할"],
                        ["preview", Eye, "미리보기"],
                      ].map(([key, Icon, label]) => (
                        <button
                          key={key}
                          className={mode === key ? "active" : ""}
                          aria-pressed={mode === key}
                          onClick={() => setMode(key)}
                        >
                          <Icon size={15} />
                          <span>{label}</span>
                        </button>
                      ))}
                    </div>
                    <div className="editor-actions">
                      <span className={`save-state ${dirty ? "unsaved" : ""}`}>
                        {dirty ? (
                          <>
                            <span />
                            저장하지 않은 변경
                          </>
                        ) : (
                          <>
                            <Check size={13} />
                            저장됨
                          </>
                        )}
                      </span>
                      <button
                        className="btn primary save-button"
                        onClick={save}
                        disabled={!dirty || busy || uploading}
                      >
                        {busy ? (
                          <Loader2 className="spin" size={15} />
                        ) : (
                          <Save size={15} />
                        )}
                        <span>저장</span>
                        <kbd>⌘ S</kbd>
                      </button>
                    </div>
                  </div>
                  {conflict && (
                    <div className="conflict-banner" role="alert">
                      <TriangleAlert size={18} />
                      <div>
                        <strong>다른 기기에서 문서가 변경되었어요.</strong>
                        <span>
                          내 변경 사항을 다운로드한 뒤 최신 버전을 불러와 병합해
                          주세요.
                        </span>
                      </div>
                      <button className="btn" onClick={exportMarkdown}>
                        내 변경 다운로드
                      </button>
                      <button
                        className="btn"
                        onClick={() => setModal({ kind: "reload" })}
                      >
                        최신 버전
                      </button>
                    </div>
                  )}
                  <div className="format-toolbar">
                    <div className="format-buttons">
                      {[
                        [Heading2, "제목", () => insert("## ")],
                        [Bold, "굵게", () => insert("**", "**")],
                        [Italic, "기울임", () => insert("*", "*")],
                        [Quote, "인용", () => insert("> ")],
                        [List, "목록", () => insert("- ")],
                        [CheckSquare, "체크리스트", () => insert("- [ ] ")],
                        [Code, "코드 블록", () => insert("```\n", "\n```")],
                        [Link, "링크", () => insert("[링크 이름](", ")")],
                      ].map(([Icon, label, fn]) => (
                        <button
                          className="icon-btn"
                          title={label}
                          aria-label={label}
                          key={label}
                          onClick={fn}
                        >
                          <Icon size={16} />
                        </button>
                      ))}
                      <span className="toolbar-separator" />
                      <button
                        className="icon-btn"
                        aria-label="이미지 업로드"
                        title="이미지 업로드"
                        disabled={uploading}
                        onClick={() => {
                          fileRef.current.accept = "image/*";
                          fileRef.current.click();
                        }}
                      >
                        <ImagePlus size={17} />
                      </button>
                      <button
                        className="icon-btn"
                        aria-label="파일 업로드"
                        title="파일 업로드"
                        disabled={uploading}
                        onClick={() => {
                          fileRef.current.accept = "";
                          fileRef.current.click();
                        }}
                      >
                        <Paperclip size={17} />
                      </button>
                    </div>
                    <button
                      className={`attachment-toggle ${attachmentsOpen ? "active" : ""}`}
                      onClick={() => setAttachmentsOpen((v) => !v)}
                    >
                      <Paperclip size={13} />
                      첨부 파일{" "}
                      <span>{detail.note.attachments?.length || 0}</span>
                    </button>
                    <input
                      type="file"
                      ref={fileRef}
                      hidden
                      multiple
                      onChange={(e) => upload(e.target.files)}
                    />
                  </div>
                  {attachmentsOpen && (
                    <div className="attachment-drawer">
                      {detail.note.attachments?.length ? (
                        detail.note.attachments.map((a) => (
                          <a
                            href={a.url}
                            key={a.id}
                            target="_blank"
                            rel="noopener noreferrer"
                          >
                            <Paperclip size={15} />
                            <span>{a.fileName}</span>
                            <small>{bytes(a.size)}</small>
                            <ArrowUpRight size={13} />
                          </a>
                        ))
                      ) : (
                        <span>
                          첨부 파일이 없습니다. 파일을 끌어다 놓거나 업로드
                          버튼을 눌러 추가하세요.
                        </span>
                      )}
                    </div>
                  )}
                  {uploading && (
                    <div className="upload-progress" role="status">
                      <Loader2 className="spin" size={14} />
                      첨부 파일을 업로드하고 있어요…
                    </div>
                  )}
                  <div className="editor-panels">
                    {mode !== "preview" && (
                      <div className="source-pane">
                        <div className="pane-label">
                          <span>MARKDOWN</span>
                          <span>
                            <Code2 size={13} /> Source
                          </span>
                        </div>
                        <div className="monaco-container">
                          <Editor
                            language="markdown"
                            theme="notedown"
                            beforeMount={(m) =>
                              m.editor.defineTheme("notedown", {
                                base: "vs",
                                inherit: true,
                                rules: [
                                  { token: "keyword", foreground: "8170B2" },
                                  { token: "string", foreground: "688773" },
                                  { token: "comment", foreground: "9794A3" },
                                ],
                                colors: {
                                  "editor.foreground": "#565C6C",
                                  "editorLineNumber.foreground": "#B4B1C1",
                                  "editorLineNumber.activeForeground":
                                    "#8170B2",
                                  "editor.selectionBackground": "#EAE5F7",
                                  "scrollbarSlider.background": "#D8D5E080",
                                  "scrollbarSlider.hoverBackground":
                                    "#BCB4D480",
                                },
                              })
                            }
                            value={content}
                            onChange={(v) => setContent(v || "")}
                            onMount={(editor) => {
                              editorRef.current = editor;
                              setScrollEditor(editor);
                              editor.onDidDispose(() => {
                                setScrollEditor((current) =>
                                  current === editor ? null : current,
                                );
                                if (editorRef.current === editor)
                                  editorRef.current = null;
                              });
                              editor.addCommand(
                                monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS,
                                () =>
                                  document
                                    .querySelector(".save-button")
                                    ?.click(),
                              );
                            }}
                            options={{
                              readOnly: busy,
                              fontFamily: "Menlo, Consolas, monospace",
                              fontSize: 13,
                              lineHeight: 24,
                              minimap: { enabled: false },
                              wordWrap: "on",
                              scrollBeyondLastLine: false,
                              padding: { top: 22, bottom: 24 },
                              renderLineHighlight: "none",
                              overviewRulerLanes: 0,
                              hideCursorInOverviewRuler: true,
                              lineNumbersMinChars: 3,
                              glyphMargin: false,
                              folding: true,
                              automaticLayout: true,
                              tabSize: 2,
                              ariaLabel: "Markdown 편집기",
                              quickSuggestions: false,
                            }}
                            loading={
                              <div className="editor-loading">
                                <Loader2 className="spin" size={20} />
                                편집기를 불러오고 있어요
                              </div>
                            }
                          />
                        </div>
                      </div>
                    )}
                    {mode !== "write" && (
                      <div className="preview-pane">
                        <div className="pane-label">
                          <span>LIVE PREVIEW</span>
                          <span>
                            <span className="tiny-dot" /> 실시간 미리보기
                          </span>
                        </div>
                        <article
                          className={`markdown-body ${content ? "" : "empty-preview"}`}
                          aria-label="문서 미리보기"
                          ref={previewRef}
                        >
                          {content ? (
                            <div
                              dangerouslySetInnerHTML={{ __html: rendered }}
                            />
                          ) : (
                            <div>
                              <BookOpen size={30} />
                              <h3>생각을 자유롭게 적어보세요</h3>
                              <p>왼쪽에 작성한 Markdown이 여기에 표시됩니다.</p>
                            </div>
                          )}
                        </article>
                      </div>
                    )}
                  </div>
                  <footer className="editor-statusbar">
                    <span>
                      <span className="tiny-dot" />
                      {dirty
                        ? "변경 사항을 저장해 주세요"
                        : "모든 변경 사항이 저장되었습니다"}
                    </span>
                    <div>
                      <span>{content.length.toLocaleString()}자</span>
                      <span>{content.split("\n").length}줄</span>
                      <span>UTF-8</span>
                      <span>Markdown</span>
                    </div>
                  </footer>
                </section>
              </div>
            )}
          </>
        )}
        {loadingNote && (
          <div className="note-loading" role="status">
            <Loader2 className="spin" />
            문서를 불러오는 중…
          </div>
        )}
      </main>
      {paletteOpen && (
        <CommandPalette
          notes={library.notes}
          currentNoteId={detail?.note.id}
          onClose={() => setPaletteOpen(false)}
          onOpen={(note) => {
            setPaletteOpen(false);
            if (detail?.note.id === note.id) return;
            guard(() => {
              setWorkspace(note.workspace);
              setQuery("");
              openNote(note);
            });
          }}
        />
      )}
      {toast && (
        <div
          className={`toast ${toast.type}`}
          role={toast.type === "error" ? "alert" : "status"}
        >
          {toast.type === "error" ? (
            <TriangleAlert size={18} />
          ) : (
            <Check size={18} />
          )}
          <span>{toast.message}</span>
          <button
            className="icon-btn"
            aria-label="알림 닫기"
            onClick={() => setToast(null)}
          >
            <X size={15} />
          </button>
        </div>
      )}
      {modal && (
        <Modal
          title={
            {
              workspace: "새 워크스페이스",
              note: "새 문서 만들기",
              unsaved: "저장하지 않은 변경 사항",
              delete: "문서를 삭제할까요?",
              draft: "작성 중이던 내용을 발견했어요",
              reload: "최신 버전을 불러올까요?",
            }[modal.kind]
          }
          onClose={() => {
            if (!busy) setModal(null);
          }}
        >
          {modalError && (
            <div className="form-error" role="alert">
              {modalError}
            </div>
          )}
          {modal.kind === "workspace" || modal.kind === "note" ? (
            <form
              onSubmit={async (e) => {
                e.preventDefault();
                const values = Object.fromEntries(
                  new FormData(e.currentTarget),
                );
                setBusy(true);
                try {
                  if (modal.kind === "workspace") {
                    const r = await api("/api/editor/workspaces", {
                      method: "POST",
                      body: { name: values.name },
                    });
                    await refresh();
                    setWorkspace(r.workspace.id);
                    setDetail(null);
                    setView("documents");
                    setModal(null);
                    notify("워크스페이스를 만들었습니다.");
                  } else {
                    let workspaceId = values.workspaceId;
                    if (!workspaceId) {
                      const r = await api("/api/editor/workspaces", {
                        method: "POST",
                        body: { name: "나의 기록" },
                      });
                      workspaceId = r.workspace.id;
                    }
                    const d = await api("/api/editor/notes", {
                      method: "POST",
                      body: { workspaceId, title: values.title, content: "" },
                    });
                    await refresh();
                    setWorkspace(workspaceId);
                    setQuery("");
                    setDetail(d);
                    setContent(d.content);
                    setTitle(d.note.title);
                    setView("documents");
                    setModal(null);
                    notify("새 문서를 만들었습니다.");
                  }
                } catch (e) {
                  notify(e.message, "error");
                } finally {
                  setBusy(false);
                }
              }}
            >
              <p className="modal-description">
                {modal.kind === "workspace"
                  ? "관련된 문서를 한곳에 모아 관리하세요."
                  : "떠오른 아이디어에 이름을 붙여보세요."}
              </p>
              <label>
                {modal.kind === "workspace" ? "워크스페이스 이름" : "문서 제목"}
                <input
                  name={modal.kind === "workspace" ? "name" : "title"}
                  maxLength={modal.kind === "workspace" ? 80 : 200}
                  required
                  placeholder={
                    modal.kind === "workspace"
                      ? "예: 프로젝트, 일상, 아이디어"
                      : "예: 새로운 프로젝트 아이디어"
                  }
                />
              </label>
              {modal.kind === "note" && library.workspaces.length > 0 && (
                <label>
                  워크스페이스
                  <select
                    name="workspaceId"
                    defaultValue={
                      workspace !== "all"
                        ? workspace
                        : library.workspaces[0]?.id
                    }
                  >
                    {library.workspaces.map((w) => (
                      <option key={w.id} value={w.id}>
                        {w.name}
                      </option>
                    ))}
                  </select>
                </label>
              )}
              <div className="modal-actions">
                <button
                  type="button"
                  className="btn"
                  onClick={() => setModal(null)}
                >
                  취소
                </button>
                <button className="btn primary" disabled={busy}>
                  {busy ? "만드는 중…" : "만들기"}
                  <Plus size={15} />
                </button>
              </div>
            </form>
          ) : modal.kind === "unsaved" ? (
            <>
              <p className="modal-description">
                아직 저장하지 않은 내용이 있습니다. 저장하고 이동하거나 변경
                사항을 버릴 수 있어요.
              </p>
              <div className="modal-actions">
                <button className="btn" onClick={() => setModal(null)}>
                  계속 편집
                </button>
                <button
                  className="btn danger"
                  onClick={() => {
                    clearDraft();
                    setModal(null);
                    actionRef.current?.();
                  }}
                >
                  변경 버리기
                </button>
                <button
                  className="btn primary"
                  onClick={async () => {
                    setModal(null);
                    if (await save()) actionRef.current?.();
                  }}
                >
                  저장 후 이동
                </button>
              </div>
            </>
          ) : modal.kind === "delete" ? (
            <>
              <p className="modal-description">
                “{title}” 문서가 삭제되며 연결된 기기에도 반영됩니다. 삭제된
                문서의 이력은 서버 관리에서 확인할 수 있어요.
              </p>
              <div className="modal-actions">
                <button className="btn" onClick={() => setModal(null)}>
                  취소
                </button>
                <button
                  className="btn danger"
                  disabled={busy}
                  onClick={async () => {
                    setBusy(true);
                    try {
                      await api(
                        `/api/editor/notes/${encodeURIComponent(detail.note.id)}?revision=${detail.revision}`,
                        { method: "DELETE" },
                      );
                      clearDraft();
                      setDetail(null);
                      setModal(null);
                      await refresh();
                      notify("문서를 삭제했습니다.");
                    } catch (e) {
                      notify(e.message, "error");
                    } finally {
                      setBusy(false);
                    }
                  }}
                >
                  문서 삭제
                </button>
              </div>
            </>
          ) : modal.kind === "draft" ? (
            <>
              <p className="modal-description">
                이 브라우저에 저장하지 않은 임시 내용이 남아 있습니다. 복원 후
                내용을 확인하고 저장하세요.
              </p>
              <div className="modal-actions">
                <button
                  className="btn"
                  onClick={() => {
                    clearDraft();
                    setModal(null);
                  }}
                >
                  서버 내용 사용
                </button>
                <button
                  className="btn primary"
                  onClick={() => {
                    setContent(modal.draft.content);
                    setTitle(modal.draft.title);
                    if (modal.draft.revision !== detail.revision) {
                      setDetail((d) => ({
                        ...d,
                        revision: modal.draft.revision,
                      }));
                      setConflict({ draft: true });
                    }
                    setModal(null);
                  }}
                >
                  임시 내용 복원
                </button>
              </div>
            </>
          ) : (
            <>
              <p className="modal-description">
                편집 중인 내용을 최신 서버 버전으로 바꿉니다. 필요한 내용은 먼저
                Markdown으로 다운로드해 보관하세요.
              </p>
              <div className="modal-actions">
                <button className="btn" onClick={() => setModal(null)}>
                  취소
                </button>
                <button className="btn" onClick={exportMarkdown}>
                  내 변경 다운로드
                </button>
                <button
                  className="btn primary"
                  onClick={() => {
                    clearDraft();
                    setModal(null);
                    openNote(detail.note);
                  }}
                >
                  최신 버전 불러오기
                </button>
              </div>
            </>
          )}
        </Modal>
      )}
    </div>
  );
}
createRoot(document.getElementById("root")).render(<App />);
registerPwa();
