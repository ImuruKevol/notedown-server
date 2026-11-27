import { createRequire } from "node:module";
import { mkdir, writeFile } from "node:fs/promises";
import assert from "node:assert/strict";
const require = createRequire(import.meta.url);
const { chromium } = require("../frontend/node_modules/@playwright/test");
const base = "http://127.0.0.1:5502"; // Synthetic verification server only.
const out = new URL("../docs/verification/command-palette/", import.meta.url).pathname;
await mkdir(out, { recursive: true });
const browser = await chromium.launch({ channel: "chrome", headless: true });
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
const page = await context.newPage();
const checks = [], screenshots = [], errors = [];
page.on("pageerror", (error) => errors.push(error.message));
const check = (name, value) => { assert.ok(value, name); checks.push(name); console.log("PASS", name); };
const api = async (path, data, method = "POST") => {
  const r = await context.request.fetch(base + path, { method, ...(data === undefined ? {} : { data }) });
  assert.ok(r.ok(), `${method} ${path}: ${r.status()}`);
  return r.json();
};
const palette = () => page.getByRole("dialog", { name: "문서 빠른 열기", exact: true });
const input = () => palette().getByRole("combobox");
const options = () => palette().getByRole("option");
const titles = () => options().locator(".palette-note > strong").allTextContents();
const activeTitle = () => palette().locator('[aria-selected="true"] .palette-note > strong').textContent();
const open = async (shortcut = "Control+p") => { await page.keyboard.press(shortcut); await input().waitFor(); };
const close = async () => { await page.keyboard.press("Escape"); await palette().waitFor({ state: "detached" }); };
const find = async (query) => { await input().fill(query); };
const shot = async (name) => {
  await page.evaluate(() => document.fonts.ready);
  await page.waitForTimeout(150);
  await page.screenshot({ path: out + name + ".png", animations: "disabled" });
  screenshots.push(name + ".png");
};
const fits = async (label) => {
  check(label + " palette fits the viewport without horizontal overflow", await palette().evaluate((n) => {
    const r = n.getBoundingClientRect();
    return r.left >= 0 && r.right <= innerWidth && r.top >= 0 && r.bottom <= innerHeight && n.scrollWidth <= n.clientWidth;
  }));
  check(label + " input, footer and close button remain visible", await palette().evaluate((n) => {
    const r = n.getBoundingClientRect();
    return [...n.querySelectorAll("input, footer, button")].every((el) => {
      const b = el.getBoundingClientRect(); return b.top >= r.top && b.bottom <= r.bottom && b.left >= r.left && b.right <= r.right;
    });
  }));
};
try {
  await page.goto(base);
  await page.locator(".auth-form").waitFor();
  const setup = await api("/api/setup/status", undefined, "GET");
  await page.getByLabel("아이디", { exact: true }).fill("reviewer");
  await page.getByLabel("비밀번호", { exact: true }).fill("browser-verification-only");
  if (!setup.configured) await page.getByLabel("비밀번호 확인", { exact: true }).fill("browser-verification-only");
  await page.getByRole("button", { name: setup.configured ? "로그인" : "워크스페이스 만들기", exact: true }).click();
  await page.locator(".library-view").waitFor();
  await api("/api/admin/storage/reset", { confirm: "RESET" });
  await page.reload();
  await page.locator(".library-view").waitFor();
  await open();
  check("An empty library has an explanatory empty state", await palette().getByText("아직 문서가 없어요", { exact: true }).isVisible());
  await page.keyboard.press("Enter");
  check("Enter on an empty library is harmless", await palette().isVisible());
  await close();
  const workspaces = {};
  for (const name of ["Product Studio", "Product API", "Research Lab", "자료 모음", "긴 워크스페이스 이름도 한눈에 구분할 수 있도록 확인하는 자료실"])
    workspaces[name] = (await api("/api/editor/workspaces", { name })).workspace;
  const fixtures = [
    ["Product Studio", "API 출시 계획"], ["Product Studio", "API 설계 노트"],
    ["Product API", "API API 가이드"], ["Research Lab", "Release Plan"],
    ["자료 모음", "회의 기록"], ["자료 모음", "C++ [draft] <script>"],
    ["긴 워크스페이스 이름도 한눈에 구분할 수 있도록 확인하는 자료실", "긴 문서 제목이 있어도 검색 결과와 키보드 이동 안내가 화면 안에 정렬되어 보이는지 확인합니다"],
    ...Array.from({ length: 16 }, (_, i) => ["자료 모음", `프로젝트 참고 자료 ${String(i + 1).padStart(2, "0")}`]),
  ];
  const notes = [];
  for (const [workspace, title] of fixtures)
    notes.push((await api("/api/editor/notes", { workspaceId: workspaces[workspace].id, title, content: `# ${title}\n\n검색과 문서 이동을 확인하는 예제입니다.` })).note);
  await page.reload();
  await page.locator(".library-view").waitFor();
  await page.waitForFunction((count) => Number(document.querySelector('.nav-item .count')?.textContent) === count, fixtures.length);
  const librarySearch = page.getByRole("textbox", { name: "문서 검색", exact: true });
  await librarySearch.fill("보이지 않는 필터");
  await open();
  check("Ctrl+P opens and focuses the palette instead of printing", await input().evaluate((n) => n === document.activeElement));
  check("Palette ignores the current library text filter", await options().count() === fixtures.length);
  await find("  PrOdUcT\t aPi  ");
  check("AND search spans workspace/title, ignores case and ranks occurrences", JSON.stringify(await titles()) === JSON.stringify(["API API 가이드", "API 설계 노트", "API 출시 계획"]));
  check("Both title and workspace matches are highlighted", await options().first().locator("mark").count() === 4);
  await page.keyboard.press("Meta+p");
  check("Cmd+P reuses the open palette without losing the query", await palette().count() === 1 && (await input().inputValue()).includes("PrOdUcT"));
  await fits("Desktop");
  await shot("01-desktop-ranked-results");
  await close();
  check("Escape restores the original input focus and filter", await librarySearch.evaluate((n) => n === document.activeElement) && await librarySearch.inputValue() === "보이지 않는 필터");
  await open("Meta+p");
  check("Cmd+P opens a fresh palette", await input().inputValue() === "");
  await find("product api");
  await page.keyboard.press("ArrowUp");
  check("Up wraps from first to last result", await activeTitle() === "API 출시 계획");
  await page.keyboard.press("ArrowDown");
  check("Down wraps back to the first result", await activeTitle() === "API API 가이드");
  await page.keyboard.press("ArrowDown");
  check("Arrow navigation retains input focus and updates the accessible active option", await input().evaluate((n) => n === document.activeElement && document.getElementById(n.getAttribute("aria-activedescendant"))?.getAttribute("aria-selected") === "true"));
  await page.keyboard.press("Enter");
  await page.getByRole("textbox", { name: "문서 제목", exact: true }).waitFor();
  check("Enter opens the keyboard-selected document", await page.getByLabel("문서 제목", { exact: true }).inputValue() === "API 설계 노트");
  check("Opening a result changes workspace and clears an unrelated list filter", (await page.locator(".workspace-nav .active").innerText()).includes("Product Studio") && await page.getByLabel("문서 검색", { exact: true }).inputValue() === "");
  await page.locator(".monaco-editor textarea").first().waitFor();
  await page.locator(".monaco-editor textarea").first().focus();
  await open("Meta+p");
  check("Shortcut works from the Monaco editor", await input().evaluate((n) => n === document.activeElement));
  await find("자료 회의");
  check("Korean search finds a document outside the selected workspace", JSON.stringify(await titles()) === JSON.stringify(["회의 기록"]));
  await input().dispatchEvent("keydown", { key: "Enter", code: "Enter", isComposing: true, keyCode: 229 });
  check("Korean IME confirmation does not open a document", await palette().isVisible());
  await find("nothing matches this query");
  check("No results are explained", await palette().getByText("일치하는 문서가 없어요", { exact: true }).isVisible());
  await page.keyboard.press("ArrowDown");
  await page.keyboard.press("Enter");
  check("Keyboard navigation with no results is harmless", await palette().isVisible());
  await shot("02-no-results");
  await find("c++ [draft]");
  check("Punctuation searches literally and HTML-like titles stay text", await options().count() === 1 && await options().first().locator("script").count() === 0 && (await titles())[0] === "C++ [draft] <script>");
  await find("");
  for (let i = 0; i < 18; i++) await page.keyboard.press("ArrowDown");
  check("Keyboard selection scrolls into view in a long result list", await palette().locator('[aria-selected="true"]').evaluate((n) => {
    const r = n.getBoundingClientRect(), list = n.parentElement.getBoundingClientRect();
    return r.top >= list.top - 1 && r.bottom <= list.bottom + 1;
  }));
  await find("release");
  check("Changing the query resets selection to its first result", await options().first().getAttribute("aria-selected") === "true");
  for (let i = 0; i < 4; i++) await page.keyboard.press("Tab");
  check("Tab focus stays inside the native modal", await palette().evaluate((n) => n.contains(document.activeElement)));
  await close();
  const titleInput = page.getByLabel("문서 제목", { exact: true });
  await titleInput.fill("API 설계 노트 작성 중");
  await open(); await find("product 설계"); await page.keyboard.press("Enter");
  check("Opening the current document preserves unsaved edits", await titleInput.inputValue() === "API 설계 노트 작성 중" && await page.getByRole("dialog").count() === 0);
  await open(); await find("research release"); await page.keyboard.press("Enter");
  const unsaved = page.getByRole("dialog", { name: "저장하지 않은 변경 사항", exact: true });
  await unsaved.waitFor();
  check("Opening another document preserves the unsaved-changes guard", await titleInput.inputValue() === "API 설계 노트 작성 중");
  await page.keyboard.press("Control+p");
  check("Quick-open does not cover an existing confirmation dialog", await palette().count() === 0 && await unsaved.isVisible());
  await unsaved.getByRole("button", { name: "계속 편집", exact: true }).click();
  check("Canceling navigation retains edits", await titleInput.inputValue() === "API 설계 노트 작성 중");
  await open(); await find("research release"); await page.keyboard.press("Enter");
  await unsaved.getByRole("button", { name: "저장 후 이동", exact: true }).click();
  await page.waitForFunction(() => document.querySelector('.title-input')?.value === "Release Plan");
  const savedNote = await api(`/api/editor/notes/${notes[1].id}`, undefined, "GET");
  check("Save-and-open persists edits before opening the selected document", savedNote.note.title === "API 설계 노트 작성 중");
  for (const [label, selector] of [["첨부 라이브러리", ".attachment-library"], ["서버 관리", ".admin-panel"]]) {
    await page.getByRole("button", { name: label, exact: true }).click();
    await page.locator(selector).waitFor();
    await open(); await find("research release");
    check(label + " supports global quick-open", await options().count() === 1);
    await close();
  }
  await page.locator(".topbar .palette-launch").click();
  await input().waitFor();
  await find("자료 회의");
  await options().first().click();
  await titleInput.waitFor();
  check("Clicking a result opens it from administration", await titleInput.inputValue() === "회의 기록");
  for (const [width, height] of [[1280, 520], [390, 844], [320, 844], [320, 390]]) {
    await page.setViewportSize({ width, height });
    check(`${width}×${height} header search and installation controls fit`, await page.locator(".topbar").evaluate((n) => {
      const boxes = [...n.querySelectorAll("button")].map((el) => el.getBoundingClientRect()).filter((r) => r.width);
      return boxes.every((r) => r.left >= 0 && r.right <= innerWidth) && !boxes.some((a, i) => boxes.slice(i + 1).some((b) => Math.min(a.right, b.right) - Math.max(a.left, b.left) > 1));
    }));
    await page.locator(".topbar .palette-launch").click();
    await input().waitFor();
    await fits(`${width}×${height}`);
    await shot(`03-${width}x${height}-results`);
    await find("워크스페이스");
    await fits(`${width}×${height} long names`);
    await shot(`04-${width}x${height}-long-names`);
    await close();
  }
  check("No browser JavaScript errors", errors.length === 0);
  await writeFile(out + "results.json", JSON.stringify({ checkedAt: new Date().toISOString(), checks, screenshots, errors }, null, 2) + "\n");
} catch (error) {
  await page.screenshot({ path: out + "failure.png" }).catch(() => {});
  throw error;
} finally { await browser.close(); }
