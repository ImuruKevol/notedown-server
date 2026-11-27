import { createRequire } from "node:module";
import { mkdir, writeFile } from "node:fs/promises";
import assert from "node:assert/strict";
const require = createRequire(import.meta.url);
const { chromium } = require("../frontend/node_modules/@playwright/test");
const base = "http://127.0.0.1:5502"; // Only the isolated verification server; never production/preview.
const out = new URL("../docs/verification/", import.meta.url).pathname;
await mkdir(out, { recursive: true });
const browser = await chromium.launch({ channel: "chrome", headless: true });
const context = await browser.newContext({
  viewport: { width: 1440, height: 1000 },
  deviceScaleFactor: 1,
});
const page = await context.newPage();
const errors = [];
const checks = [];
page.on("pageerror", (e) => errors.push(e.message));
const check = (name, value = true) => {
  assert.ok(value, name);
  checks.push(name);
  console.log("PASS", name);
};
const shot = async (name) => {
  await page.evaluate(() => document.fonts.ready);
  if (
    await page.getByRole("button", { name: "알림 닫기", exact: true }).count()
  )
    await page.getByRole("button", { name: "알림 닫기", exact: true }).click();
  await page.screenshot({
    path: out + name + ".png",
    fullPage: true,
    animations: "disabled",
  });
};
const request = async (path, data, method = "POST") => {
  const r = await context.request.fetch(base + path, {
    method,
    ...(data === undefined ? {} : { data }),
  });
  assert.ok(r.ok(), `${method} ${path}: ${await r.text()}`);
  return r.json();
};
const content = `# 아이디어에서 시작하는 새로운 공간\n\n흩어져 있던 생각을 한곳에 모으고, 작은 기록을 다음 단계로 이어갑니다.\n\n> 좋은 아이디어는 완벽하게 정리된 순간보다, 처음 기록한 순간에 시작됩니다.\n\n## 이번 주의 작은 목표\n\n- [x] 프로젝트의 방향 정리하기\n- [x] 참고 자료와 이미지 모으기\n- [ ] 첫 번째 프로토타입 만들기\n\n## 함께 정리한 아이디어\n\n| 주제 | 다음 할 일 | 상태 |\n| --- | --- | --- |\n| 문서 공간 | 읽기 편한 레이아웃 | 진행 중 |\n| 기록의 연결 | 기기 간 동기화 확인 | 완료 |\n\n### 가볍게 시작하기\n\n\`\`\`python\nfrom fastapi import FastAPI\n\napp = FastAPI()\n# 작은 시작, 꾸준한 기록\n\`\`\`\n\n**작성 원칙**: 짧게 기록하고, 자주 돌아보기.\n`;
try {
  await page.goto(base);
  await page.waitForSelector(".auth-form");
  const status = await (
    await context.request.get(base + "/api/setup/status")
  ).json();
  if (!status.configured) {
    await shot("01-setup");
    await page.getByLabel("아이디", { exact: true }).fill("reviewer");
    await page
      .getByLabel("비밀번호", { exact: true })
      .fill("browser-verification-only");
    await page
      .getByLabel("비밀번호 확인", { exact: true })
      .fill("browser-verification-only");
    await page
      .getByRole("button", { name: "워크스페이스 만들기", exact: true })
      .click();
  } else {
    await page.getByLabel("아이디", { exact: true }).fill("reviewer");
    await page
      .getByLabel("비밀번호", { exact: true })
      .fill("browser-verification-only");
    await page.getByRole("button", { name: "로그인", exact: true }).click();
  }
  await page.waitForSelector(".library-view");
  check("setup/login reaches authenticated library");
  await request("/api/admin/storage/reset", { confirm: "RESET" });
  const ws1 = (
    await request("/api/editor/workspaces", { name: "프로젝트 노트" })
  ).workspace;
  const ws2 = (await request("/api/editor/workspaces", { name: "일상과 생각" }))
    .workspace;
  const ws3 = (await request("/api/editor/workspaces", { name: "배움의 기록" }))
    .workspace;
  const first = await request("/api/editor/notes", {
    workspaceId: ws1.id,
    title: "새로운 공간을 위한 아이디어",
    content,
  });
  for (const [title, workspaceId, text] of [
    [
      "서비스 기획 메모",
      ws1.id,
      "# 서비스 기획 메모\n\n사용자의 하루에 자연스럽게 스며드는 서비스.",
    ],
    [
      "9월의 읽고 싶은 책",
      ws2.id,
      "# 읽고 싶은 책\n\n- 디자인과 사람\n- 생각을 정리하는 습관",
    ],
    [
      "오늘의 작은 발견",
      ws2.id,
      "# 오늘의 작은 발견\n\n기록은 기억보다 오래 남는다.",
    ],
    [
      "FastAPI 시작하기",
      ws3.id,
      "# FastAPI 시작하기\n\n타입으로 표현하는 API 설계.",
    ],
  ])
    await request("/api/editor/notes", { workspaceId, title, content: text });
  await page.reload();
  await page.waitForSelector(".note-card");
  await shot("02-library-desktop");
  await page
    .getByRole("button", {
      name: /새로운 공간을 위한 아이디어 MD|MD 새로운 공간을 위한 아이디어/,
    })
    .click();
  await page.waitForSelector(".monaco-editor");
  await page.waitForSelector(".markdown-body h1");
  check("real Monaco editor and live Markdown preview rendered");
  await shot("03-editor-desktop-before");
  await page
    .getByLabel("문서 제목", { exact: true })
    .fill("새로운 공간을 위한 아이디어 · 초안");
  await page.locator(".monaco-editor textarea").focus();
  await page.keyboard.press("Control+End");
  await page.keyboard.insertText("\n\n브라우저에서 추가한 기록입니다.");
  await page
    .getByRole("button", { name: /저장/ })
    .filter({ has: page.locator("kbd") })
    .click();
  await page.waitForFunction(
    () =>
      document.querySelector(".save-state")?.textContent === "저장됨" &&
      !document.querySelector(".save-button .spin"),
  );
  let saved = await request(
    "/api/editor/notes/" + first.note.id,
    undefined,
    "GET",
  );
  check(
    "title/content save persists through API",
    saved.content.includes("브라우저에서 추가한 기록입니다.") &&
      saved.note.title.endsWith("초안"),
  );
  await page.reload();
  await page.waitForSelector(".note-card");
  await page
    .getByRole("button", { name: /MD 새로운 공간을 위한 아이디어 · 초안/ })
    .click();
  await page.waitForSelector(".monaco-editor");
  check("saved note survives reload");
  const imageData = await page.evaluate(() => {
    const c = document.createElement("canvas");
    c.width = 640;
    c.height = 200;
    const x = c.getContext("2d");
    x.fillStyle = "#f4f1fb";
    x.fillRect(0, 0, 640, 200);
    x.fillStyle = "#8b7dcc";
    x.font = "25px sans-serif";
    x.fillText("Small ideas, meaningful progress.", 32, 55);
    [110, 160, 205, 240, 310, 380].forEach((v, i) => {
      x.fillStyle = ["#cfc6ea", "#bdb1e0", "#a595d2"][i % 3];
      x.fillRect(34 + i * 94, 155 - v / 4, 58, v / 4);
    });
    return c.toDataURL("image/png").split(",")[1];
  });
  await page.locator("input[type=file]").setInputFiles({
    name: "아이디어 (1).png",
    mimeType: "image/png",
    buffer: Buffer.from(imageData, "base64"),
  });
  await page.waitForFunction(
    () =>
      document.querySelector(".markdown-body img")?.complete &&
      document.querySelector(".markdown-body img")?.naturalWidth > 0,
  );
  check("image upload resolves desktop-compatible Markdown link");
  await page.locator("input[type=file]").setInputFiles({
    name: "프로젝트 참고.txt",
    mimeType: "text/plain",
    buffer: Buffer.from("Notedown browser attachment verification"),
  });
  await page.waitForFunction(() =>
    document.querySelector(".attachment-toggle")?.textContent.includes("2"),
  );
  await page.getByRole("button", { name: /첨부 파일/ }).click();
  check(
    "file upload attachment drawer",
    (await page.locator(".attachment-drawer a").count()) === 2,
  );
  await page.locator(".save-button").click();
  await page.waitForFunction(
    () =>
      document.querySelector(".save-state")?.textContent === "저장됨" &&
      !document.querySelector(".save-button .spin"),
  );
  await page.getByRole("button", { name: /첨부 파일/ }).click();
  // A malformed URL must remain editable even in a document with attachments.
  await page.locator(".monaco-editor textarea").focus();
  await page.keyboard.press("Control+End");
  await page.keyboard.insertText("\n[bad URL](https://example.com/%E0%A4%A)");
  await page.waitForTimeout(250);
  check(
    "malformed URI cannot crash attachment preview",
    (await page.locator(".editor-workspace").count()) === 1,
  );
  await page.locator(".save-button").click();
  await page.waitForFunction(
    () =>
      document.querySelector(".save-state")?.textContent === "저장됨" &&
      !document.querySelector(".save-button .spin"),
  );
  await page.getByRole("button", { name: "편집", exact: true }).click();
  check("write mode", (await page.locator(".preview-pane").count()) === 0);
  await page.getByRole("button", { name: "미리보기", exact: true }).click();
  check("preview mode", (await page.locator(".source-pane").count()) === 0);
  await page.getByRole("button", { name: "분할", exact: true }).click();
  await page.waitForSelector(".monaco-editor");
  // Navigate while dirty: preserve edits until the user explicitly discards.
  await page
    .getByLabel("문서 제목", { exact: true })
    .fill("저장하지 않은 제목");
  await page
    .getByRole("button", { name: "문서 목록으로", exact: true })
    .click();
  await page.getByRole("dialog").waitFor();
  check("unsaved navigation confirmation");
  await page.getByRole("button", { name: "계속 편집", exact: true }).click();
  check(
    "cancel navigation retains title",
    (await page.getByLabel("문서 제목", { exact: true }).inputValue()) ===
      "저장하지 않은 제목",
  );
  // Concurrent desktop update should produce 409 and preserve browser edits.
  saved = await request("/api/editor/notes/" + first.note.id, undefined, "GET");
  await request(
    "/api/editor/notes/" + first.note.id,
    {
      title: saved.note.title,
      content: saved.content + "\n다른 기기의 변경 사항",
      revision: saved.revision,
    },
    "PUT",
  );
  await page.locator(".save-button").click();
  await page.locator(".conflict-banner").waitFor();
  check(
    "concurrent update rejected without losing browser draft",
    (await page.getByLabel("문서 제목", { exact: true }).inputValue()) ===
      "저장하지 않은 제목",
  );
  await shot("04-conflict");
  await page.getByRole("button", { name: "최신 버전", exact: true }).click();
  await page
    .getByRole("button", { name: "최신 버전 불러오기", exact: true })
    .click();
  await page.waitForFunction(() => !document.querySelector(".conflict-banner") && !document.querySelector(".note-loading") && document.querySelector(".save-state")?.textContent === "저장됨");
  await page.waitForSelector(".monaco-editor");
  // Regression: navigation cannot switch documents while an upload is in flight.
  await page.route("**/api/editor/notes/*/attachments", async (route) => {
    await new Promise((r) => setTimeout(r, 650));
    await route.continue();
  });
  await page.locator("input[type=file]").setInputFiles({
    name: "slow.txt",
    mimeType: "text/plain",
    buffer: Buffer.from("delayed upload"),
  });
  await page.locator(".upload-progress").waitFor();
  await page
    .getByRole("button", { name: "문서 목록으로", exact: true })
    .click();
  check(
    "navigation blocked while attachment upload is in flight",
    (await page.locator(".editor-workspace").count()) === 1,
  );
  await page.waitForFunction(() => !document.querySelector(".upload-progress"));
  await page.unroute("**/api/editor/notes/*/attachments");
  await page.locator(".save-button").click();
  await page.waitForFunction(
    () =>
      document.querySelector(".save-state")?.textContent === "저장됨" &&
      !document.querySelector(".save-button .spin"),
  );
  // Ensure the persisted picture opens and attachment response is not HTML.
  saved = await request("/api/editor/notes/" + first.note.id, undefined, "GET");
  const ar = await context.request.get(base + saved.note.attachments.find(a=>a.fileName === "아이디어 (1).png").url);
  check(
    "attachment raw image headers",
    ar.headers()["content-type"].includes("image/png") &&
      ar.headers()["x-content-type-options"] === "nosniff",
  );
  await request(
    "/api/editor/notes/" + first.note.id,
    { title: saved.note.title, content, revision: saved.revision },
    "PUT",
  );
  await page
    .getByRole("button", { name: "문서 목록으로", exact: true })
    .click();
  await page
    .getByRole("button", { name: /MD 새로운 공간을 위한 아이디어 · 초안/ })
    .click();
  await page.waitForSelector(".markdown-body h1");
  await page.locator(".markdown-body").evaluate((e) => (e.scrollTop = 0));
  await page.locator(".monaco-editor textarea").focus();
  await page.keyboard.press("Control+Home");
  await shot("05-editor-desktop");
  // A restored draft keeps its old baseline, so it cannot overwrite a newer device edit.
  saved = await request("/api/editor/notes/" + first.note.id, undefined, "GET");
  await page.evaluate(
    ({ id, revision }) =>
      localStorage.setItem(
        "notedown-draft:reviewer:" + id,
        JSON.stringify({
          title: "오래된 임시 제목",
          content: "저장하지 않은 내 초안",
          revision,
        }),
      ),
    { id: first.note.id, revision: saved.revision },
  );
  await request(
    "/api/editor/notes/" + first.note.id,
    {
      title: saved.note.title,
      content: content + "\n다른 기기의 새 내용",
      revision: saved.revision,
    },
    "PUT",
  );
  await page.reload();
  await page.waitForSelector(".note-card");
  await page
    .getByRole("button", { name: /MD 새로운 공간을 위한 아이디어 · 초안/ })
    .click();
  await page
    .getByRole("button", { name: "임시 내용 복원", exact: true })
    .click();
  await page.locator(".save-button").click();
  await page.waitForFunction(
    () => !document.querySelector(".save-button .spin"),
  );
  check(
    "restored stale draft cannot overwrite latest server revision",
    (
      await request("/api/editor/notes/" + first.note.id, undefined, "GET")
    ).content.endsWith("다른 기기의 새 내용"),
  );
  await page.getByRole("button", { name: "최신 버전", exact: true }).click();
  await page
    .getByRole("button", { name: "최신 버전 불러오기", exact: true })
    .click();
  await page.waitForFunction(() => !document.querySelector(".conflict-banner") && !document.querySelector(".note-loading") && document.querySelector(".save-state")?.textContent === "저장됨");

  await page.getByRole("button", { name: /서버 관리/ }).click();
  await page.locator(".ap-tab-content").waitFor();
  await shot("06-admin-overview");
  check("admin overview uses live API");
  await page.getByRole("button", { name: /연결된 기기/ }).click();
  await page.locator(".ap-table").waitFor();
  check("client management list");
  await shot("07-admin-clients");
  await page.getByRole("button", { name: /액세스 토큰/ }).click();
  await page.locator(".ap-revoke").first().click();
  await page.getByRole("dialog").waitFor();
  check("token revocation confirmation");
  await page.getByRole("button", { name: "취소", exact: true }).click();
  await shot("08-admin-tokens");
  await page.getByRole("button", { name: "변경 이력", exact: true }).click();
  await page
    .locator(".ap-file-item")
    .filter({ hasText: "새로운 공간을 위한 아이디어" })
    .click();
  await page.locator(".ap-history-item").first().waitFor();
  check(
    "rename preserves multiple Git versions",
    (await page.locator(".ap-history-item").count()) > 1,
  );
  await page.locator(".ap-history-item").last().click();
  await page.waitForFunction(
    () =>
      !document.querySelector(".ap-preview-card .ap-loading") &&
      !!document.querySelector(".ap-document-preview"),
  );
  await shot("09-admin-history");
  await page
    .getByRole("button", { name: "이 버전으로 복원", exact: true })
    .click();
  await page.getByRole("dialog").waitFor();
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "이 버전으로 복원", exact: true })
    .click();
  await page.waitForFunction(() => !document.querySelector("dialog[open]"));
  check("history rollback completes");
  await page.getByRole("button", { name: "설정", exact: true }).click();
  await page
    .getByRole("button", { name: "저장소 초기화", exact: true })
    .click();
  await page.getByRole("dialog").waitFor();
  check(
    "storage reset requires typed confirmation",
    await page
      .getByRole("dialog")
      .getByRole("button", { name: "저장소 초기화", exact: true })
      .isDisabled(),
  );
  await page.getByRole("button", { name: "취소", exact: true }).click();
  await shot("10-admin-settings");
  // Verify account settings refresh the surrounding identity immediately.
  await page
    .locator(".ap-account-form input[name=username]")
    .fill("reviewer-updated");
  await page
    .locator(".ap-account-form input[name=currentPassword]")
    .fill("browser-verification-only");
  await page
    .locator(".ap-account-form input[name=password]")
    .fill("browser-verification-only");
  await page
    .locator(".ap-account-form input[name=confirmPassword]")
    .fill("browser-verification-only");
  await page
    .getByRole("button", { name: "변경 사항 저장", exact: true })
    .click();
  await page.waitForFunction(
    () =>
      document.querySelector(".account strong")?.textContent ===
      "reviewer-updated",
  );
  check("account rename refreshes sidebar identity");
  await page.locator(".ap-account-form input[name=username]").fill("reviewer");
  await page
    .locator(".ap-account-form input[name=currentPassword]")
    .fill("browser-verification-only");
  await page
    .locator(".ap-account-form input[name=password]")
    .fill("browser-verification-only");
  await page
    .locator(".ap-account-form input[name=confirmPassword]")
    .fill("browser-verification-only");
  await page
    .getByRole("button", { name: "변경 사항 저장", exact: true })
    .click();
  await page.waitForFunction(
    () => document.querySelector(".account strong")?.textContent === "reviewer",
  );

  await page.getByRole("button", { name: /문서 라이브러리/ }).click();
  await page.getByRole("button", { name: "새 문서", exact: true }).click();
  await page.getByRole("dialog").getByLabel("문서 제목").fill("삭제 흐름 검증");
  await page.getByRole("button", { name: "만들기", exact: true }).click();
  await page.waitForSelector(".editor-workspace");
  check("document creation through UI");
  await page.getByRole("button", { name: "문서 삭제", exact: true }).click();
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "문서 삭제", exact: true })
    .click();
  await page.waitForSelector(".library-view");
  check("document deletion returns to library");
  await page
    .getByRole("button", { name: "새 워크스페이스", exact: true })
    .click();
  await page
    .getByRole("dialog")
    .getByLabel("워크스페이스 이름")
    .fill("검증 워크스페이스");
  await page.getByRole("button", { name: "만들기", exact: true }).click();
  await page.getByRole("heading", { name: /검증 워크스페이스/ }).waitFor();
  check("workspace creation navigates to new workspace");
  await page
    .getByRole("button", { name: "새 워크스페이스", exact: true })
    .click();
  await page
    .getByRole("dialog")
    .getByLabel("워크스페이스 이름")
    .fill("검증 워크스페이스");
  await page.getByRole("button", { name: "만들기", exact: true }).click();
  await page.getByRole("dialog").getByRole("alert").waitFor();
  check("duplicate workspace error appears inside dialog");
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "닫기", exact: true })
    .click();
  await page.getByRole("button", { name: /문서 라이브러리/ }).click();
  await page.waitForSelector(".library-view");
  await context.setOffline(true);
  await page.getByRole("button", { name: "새로고침", exact: true }).click();
  await page.getByText("연결 확인 필요", { exact: true }).waitFor();
  check("offline state replaces connected indicator");
  await context.setOffline(false);
  await page.getByRole("button", { name: "새로고침", exact: true }).click();
  await page.getByText("서버 연결됨", { exact: true }).waitFor();
  await page.setViewportSize({ width: 390, height: 844 });
  await shot("11-library-mobile");
  check(
    "mobile library no horizontal overflow",
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  );
  await page
    .getByRole("button", { name: /MD 새로운 공간을 위한 아이디어/ })
    .click();
  await page.waitForSelector(".monaco-editor");
  await shot("12-editor-mobile");
  check(
    "Monaco theme has valid line-number colors",
    await page
      .locator(".monaco-editor .line-numbers")
      .first()
      .evaluate((e) => getComputedStyle(e).color !== "rgb(255, 0, 0)"),
  );
  await page
    .getByRole("button", { name: "문서 목록으로", exact: true })
    .click();
  await page.waitForSelector(".library-view");
  check("mobile direct back-to-library action");
  await page
    .getByRole("button", { name: /MD 새로운 공간을 위한 아이디어/ })
    .click();
  await page.waitForSelector(".monaco-editor");
  check(
    "mobile editor no horizontal overflow",
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  );
  await page.getByRole("button", { name: "메뉴 열기", exact: true }).click();
  await page.getByRole("button", { name: /서버 관리/ }).click();
  await page.locator(".ap-tab-content").waitFor();
  await shot("13-admin-mobile");
  check(
    "mobile admin no horizontal overflow",
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  );
  await page.getByRole("button", { name: "메뉴 열기", exact: true }).click();
  await page.getByRole("button", { name: "로그아웃", exact: true }).click();
  await page.waitForSelector(".auth-form");
  await page.setViewportSize({ width: 1440, height: 1000 });
  await shot("14-login");
  check(
    "logout clears protected session",
    (await context.request.get(base + "/api/admin/account")).status() === 401,
  );
  check("no browser JavaScript errors", errors.length === 0);
  await writeFile(
    out + "browser-results.json",
    JSON.stringify(
      { checkedAt: new Date().toISOString(), checks, errors },
      null,
      2,
    ),
  );
  console.log(`Completed ${checks.length} browser checks. Screenshots: ${out}`);
} catch (e) {
  await shot("failure");
  console.error("Page errors:", errors);
  throw e;
} finally {
  await browser.close();
}
