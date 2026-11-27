import { createRequire } from "node:module";
import { mkdir, writeFile } from "node:fs/promises";
import assert from "node:assert/strict";
const require = createRequire(import.meta.url);
const { chromium } = require("../frontend/node_modules/@playwright/test");
const base = "http://127.0.0.1:5502"; // Dedicated synthetic verification data only.
const out = new URL(
  `../docs/verification/${process.env.NOTEDOWN_VERIFICATION_OUTPUT || "enhancements"}/`,
  import.meta.url,
).pathname;
await mkdir(out, { recursive: true });
const browser = await chromium.launch({ channel: "chrome", headless: true });
const context = await browser.newContext({
  viewport: { width: 1440, height: 1000 },
  deviceScaleFactor: 1,
});
const page = await context.newPage();
const errors = [],
  checks = [];
page.on("pageerror", (e) => errors.push(e.message));
const check = (name, value = true) => {
  assert.ok(value, name);
  checks.push(name);
  console.log("PASS", name);
};
const api = async (path, body, method = "POST") => {
  const r = await context.request.fetch(base + path, {
    method,
    ...(body === undefined ? {} : { data: body }),
  });
  assert.ok(r.ok(), `${method} ${path}: ${await r.text()}`);
  return r.json();
};
const shot = async (name) => {
  if (
    (await page
      .getByRole("button", { name: "알림 닫기", exact: true })
      .count()) &&
    !(await page.locator("dialog[open]").count())
  )
    await page.getByRole("button", { name: "알림 닫기", exact: true }).click();
  // Let responsive transitions and visible image decoding settle before capture.
  await page.waitForTimeout(250);
  await page.screenshot({
    path: out + name + ".png",
    fullPage: true,
    animations: "disabled",
  });
};
const sortGroup = () =>
  page.getByRole("group", { name: "문서 정렬", exact: true });
const sortButton = (name) =>
  sortGroup().getByRole("button", { name, exact: true });
const directionButton = () => page.locator(".sort-direction-button");
const directionIs = async (direction) =>
  (await directionButton().getAttribute("aria-label")) ===
  `정렬 방향: ${direction}`;
const setDirection = async (direction) => {
  if (!(await directionIs(direction))) await directionButton().click();
  assert.ok(await directionIs(direction), `sort direction is ${direction}`);
};
const assertSortControlsFit = async (label) => {
  check(
    label,
    await page.locator(".library-toolbar").evaluate((controls) => {
      const nodes = [...controls.querySelectorAll("button")];
      const boxes = nodes.map((node) => node.getBoundingClientRect());
      const visible = boxes.every(
        (box) =>
          box.width > 0 &&
          box.height > 0 &&
          box.left >= 0 &&
          box.right <= innerWidth,
      );
      const overlap = boxes.some((box, index) =>
        boxes
          .slice(index + 1)
          .some(
            (other) =>
              Math.min(box.right, other.right) -
                Math.max(box.left, other.left) >
                1 &&
              Math.min(box.bottom, other.bottom) -
                Math.max(box.top, other.top) >
                1,
          ),
      );
      return (
        visible &&
        !overlap &&
        document.documentElement.scrollWidth <= innerWidth
      );
    }),
  );
};
const noteOrder = async () =>
  page
    .locator(".note-card[data-note-id],.document-table tbody tr")
    .evaluateAll((nodes) => nodes.map((n) => n.dataset.noteId));
const saved = () =>
  page.waitForFunction(
    () =>
      document.querySelector(".save-state")?.textContent === "저장됨" &&
      !document.querySelector(".save-button .spin") &&
      !document.querySelector(".note-loading"),
  );
const sourceLine = () =>
  page.evaluate(() => {
    const pane = document
      .querySelector(".monaco-container")
      .getBoundingClientRect();
    return (
      [...document.querySelectorAll(".monaco-editor .line-numbers")]
        .filter((n) => {
          const r = n.getBoundingClientRect();
          return (
            r.top >= pane.top - 1 && r.top < pane.bottom && n.textContent.trim()
          );
        })
        .map((n) => Number(n.textContent))[0] || 0
    );
  });
try {
  await page.goto(base);
  await page.waitForSelector(".auth-form");
  const status = await (
    await context.request.get(base + "/api/setup/status")
  ).json();
  await page.getByLabel("아이디", { exact: true }).fill("reviewer");
  await page
    .getByLabel("비밀번호", { exact: true })
    .fill("browser-verification-only");
  if (!status.configured) {
    await page
      .getByLabel("비밀번호 확인", { exact: true })
      .fill("browser-verification-only");
    await page
      .getByRole("button", { name: "워크스페이스 만들기", exact: true })
      .click();
  } else
    await page.getByRole("button", { name: "로그인", exact: true }).click();
  await page.waitForSelector(".library-view");
  await api("/api/admin/storage/reset", { confirm: "RESET" });
  const workspace = (await api("/api/editor/workspaces", { name: "제품 기획" }))
    .workspace;
  const a = await api("/api/editor/notes", {
    workspaceId: workspace.id,
    title: "가이드 · 디자인 원칙",
    content: "# 디자인 원칙\n\n읽기 편한 문서 경험을 만듭니다.",
  });
  const b = await api("/api/editor/notes", {
    workspaceId: workspace.id,
    title: "브랜드 참고자료",
    content: "# 브랜드 참고자료",
  });
  const c = await api("/api/editor/notes", {
    workspaceId: workspace.id,
    title: "운영 체크리스트",
    content: "# 운영 체크리스트",
  });
  const image = await page.evaluate(() => {
    const canvas = document.createElement("canvas");
    canvas.width = 720;
    canvas.height = 320;
    const x = canvas.getContext("2d");
    x.fillStyle = "#f1edfa";
    x.fillRect(0, 0, 720, 320);
    x.fillStyle = "#8473bc";
    x.font = "28px sans-serif";
    x.fillText("A clearer space for your ideas.", 34, 65);
    [130, 190, 230, 300, 365, 420].forEach((h, i) => {
      x.fillStyle = ["#d5cbea", "#b7a6dc", "#9380c4"][i % 3];
      x.fillRect(40 + i * 106, 270 - h / 2, 68, h / 2);
    });
    return canvas.toDataURL().split(",")[1];
  });
  const upload = async (name, mime, buffer) => {
    const r = await context.request.post(
      base + "/api/editor/notes/" + a.note.id + "/attachments",
      { multipart: { file: { name, mimeType: mime, buffer } } },
    );
    assert.equal(r.status(), 201, await r.text());
    return r.json();
  };
  const picture = await upload(
    "공간의 방향.png",
    "image/png",
    Buffer.from(image, "base64"),
  );
  const shared = await upload(
    "팀 참고자료.txt",
    "text/plain",
    Buffer.from("Shared material referenced by a different document."),
  );
  const unused = await upload(
    "사용하지 않는 초안.pdf",
    "application/pdf",
    Buffer.from("%PDF-1.4\nSynthetic unused draft"),
  );
  const racing = await upload(
    "정리 후보.txt",
    "text/plain",
    Buffer.from("This candidate will acquire a reference during confirmation."),
  );
  const longContent =
    "# 가이드 · 디자인 원칙\n\n명료한 기록, 연결된 생각.\n\n" +
    Array.from(
      { length: 45 },
      (_, i) =>
        `## ${String(i + 1).padStart(2, "0")}. ${["기록의 시작", "읽기 편한 여백", "흐름을 연결하기", "작은 개선의 반복"][i % 4]}\n\n${"아이디어를 짧게 기록하고 필요한 맥락을 함께 남깁니다. ".repeat((i % 5) + 1)}\n\n${i % 3 === 0 ? "> 문서의 구조와 실제 미리보기의 위치를 함께 따라갑니다.\n\n" : ""}${i % 4 === 0 ? "| 기준 | 확인 내용 |\n| --- | --- |\n| 명료함 | 제목과 문단의 관계 |\n| 연결 | 참고 자료와 첨부 이미지 |\n\n" : ""}${i === 4 ? picture.markdown + "\n\n" : ""}${i % 6 === 0 ? "```python\nfor idea in ideas:\n    keep(idea)\n```\n\n" : ""}`,
    ).join("");
  const aSaved = await api(
    "/api/editor/notes/" + a.note.id,
    { title: a.note.title, content: longContent, revision: a.revision },
    "PUT",
  );
  await api(
    "/api/editor/notes/" + b.note.id,
    {
      title: b.note.title,
      content: "# 브랜드 참고자료\n\n" + shared.markdown,
      revision: b.revision,
    },
    "PUT",
  );
  // Updated date now differs from creation order.
  await api(
    "/api/editor/notes/" + a.note.id,
    {
      title: a.note.title,
      content: longContent + "\n마지막 검토 완료.\n",
      revision: aSaved.revision,
    },
    "PUT",
  );
  await page.reload();
  await page.waitForSelector(".note-card");
  check(
    "new notes expose actual creation dates",
    [a, b, c].every((n) => n.note.createdAtMs > 0),
  );
  check(
    "sort criterion has three toggle buttons and exactly one active choice",
    (await sortGroup().getByRole("button").count()) === 3 &&
      (await sortGroup().locator('button[aria-pressed="true"]').count()) === 1,
  );
  check(
    "sort direction uses a single button",
    (await directionButton().count()) === 1,
  );
  await sortButton("이름").click();
  await setDirection("오름차순");
  await sortButton("이름").click();
  check(
    "clicking selected criterion keeps direction and active choice",
    (await directionIs("오름차순")) &&
      (await sortButton("이름").getAttribute("aria-pressed")) === "true" &&
      (await sortGroup().locator('button[aria-pressed="true"]').count()) === 1,
  );
  check(
    "name ascending sort",
    JSON.stringify(await noteOrder()) ===
      JSON.stringify([a.note.id, b.note.id, c.note.id]),
  );
  const originalDirectionButton = await directionButton().elementHandle();
  await directionButton().click();
  check(
    "same direction button toggles ascending to descending",
    (await directionIs("내림차순")) &&
      (await directionButton().evaluate(
        (button, original) => button === original,
        originalDirectionButton,
      )),
  );
  check(
    "name descending sort",
    JSON.stringify(await noteOrder()) ===
      JSON.stringify([c.note.id, b.note.id, a.note.id]),
  );
  await sortButton("생성일").click();
  check(
    "created date descending sort",
    JSON.stringify(await noteOrder()) ===
      JSON.stringify([c.note.id, b.note.id, a.note.id]),
  );
  await setDirection("오름차순");
  check(
    "created date ascending sort",
    JSON.stringify(await noteOrder()) ===
      JSON.stringify([a.note.id, b.note.id, c.note.id]),
  );
  await sortButton("수정일").click();
  await setDirection("내림차순");
  check(
    "modified date differs from created date",
    (await noteOrder())[0] === a.note.id,
  );
  await page
    .getByRole("button", { name: "테이블로 보기", exact: true })
    .click();
  await page.locator(".document-table").waitFor();
  check(
    "card/table toggle renders actual table",
    (await page.locator(".document-table tbody tr").count()) === 3,
  );
  await assertSortControlsFit(
    "desktop sorting controls are visible without overlap",
  );
  await shot("01-document-table");
  await page
    .getByRole("button", { name: "이름으로 정렬", exact: true })
    .click();
  check(
    "table header sets ascending name sort",
    (await sortButton("이름").getAttribute("aria-pressed")) === "true" &&
      (await directionIs("오름차순")),
  );
  await page
    .getByRole("button", { name: "이름으로 정렬", exact: true })
    .click();
  check("table header toggles sort direction", await directionIs("내림차순"));
  await page.reload();
  await page.locator(".document-table").waitFor();
  check(
    "sort and layout preferences survive reload",
    (await sortButton("이름").getAttribute("aria-pressed")) === "true" &&
      (await directionIs("내림차순")),
  );
  await page.getByRole("button", { name: "카드로 보기", exact: true }).click();
  await page.locator(".note-card").first().waitFor();
  check("table switches back to cards");
  await shot("02-document-cards");
  await page.locator(`.note-card[data-note-id="${a.note.id}"]`).click();
  await page.waitForSelector(".monaco-editor");
  await page.waitForSelector(".markdown-body [data-source-line]");
  await saved();
  const source = await page.locator(".monaco-container").boundingBox();
  await page.mouse.move(
    source.x + source.width / 2,
    source.y + source.height / 2,
  );
  // Monaco clamps a Chromium wheel event; exercise a real sequence of wheel gestures.
  for (let i = 0; i < 20; i++) {
    await page.mouse.wheel(0, 1800);
    await page.waitForTimeout(20);
  }
  await page.waitForTimeout(350);
  check(
    "source scrolling moves preview",
    (await page.locator(".markdown-body").evaluate((e) => e.scrollTop)) > 500,
  );
  let firstLine = await sourceLine();
  check("Monaco scrolled from first source line", firstLine > 10);
  const preview = await page.locator(".markdown-body").boundingBox();
  await page.mouse.move(
    preview.x + preview.width / 2,
    preview.y + preview.height / 2,
  );
  await page.mouse.wheel(0, 1500);
  await page.waitForTimeout(350);
  check("preview scrolling moves source", (await sourceLine()) > firstLine);
  const before = await page
    .locator(".markdown-body")
    .evaluate((e) => e.scrollTop);
  await page.waitForTimeout(250);
  check(
    "bidirectional sync settles without feedback drift",
    Math.abs(
      (await page.locator(".markdown-body").evaluate((e) => e.scrollTop)) -
        before,
    ) < 2,
  );
  await shot("03-synchronized-scroll");
  await page
    .locator(".markdown-body")
    .evaluate((e) => (e.scrollTop = e.scrollHeight));
  await page.waitForTimeout(300);
  check("preview end maps to source end", (await sourceLine()) > 150);
  await page.locator(".markdown-body").evaluate((e) => (e.scrollTop = 0));
  await page.waitForTimeout(300);
  check(
    "preview beginning maps to source beginning",
    (await sourceLine()) <= 2,
  );
  await page.getByRole("button", { name: "미리보기", exact: true }).click();
  await page.getByRole("button", { name: "분할", exact: true }).click();
  await page.waitForSelector(".monaco-editor");
  await page.waitForTimeout(100);
  await page.locator(".markdown-body").evaluate((e) => (e.scrollTop = 1000));
  await page.waitForTimeout(300);
  check(
    "scroll sync reconnects after switching editor modes",
    (await sourceLine()) > 2,
  );
  // Saved reference counts consider every document, not only the attachment owner.
  await page
    .getByRole("button", { name: "첨부 라이브러리", exact: true })
    .click();
  await page.locator(".attachment-library").waitFor();
  const library = await api("/api/editor/attachment-library", undefined, "GET");
  check(
    "global library includes images and files",
    library.attachments.length === 4,
  );
  check(
    "other document reference prevents cleanup",
    library.attachments
      .find((item) => item.id === shared.attachment.id)
      .references.some((n) => n.id === b.note.id),
  );
  check(
    "unused candidates distinguished",
    library.attachments.filter((item) => !item.referenced).length === 2,
  );
  await page.waitForFunction(
    () => document.querySelectorAll(".al-file-card").length === 4,
  );
  await page.waitForFunction(() => {
    const image = document.querySelector(".al-image");
    return image?.complete && image.naturalWidth > 0;
  });
  check("attachment image thumbnail loads");
  await shot("04-attachment-library");
  check(
    "attachment list can scroll to its last card",
    await page.locator(".attachment-library").evaluate((e) => {
      e.scrollTop = e.scrollHeight;
      const last = e
        .querySelector(".al-file-card:last-child")
        .getBoundingClientRect();
      const bounds = e.getBoundingClientRect();
      const visible = last.bottom <= bounds.bottom + 1;
      e.scrollTop = 0;
      return visible;
    }),
  );
  await page
    .getByLabel("첨부 파일 종류")
    .getByRole("button", { name: /^이미지/ })
    .click();
  await page.waitForFunction(
    () => document.querySelectorAll(".al-file-card").length === 1,
  );
  check("image type filter");
  await page
    .getByLabel("첨부 파일 종류")
    .getByRole("button", { name: /^파일/ })
    .click();
  await page.waitForFunction(
    () => document.querySelectorAll(".al-file-card").length === 3,
  );
  check("file type filter");
  await page
    .getByRole("button", {
      name: "첨부 파일 자세히 보기: 팀 참고자료.txt",
      exact: true,
    })
    .click();
  await page.getByRole("dialog").waitFor();
  check(
    "attachment details list the referencing document",
    (await page
      .getByRole("dialog")
      .getByRole("button", { name: b.note.title, exact: true })
      .count()) === 1,
  );
  await page
    .getByRole("dialog")
    .getByRole("button", { name: b.note.title, exact: true })
    .click();
  await page.waitForSelector(".monaco-editor");
  await saved();
  check(
    "attachment reference opens the right document",
    (await page.getByLabel("문서 제목", { exact: true }).inputValue()) ===
      b.note.title,
  );
  await page
    .getByRole("button", { name: "첨부 라이브러리", exact: true })
    .click();
  await page.locator(".al-file-card").first().waitFor();
  // Follow-up selectors below track the accessible component contract.
  await page
    .getByRole("searchbox", { name: "첨부 파일 검색", exact: true })
    .fill("팀 참고자료");
  await page.waitForFunction(
    () => document.querySelectorAll(".al-file-card").length === 1,
  );
  check("attachment filename search");
  await page
    .getByRole("searchbox", { name: "첨부 파일 검색", exact: true })
    .fill("");
  await page
    .getByLabel("첨부 파일 종류")
    .getByRole("button", { name: /^미사용/ })
    .click();
  await page.waitForFunction(
    () => document.querySelectorAll(".al-file-card").length === 2,
  );
  check("unreferenced filter");
  await page
    .getByLabel("사용하지 않는 초안.pdf 정리 대상으로 선택", { exact: true })
    .check();
  await page.getByRole("button", { name: /선택한.*정리/ }).click();
  await page.getByRole("dialog").waitFor();
  await shot("05-cleanup-confirmation");
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "1개 파일 삭제", exact: true })
    .click();
  await page.waitForFunction(() => !document.querySelector("dialog[open]"));
  check(
    "explicit cleanup deletes selected unused file",
    (await context.request.get(base + unused.attachment.url)).status() === 404,
  );
  check(
    "cleanup preserves referenced attachment",
    (await context.request.get(base + shared.attachment.url)).status() === 200,
  );
  await page
    .getByLabel("정리 후보.txt 정리 대상으로 선택", { exact: true })
    .check();
  await page.getByRole("button", { name: /선택한.*정리/ }).click();
  await page.getByRole("dialog").waitFor();
  const latestB = await api("/api/editor/notes/" + b.note.id, undefined, "GET");
  await api(
    "/api/editor/notes/" + b.note.id,
    {
      title: latestB.note.title,
      content: latestB.content + "\n" + racing.markdown,
      revision: latestB.revision,
    },
    "PUT",
  );
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "1개 파일 삭제", exact: true })
    .click();
  await page.getByRole("dialog").getByRole("alert").waitFor();
  check(
    "new reference after confirmation blocks cleanup",
    (await context.request.get(base + racing.attachment.url)).status() === 200,
  );
  await shot("06-cleanup-race");
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "취소", exact: true })
    .click();
  await page
    .getByRole("button", { name: "첨부 파일 새로고침", exact: true })
    .click();
  await page
    .getByLabel("첨부 파일 종류")
    .getByRole("button", { name: /^전체/ })
    .click();
  await page.waitForFunction(
    () => document.querySelectorAll(".al-file-card").length === 3,
  );
  await page.setViewportSize({ width: 390, height: 844 });
  await shot("07-attachment-mobile");
  check(
    "attachment screen has no mobile page overflow",
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  );
  await page.getByRole("button", { name: "메뉴 열기", exact: true }).click();
  await page.getByRole("button", { name: /문서 라이브러리/ }).click();
  await page.waitForSelector(".library-view");
  await page
    .getByRole("button", { name: "테이블로 보기", exact: true })
    .click();
  await assertSortControlsFit(
    "390px sorting controls are visible without overlap",
  );
  await shot("08-table-mobile");
  await page.setViewportSize({ width: 320, height: 740 });
  await assertSortControlsFit(
    "320px sorting controls are visible without overlap",
  );
  await shot("10-sort-controls-narrow-mobile");
  await page.setViewportSize({ width: 390, height: 844 });
  check(
    "mobile table scroll stays within page",
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  );
  await page
    .locator(".table-document-link")
    .filter({ hasText: a.note.title })
    .click();
  await page.waitForSelector(".monaco-editor");
  await page.locator(".markdown-body").evaluate((e) => (e.scrollTop = 900));
  await page.waitForTimeout(350);
  check("vertical mobile split sync", (await sourceLine()) > 2);
  await shot("09-scroll-mobile");
  check("no browser JavaScript errors", errors.length === 0);
  await writeFile(
    out + "browser-results.json",
    JSON.stringify(
      { checkedAt: new Date().toISOString(), checks, errors },
      null,
      2,
    ),
  );
  console.log(`Completed ${checks.length} enhancement checks`);
} catch (error) {
  await shot("failure");
  console.error("Page errors:", errors);
  throw error;
} finally {
  await browser.close();
}
