const state = {
  manifest: null,
  files: [],
  attachments: [],
  tokens: [],
  account: null,
  selectedPath: null,
  selectedAttachmentPath: null,
  selectedAttachmentObjectUrl: null,
  history: [],
  selectedHistoryCommit: null,
};

const els = {
  status: document.querySelector("#server-status"),
  dashboard: document.querySelector("#dashboard"),
  refresh: document.querySelector("#refresh-button"),
  accountButton: document.querySelector("#account-button"),
  logout: document.querySelector("#logout-button"),
  menuButtons: document.querySelectorAll("[data-admin-section]"),
  adminSections: document.querySelectorAll("[data-admin-panel]"),
  fileCount: document.querySelector("#summary-file-count"),
  lastSync: document.querySelector("#summary-last-sync"),
  fileFilter: document.querySelector("#file-filter"),
  folderFilter: document.querySelector("#folder-filter"),
  filesTable: document.querySelector("#files-table"),
  clientsTable: document.querySelector("#clients-table"),
  tokensTable: document.querySelector("#tokens-table"),
  previewPath: document.querySelector("#preview-path"),
  selectedFileMeta: document.querySelector("#selected-file-meta"),
  preview: document.querySelector("#file-preview"),
  attachmentsStatus: document.querySelector("#attachments-status"),
  attachmentList: document.querySelector("#attachment-list"),
  attachmentModal: document.querySelector("#attachment-modal"),
  attachmentModalTitle: document.querySelector("#attachment-modal-title"),
  attachmentModalMeta: document.querySelector("#attachment-modal-meta"),
  attachmentModalBody: document.querySelector("#attachment-modal-body"),
  attachmentModalDownload: document.querySelector("#attachment-modal-download"),
  rollbackButton: document.querySelector("#rollback-button"),
  historyStatus: document.querySelector("#history-status"),
  historyTable: document.querySelector("#history-table"),
  accountModal: document.querySelector("#account-modal"),
  accountForm: document.querySelector("#account-form"),
  accountSource: document.querySelector("#account-source"),
  accountUsername: document.querySelector("#account-username"),
  accountCurrentPassword: document.querySelector("#account-current-password"),
  accountPassword: document.querySelector("#account-password"),
  accountPasswordConfirm: document.querySelector("#account-password-confirm"),
  accountSubmit: document.querySelector("#account-submit"),
  accountMessage: document.querySelector("#account-message"),
};

const ERROR_MESSAGES = {
  auth_update_not_supported: "환경변수로 설정된 계정은 이 화면에서 변경할 수 없습니다.",
  invalid_current_password: "현재 Password가 올바르지 않습니다.",
  username_required: "관리자 ID를 입력하세요.",
  password_too_short: "새 Password는 8자 이상이어야 합니다.",
  password_confirmation_mismatch: "새 Password와 확인 값이 일치하지 않습니다.",
};

function setStatus(message) {
  els.status.textContent = message;
}

function setActiveSection(section) {
  els.menuButtons.forEach((button) => {
    const active = button.dataset.adminSection === section;
    button.classList.toggle("active", active);
    button.setAttribute("aria-selected", active ? "true" : "false");
  });
  els.adminSections.forEach((panel) => {
    panel.hidden = panel.dataset.adminPanel !== section;
  });
}

async function requestJson(path, options = {}) {
  const headers = new Headers(options.headers || {});
  headers.set("Content-Type", "application/json");

  const response = await fetch(path, { ...options, headers });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const message = body.message || body.error || `HTTP ${response.status}`;
    throw new Error(message);
  }
  return body;
}

function formatDate(value) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString("ko-KR");
}

function formatBytes(value) {
  const size = Number(value || 0);
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

function encodePath(path) {
  return path.split("/").map(encodeURIComponent).join("/");
}

function cleanText(value) {
  return typeof value === "string" ? value.trim() : "";
}

function folderFromPath(relativePath) {
  const path = cleanText(relativePath);
  const separatorIndex = path.lastIndexOf("/");
  if (separatorIndex <= 0) return "루트";
  return path.slice(0, separatorIndex);
}

function noteInfo(file) {
  return objectOrEmpty(file?.note);
}

function fileTitle(file) {
  return cleanText(noteInfo(file).title) || "제목 없음";
}

function fileFolder(file) {
  const note = noteInfo(file);
  return (
    cleanText(note.folder) ||
    cleanText(note.workspaceName) ||
    cleanText(note.workspace) ||
    folderFromPath(file?.relativePath)
  );
}

function fileSearchText(file) {
  return fileTitle(file).toLowerCase();
}

function fileSortKey(file) {
  return `${fileFolder(file)} ${fileTitle(file)} ${cleanText(file?.relativePath)}`;
}

function attachmentsForFile(file) {
  const relativePath = cleanText(file?.relativePath);
  if (!relativePath) return [];
  return state.attachments
    .filter((attachment) => cleanText(attachment?.noteRelativePath) === relativePath)
    .sort((a, b) => attachmentTitle(a).localeCompare(attachmentTitle(b), "ko-KR"));
}

function attachmentTitle(attachment) {
  return (
    cleanText(attachment?.fileName) ||
    cleanText(attachment?.attachment?.fileName) ||
    cleanText(attachment?.relativePath).split("/").pop() ||
    "첨부 파일"
  );
}

function attachmentTypeLabel(attachment) {
  const mimeType = cleanText(attachment?.mimeType);
  if (mimeType.startsWith("image/")) return "이미지";
  if (mimeType) return mimeType;
  return "파일";
}

function isImageAttachment(attachment) {
  return cleanText(attachment?.mimeType).startsWith("image/");
}

function isTextAttachment(attachment) {
  const mimeType = cleanText(attachment?.mimeType);
  const fileName = attachmentTitle(attachment).toLowerCase();
  return (
    mimeType.startsWith("text/") ||
    mimeType === "application/json" ||
    fileName.endsWith(".txt") ||
    fileName.endsWith(".md") ||
    fileName.endsWith(".json")
  );
}

function folderOptions() {
  return [...new Set(state.files.map(fileFolder))]
    .filter(Boolean)
    .sort((a, b) => a.localeCompare(b, "ko-KR"));
}

function renderFolderFilter() {
  const folders = folderOptions();
  const selectedFolder = els.folderFilter.value;
  const nextFolder = folders.includes(selectedFolder) ? selectedFolder : "";

  els.folderFilter.innerHTML = [
    '<option value="">전체 폴더</option>',
    ...folders.map((folder) => (
      `<option value="${escapeHtml(folder)}">${escapeHtml(folder)}</option>`
    )),
  ].join("");
  els.folderFilter.value = nextFolder;
}

function lastSyncTime(manifest) {
  const syncTimes = Object.values(manifest.clients || {})
    .map((client) => client?.lastSyncAt)
    .filter(Boolean)
    .map((value) => new Date(value).getTime())
    .filter((value) => !Number.isNaN(value));

  if (syncTimes.length) {
    return new Date(Math.max(...syncTimes)).toISOString();
  }
  return manifest.metadata?.updatedAt || manifest.updatedAt;
}

function renderSummary(manifest) {
  const files = manifest.files || [];
  const activeFileCount = files.filter((file) => !file.deleted).length;
  els.fileCount.textContent = `${activeFileCount.toLocaleString("ko-KR")}개`;
  els.lastSync.textContent = formatDate(lastSyncTime(manifest));
}

function renderFiles() {
  const filter = els.fileFilter.value.trim().toLowerCase();
  const selectedFolder = els.folderFilter.value;
  const rows = state.files
    .filter((file) => (
      (!selectedFolder || fileFolder(file) === selectedFolder) &&
      fileSearchText(file).includes(filter)
    ))
    .sort((a, b) => fileSortKey(a).localeCompare(fileSortKey(b), "ko-KR"));

  if (!rows.length) {
    els.filesTable.innerHTML =
      '<tr class="empty-row"><td colspan="2">표시할 파일이 없습니다.</td></tr>';
    return;
  }

  els.filesTable.innerHTML = rows
    .map((file) => {
      const status = file.deleted ? "삭제됨" : "활성";
      const pillClass = file.deleted ? "status-pill deleted" : "status-pill";
      const selected = file.relativePath === state.selectedPath ? " selected" : "";
      return `
        <tr class="${selected}" data-path="${escapeHtml(file.relativePath)}" data-deleted="${file.deleted}">
          <td class="note-cell">
            <strong>${escapeHtml(fileTitle(file))}</strong>
            <span>${escapeHtml(fileFolder(file))} · 리비전 ${escapeHtml(file.revision ?? "-")} · ${formatBytes(file.size)} · ${formatDate(file.serverUpdatedAt)}</span>
          </td>
          <td><span class="${pillClass}">${status}</span></td>
        </tr>
      `;
    })
    .join("");
}

function selectedFile() {
  return state.files.find((item) => item.relativePath === state.selectedPath);
}

function renderSelectedFileMeta(file = selectedFile()) {
  if (!state.selectedPath) {
    els.previewPath.textContent = "선택 없음";
    els.selectedFileMeta.textContent = "파일을 선택하면 상태 정보가 표시됩니다.";
    return;
  }

  if (!file) {
    els.selectedFileMeta.textContent = "선택한 파일 정보를 찾을 수 없습니다.";
    return;
  }

  const status = file.deleted ? "삭제됨" : "활성";
  const pillClass = file.deleted ? "status-pill deleted" : "status-pill";
  els.previewPath.textContent = fileTitle(file);
  els.selectedFileMeta.innerHTML = `
    <span class="${pillClass}">${status}</span>
    <span>${escapeHtml(fileFolder(file))}</span>
    <span>리비전 ${escapeHtml(file.revision ?? "-")}</span>
    <span>${formatBytes(file.size)}</span>
    <span>${formatDate(file.serverUpdatedAt)}</span>
  `;
}

function renderAttachments(file = selectedFile()) {
  if (!state.selectedPath) {
    state.selectedAttachmentPath = null;
    revokeSelectedAttachmentUrl();
    els.attachmentsStatus.textContent = "파일을 선택하면 첨부 목록이 표시됩니다.";
    els.attachmentList.innerHTML = '<div class="attachment-empty">첨부 없음</div>';
    return;
  }

  const attachments = attachmentsForFile(file);
  if (!attachments.length) {
    state.selectedAttachmentPath = null;
    revokeSelectedAttachmentUrl();
    els.attachmentsStatus.textContent = "첨부된 파일이 없습니다.";
    els.attachmentList.innerHTML = '<div class="attachment-empty">첨부 없음</div>';
    return;
  }

  if (
    state.selectedAttachmentPath &&
    !attachments.some((item) => item.relativePath === state.selectedAttachmentPath)
  ) {
    state.selectedAttachmentPath = null;
    revokeSelectedAttachmentUrl();
  }

  els.attachmentsStatus.textContent = `${attachments.length}개 첨부`;
  els.attachmentList.innerHTML = attachments
    .map((attachment) => {
      const selected = attachment.relativePath === state.selectedAttachmentPath
        ? " selected"
        : "";
      return `
        <div class="attachment-item${selected}">
          <button class="attachment-select" type="button" data-attachment-view="${escapeHtml(attachment.relativePath)}">
            <strong>${escapeHtml(attachmentTitle(attachment))}</strong>
            <span>${escapeHtml(attachmentTypeLabel(attachment))} · ${formatBytes(attachment.size)}</span>
          </button>
          <button class="attachment-download" type="button" data-attachment-download="${escapeHtml(attachment.relativePath)}">다운로드</button>
        </div>
      `;
    })
    .join("");
}

function renderClients(clients) {
  const entries = Object.entries(clients || {}).sort((a, b) => {
    const aTime = new Date(a[1]?.lastSeenAt || a[1]?.lastSyncAt || 0).getTime();
    const bTime = new Date(b[1]?.lastSeenAt || b[1]?.lastSyncAt || 0).getTime();
    return bTime - aTime;
  });

  if (!entries.length) {
    els.clientsTable.innerHTML =
      '<tr class="empty-row"><td colspan="4">등록된 클라이언트가 없습니다.</td></tr>';
    return;
  }

  els.clientsTable.innerHTML = entries
    .map(([id, client]) => {
      const info = objectOrEmpty(client.clientInfo);
      const connection = objectOrEmpty(client.connectionInfo);
      const ipAddress = pickValue(
        [info, connection],
        ["ipAddress", "ip", "ipv4", "remoteAddress"]
      );
      const forwardedFor = pickValue([connection], ["forwardedFor"]);
      const userAgent = pickValue([connection, info], ["userAgent", "ua"]);
      const lastSeenAt = client.lastSeenAt || client.lastSyncAt || client.lastPlanAt;

      return `
      <tr>
        <td class="client-cell">
          <strong>${escapeHtml(id)}</strong>
          <span>${escapeHtml(client.user || "-")}</span>
        </td>
        <td>${detailStack([
          ipAddress ? `IP ${ipAddress}` : "IP -",
          forwardedFor ? `Forwarded ${forwardedFor}` : null,
          userAgent,
        ])}</td>
        <td>${formatDate(lastSeenAt)}</td>
        <td>${client.lastSeenRevision ?? "-"}</td>
      </tr>
    `;
    })
    .join("");
}

function renderTokens(tokens) {
  if (!tokens.length) {
    els.tokensTable.innerHTML =
      '<tr class="empty-row"><td colspan="5">발급된 토큰이 없습니다.</td></tr>';
    return;
  }

  els.tokensTable.innerHTML = tokens
    .map((token) => {
      const tokenId = token.id || "";
      const connection = objectOrEmpty(token.connectionInfo);
      const ipAddress = pickValue(
        [connection],
        ["ipAddress", "ip", "ipv4", "remoteAddress"]
      );
      const forwardedFor = pickValue([connection], ["forwardedFor"]);
      const userAgent = pickValue([connection], ["userAgent"]);

      return `
      <tr>
        <td class="token-cell">
          <strong>${escapeHtml(shortTokenId(tokenId))}</strong>
          <span>${escapeHtml(tokenId)}</span>
        </td>
        <td>${escapeHtml(token.username || "-")}</td>
        <td>${detailStack([
          ipAddress ? `IP ${ipAddress}` : "IP -",
          forwardedFor ? `Forwarded ${forwardedFor}` : null,
          userAgent,
        ])}</td>
        <td>${detailStack([
          `발급 ${formatDate(token.issuedAt)}`,
          token.lastUsedAt ? `사용 ${formatDate(token.lastUsedAt)}` : "사용 -",
        ], 2)}</td>
        <td class="token-actions">
          <button class="danger-button" type="button" data-token-id="${escapeHtml(tokenId)}">
            무효화
          </button>
        </td>
      </tr>
    `;
    })
    .join("");
}

function renderHistory() {
  const commits = state.history || [];
  els.rollbackButton.disabled = !state.selectedHistoryCommit || !state.selectedPath;

  if (!commits.length) {
    els.historyStatus.textContent = state.selectedPath
      ? "표시할 git 이력이 없습니다."
      : "파일을 선택하면 이력이 표시됩니다.";
    els.historyTable.innerHTML =
      '<tr class="empty-row"><td colspan="2">이력 없음</td></tr>';
    return;
  }

  els.historyStatus.textContent = `${commits.length}개 커밋`;
  els.historyTable.innerHTML = commits
    .map((commit) => {
      const selected = commit.commit === state.selectedHistoryCommit ? " selected" : "";
      const status = commit.deleted ? "삭제됨" : formatBytes(commit.size);
      return `
        <tr class="${selected}" data-commit="${escapeHtml(commit.commit)}">
          <td class="commit-cell">
            <strong>${escapeHtml(commit.shortCommit)}</strong>
            <span>${escapeHtml(commit.author || "-")}</span>
          </td>
          <td class="history-detail">
            <span class="${commit.deleted ? "status-pill deleted" : "status-pill"}">${status}</span>
            <strong>${formatDate(commit.committedAt)}</strong>
            <span class="history-message">${escapeHtml(commit.message || "-")}</span>
          </td>
        </tr>
      `;
    })
    .join("");
}

function shortTokenId(tokenId) {
  if (!tokenId || tokenId.length <= 18) {
    return tokenId || "-";
  }
  return `${tokenId.slice(0, 8)}...${tokenId.slice(-6)}`;
}

function objectOrEmpty(value) {
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}

function pickValue(objects, keys) {
  for (const object of objects) {
    for (const key of keys) {
      const value = object[key];
      if (value !== undefined && value !== null && value !== "") {
        return String(value);
      }
    }
  }
  return "";
}

function detailStack(lines, limit = 3) {
  const visibleLines = lines.filter(Boolean).slice(0, limit);
  return `
    <div class="detail-stack">
      ${visibleLines.map((line) => `<span>${escapeHtml(line)}</span>`).join("")}
    </div>
  `;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

async function loadManifest() {
  setStatus("데이터 조회 중");
  let manifest;
  let tokenPayload;
  try {
    [manifest, tokenPayload] = await Promise.all([
      requestJson("/api/manifest"),
      requestJson("/api/admin/tokens"),
    ]);
  } catch (error) {
    if (error.message.includes("token") || error.message.includes("401")) {
      window.location.assign("/login");
      return;
    }
    throw error;
  }
  state.manifest = manifest;
  state.files = manifest.files || [];
  state.attachments = manifest.attachments || [];
  state.tokens = tokenPayload.tokens || [];
  if (
    state.selectedPath &&
    !state.files.some((file) => file.relativePath === state.selectedPath)
  ) {
    state.selectedPath = null;
    state.selectedAttachmentPath = null;
    revokeSelectedAttachmentUrl();
    state.selectedHistoryCommit = null;
    state.history = [];
  }
  renderSummary(manifest);
  renderFolderFilter();
  renderFiles();
  renderSelectedFileMeta();
  renderAttachments();
  renderHistory();
  renderClients(manifest.clients);
  renderTokens(state.tokens);
  setStatus("연결됨");
}

async function loadFile(relativePath) {
  state.selectedPath = relativePath;
  state.selectedHistoryCommit = null;
  state.history = [];
  const file = state.files.find((item) => item.relativePath === relativePath);
  renderFiles();
  renderSelectedFileMeta(file);
  state.selectedAttachmentPath = null;
  revokeSelectedAttachmentUrl();
  renderAttachments(file);
  renderHistory();

  els.previewPath.textContent = fileTitle(file);
  els.historyStatus.textContent = "Git 이력 조회 중";
  const historyPromise = loadFileHistory(relativePath);

  if (!file || file.deleted) {
    els.preview.textContent = "삭제된 파일은 미리볼 수 없습니다.";
    await historyPromise;
    return;
  }

  els.preview.textContent = "파일 조회 중";
  const payload = await requestJson(`/api/files/${encodePath(relativePath)}`);
  els.preview.textContent = decodeBase64Text(payload.content);
  await historyPromise;
}

async function loadFileHistory(relativePath) {
  const payload = await requestJson(`/api/admin/files/${encodePath(relativePath)}/history`);
  state.history = payload.commits || [];
  renderHistory();
}

async function loadFileVersion(relativePath, commit) {
  state.selectedHistoryCommit = commit;
  renderHistory();
  els.previewPath.textContent = `${fileTitle(selectedFile())} @ ${shortTokenId(commit)}`;
  els.preview.textContent = "버전 조회 중";

  const payload = await requestJson(
    `/api/admin/files/${encodePath(relativePath)}/history/${encodeURIComponent(commit)}`
  );
  if (payload.deleted) {
    els.preview.textContent = "선택한 커밋에서는 파일이 삭제된 상태입니다.";
  } else {
    els.preview.textContent = decodeBase64Text(payload.content);
  }
  renderHistory();
}

async function loadAttachmentView(relativePath) {
  const attachment = state.attachments.find((item) => item.relativePath === relativePath);
  if (!attachment || attachment.deleted) {
    openAttachmentModalShell({
      title: "첨부 미리보기",
      meta: "선택한 첨부 정보를 찾을 수 없습니다.",
      relativePath: "",
    });
    els.attachmentModalBody.textContent = "선택한 첨부 정보를 찾을 수 없습니다.";
    return;
  }

  state.selectedAttachmentPath = relativePath;
  renderAttachments(selectedFile());
  revokeSelectedAttachmentUrl();
  openAttachmentModalShell({
    title: attachmentTitle(attachment),
    meta: `${attachmentTypeLabel(attachment)} · ${formatBytes(attachment.size)}`,
    relativePath,
  });
  els.attachmentModalBody.textContent = "첨부 조회 중";

  const payload = await requestJson(`/api/attachments/${encodePath(relativePath)}`);
  const blob = attachmentBlob(payload);
  const objectUrl = URL.createObjectURL(blob);
  state.selectedAttachmentObjectUrl = objectUrl;
  els.attachmentModalDownload.disabled = false;
  els.attachmentModalDownload.dataset.attachmentDownload = relativePath;
  els.attachmentModalMeta.textContent = `${payload.mimeType || "application/octet-stream"} · ${formatBytes(payload.size)}`;

  if (isImageAttachment(payload)) {
    els.attachmentModalBody.innerHTML = `
      <img src="${objectUrl}" alt="${escapeHtml(attachmentTitle(payload))}">
      <div class="attachment-viewer-meta">
        ${escapeHtml(payload.mimeType || "image")} · ${formatBytes(payload.size)}
      </div>
    `;
    return;
  }

  if (isTextAttachment(payload)) {
    els.attachmentModalBody.innerHTML = `
      <pre class="attachment-text-preview">${escapeHtml(decodeBase64Text(payload.content))}</pre>
    `;
    return;
  }

  els.attachmentModalBody.innerHTML = `
    <div class="attachment-download-state">
      <strong>${escapeHtml(attachmentTitle(payload))}</strong>
      <span>${escapeHtml(payload.mimeType || "application/octet-stream")} · ${formatBytes(payload.size)}</span>
      <button type="button" data-attachment-download="${escapeHtml(relativePath)}">다운로드</button>
    </div>
  `;
}

function openAttachmentModalShell({ title, meta, relativePath }) {
  els.attachmentModalTitle.textContent = title || "첨부 미리보기";
  els.attachmentModalMeta.textContent = meta || "";
  els.attachmentModalDownload.disabled = true;
  if (relativePath) {
    els.attachmentModalDownload.dataset.attachmentDownload = relativePath;
  } else {
    delete els.attachmentModalDownload.dataset.attachmentDownload;
  }
  els.attachmentModal.hidden = false;
  document.body.classList.add("modal-open");
}

function closeAttachmentModal() {
  els.attachmentModal.hidden = true;
  revokeSelectedAttachmentUrl();
  if (els.accountModal.hidden) {
    document.body.classList.remove("modal-open");
  }
}

async function downloadAttachment(relativePath) {
  const payload = await requestJson(`/api/attachments/${encodePath(relativePath)}`);
  const blob = attachmentBlob(payload);
  const objectUrl = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = objectUrl;
  link.download = attachmentTitle(payload);
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(objectUrl);
}

function attachmentBlob(payload) {
  return new Blob([decodeBase64Bytes(payload.content)], {
    type: payload.mimeType || "application/octet-stream",
  });
}

function revokeSelectedAttachmentUrl() {
  if (!state.selectedAttachmentObjectUrl) return;
  URL.revokeObjectURL(state.selectedAttachmentObjectUrl);
  state.selectedAttachmentObjectUrl = null;
}

async function rollbackSelectedVersion() {
  if (!state.selectedPath || !state.selectedHistoryCommit) return;
  const shouldRollback = window.confirm("선택한 git 커밋 상태로 파일을 롤백할까요?");
  if (!shouldRollback) return;

  setStatus("파일 롤백 중");
  await requestJson(`/api/admin/files/${encodePath(state.selectedPath)}/rollback`, {
    method: "POST",
    body: JSON.stringify({ commit: state.selectedHistoryCommit }),
  });
  await loadManifest();
  await loadFile(state.selectedPath);
  setStatus("파일 롤백됨");
}

function decodeBase64Text(content) {
  return new TextDecoder("utf-8").decode(decodeBase64Bytes(content));
}

function decodeBase64Bytes(content) {
  return Uint8Array.from(atob(content || ""), (char) => char.charCodeAt(0));
}

async function logout() {
  await fetch("/api/logout", { method: "POST" }).catch(() => {});
  sessionStorage.removeItem("notedownAdminToken");
  window.location.assign("/login");
}

async function revokeToken(tokenId) {
  if (!tokenId) return;
  const shouldRevoke = window.confirm("이 토큰을 무효화할까요?");
  if (!shouldRevoke) return;

  setStatus("토큰 무효화 중");
  await requestJson(`/api/admin/tokens/${encodeURIComponent(tokenId)}`, {
    method: "DELETE",
  });
  await loadManifest();
  setStatus("토큰 무효화됨");
}

function friendlyError(error) {
  return ERROR_MESSAGES[error.message] || error.message;
}

function setAccountMessage(message, isError = false) {
  els.accountMessage.textContent = message;
  els.accountMessage.classList.toggle("error", isError);
}

async function loadAccount() {
  const account = await requestJson("/api/admin/account");
  state.account = account;
  renderAccount(account);
}

function renderAccount(account) {
  const editable = account.editable !== false;
  const sourceLabel = account.source === "environment" ? "환경변수 계정" : "저장 파일 계정";
  els.accountUsername.value = account.username || "";
  els.accountSource.textContent = editable
    ? sourceLabel
    : `${sourceLabel}: 서버 설정에서 변경`;
  els.accountUsername.disabled = !editable;
  els.accountCurrentPassword.disabled = !editable;
  els.accountPassword.disabled = !editable;
  els.accountPasswordConfirm.disabled = !editable;
  els.accountSubmit.disabled = !editable;
}

function openAccountModal() {
  els.accountModal.hidden = false;
  document.body.classList.add("modal-open");
  setAccountMessage("");
  els.accountCurrentPassword.value = "";
  els.accountPassword.value = "";
  els.accountPasswordConfirm.value = "";
  loadAccount()
    .then(() => els.accountUsername.focus())
    .catch((error) => {
      setAccountMessage(friendlyError(error), true);
    });
}

function closeAccountModal() {
  els.accountModal.hidden = true;
  document.body.classList.remove("modal-open");
}

async function saveAccount(event) {
  event.preventDefault();
  const username = els.accountUsername.value.trim();
  const currentPassword = els.accountCurrentPassword.value;
  const password = els.accountPassword.value;
  const confirmPassword = els.accountPasswordConfirm.value;

  if (password.length < 8) {
    setAccountMessage(ERROR_MESSAGES.password_too_short, true);
    return;
  }

  if (password !== confirmPassword) {
    setAccountMessage(ERROR_MESSAGES.password_confirmation_mismatch, true);
    return;
  }

  els.accountSubmit.disabled = true;
  setAccountMessage("저장 중");

  try {
    const account = await requestJson("/api/admin/account", {
      method: "POST",
      body: JSON.stringify({ username, currentPassword, password, confirmPassword }),
    });
    state.account = account;
    renderAccount(account);
    els.accountCurrentPassword.value = "";
    els.accountPassword.value = "";
    els.accountPasswordConfirm.value = "";
    setAccountMessage("저장됨");
    setStatus("관리자 계정 변경됨");
    setTimeout(closeAccountModal, 500);
  } catch (error) {
    setAccountMessage(friendlyError(error), true);
  } finally {
    if (state.account?.editable !== false) {
      els.accountSubmit.disabled = false;
    }
  }
}

els.refresh.addEventListener("click", () => loadManifest().catch((error) => {
  setStatus(error.message);
  if (error.message.includes("token") || error.message.includes("401")) {
    window.location.assign("/login");
  }
}));
els.accountButton.addEventListener("click", openAccountModal);
els.logout.addEventListener("click", logout);
els.menuButtons.forEach((button) => {
  button.addEventListener("click", () => setActiveSection(button.dataset.adminSection));
});
els.fileFilter.addEventListener("input", renderFiles);
els.folderFilter.addEventListener("change", renderFiles);
els.rollbackButton.addEventListener("click", () => {
  rollbackSelectedVersion().catch((error) => setStatus(error.message));
});
els.filesTable.addEventListener("click", (event) => {
  const row = event.target.closest("tr[data-path]");
  if (!row) return;
  loadFile(row.dataset.path).catch((error) => {
    els.preview.textContent = error.message;
  });
});
document.addEventListener("click", (event) => {
  const viewButton = event.target.closest("[data-attachment-view]");
  if (viewButton) {
    loadAttachmentView(viewButton.dataset.attachmentView).catch((error) => {
      els.attachmentModalBody.textContent = error.message;
    });
    return;
  }

  const downloadButton = event.target.closest("[data-attachment-download]");
  if (!downloadButton || downloadButton.disabled) return;
  downloadAttachment(downloadButton.dataset.attachmentDownload).catch((error) => {
    if (!els.attachmentModal.hidden) {
      els.attachmentModalBody.textContent = error.message;
    } else {
      setStatus(error.message);
    }
  });
});
els.historyTable.addEventListener("click", (event) => {
  const row = event.target.closest("tr[data-commit]");
  if (!row || !state.selectedPath) return;
  loadFileVersion(state.selectedPath, row.dataset.commit).catch((error) => {
    els.preview.textContent = error.message;
  });
});
els.tokensTable.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-token-id]");
  if (!button) return;
  revokeToken(button.dataset.tokenId).catch((error) => setStatus(error.message));
});
els.accountForm.addEventListener("submit", saveAccount);
document.querySelectorAll("[data-account-close]").forEach((button) => {
  button.addEventListener("click", closeAccountModal);
});
document.querySelectorAll("[data-attachment-close]").forEach((button) => {
  button.addEventListener("click", closeAttachmentModal);
});
document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  if (!els.attachmentModal.hidden) {
    closeAttachmentModal();
    return;
  }
  if (!els.accountModal.hidden) {
    closeAccountModal();
  }
});

requestJson("/api/health")
  .then(() => setStatus("연결됨"))
  .catch(() => setStatus("서버 상태 확인 실패"));

sessionStorage.removeItem("notedownAdminToken");
setActiveSection("files");
loadManifest().catch((error) => setStatus(error.message));
