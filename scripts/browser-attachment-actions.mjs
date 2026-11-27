import { createRequire } from "node:module";
import { mkdir, writeFile } from "node:fs/promises";
import assert from "node:assert/strict";

const require = createRequire(import.meta.url);
const { chromium } = require("../frontend/node_modules/@playwright/test");
// Intentionally fixed: all destructive requests use the dedicated synthetic fixture.
const base = "http://127.0.0.1:5502";
const out = new URL("../docs/verification/attachment-actions/", import.meta.url)
  .pathname;
await mkdir(out, { recursive: true });
const browser = await chromium.launch({ channel: "chrome", headless: true });
const context = await browser.newContext({
  viewport: { width: 1440, height: 1000 },
  deviceScaleFactor: 1,
});
const page = await context.newPage();
const checks = [],
  errors = [],
  cleanupRequests = [],
  screenshots = [];
page.on("pageerror", (error) => errors.push(error.message));
page.on("request", (request) => {
  if (
    request.url() === base + "/api/editor/attachments/cleanup" &&
    request.method() === "POST"
  )
    cleanupRequests.push(request.postDataJSON());
});
const check = (name, result = true) => {
  assert.ok(result, name);
  checks.push(name);
  console.log("PASS", name);
};
const api = async (path, body, method = "POST") => {
  const response = await context.request.fetch(base + path, {
    method,
    ...(body === undefined ? {} : { data: body }),
  });
  assert.ok(response.ok(), `${method} ${path}: ${await response.text()}`);
  return response.json();
};
const library = () => api("/api/editor/attachment-library", undefined, "GET");
const bulkButton = () =>
  page.getByRole("button", { name: /^미사용 파일 정리/ });
const selectedButton = () =>
  page.getByRole("button", { name: /^선택한 파일 정리/ });
const dialog = () => page.getByRole("dialog");
const search = () =>
  page.getByRole("searchbox", { name: "첨부 파일 검색", exact: true });
const formatSize = (value) =>
  value < 1024
    ? `${value.toLocaleString("ko-KR")} B`
    : value < 1024 ** 2
      ? `${(value / 1024).toFixed(1)} KB`
      : `${(value / 1024 ** 2).toFixed(1)} MB`;
const shot = async (name) => {
  const dismiss = page.getByRole("button", { name: "알림 닫기", exact: true });
  if ((await dismiss.count()) && !(await page.locator("dialog[open]").count()))
    await dismiss.click();
  await page.waitForTimeout(180);
  await page.screenshot({
    path: out + name + ".png",
    fullPage: true,
    animations: "disabled",
  });
  screenshots.push(name + ".png");
};
const waitCards = (count) =>
  page.waitForFunction(
    (expected) =>
      document.querySelectorAll(".al-file-card").length === expected,
    count,
  );
const refresh = async (count) => {
  await page
    .getByRole("button", { name: "첨부 파일 새로고침", exact: true })
    .click();
  await page.waitForFunction(
    () =>
      !document.querySelector(".al-refresh-status") &&
      !document.querySelector(".al-page-heading button:disabled"),
  );
  if (count !== undefined) await waitCards(count);
};
const assertLayout = async (label) => {
  await page.locator(".attachment-library").evaluate((node) => {
    node.scrollTop = 0;
  });
  check(
    label + " search and cleanup controls fit without overlap",
    await page.locator(".al-selection-bar").evaluate((bar) => {
      const search = bar.querySelector(".al-search");
      const buttons = [...bar.querySelectorAll("button")].filter((node) =>
        /^(미사용 파일 정리|선택한 파일 정리)/.test(node.textContent.trim()),
      );
      if (!search || buttons.length !== 2) return false;
      const boxes = [search, ...buttons].map((node) =>
        node.getBoundingClientRect(),
      );
      const fit = boxes.every(
        (box) => box.width > 0 && box.left >= 0 && box.right <= innerWidth + 1,
      );
      const overlaps = boxes.some((box, index) =>
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
        fit && !overlaps && document.documentElement.scrollWidth <= innerWidth
      );
    }),
  );
  const bulk = await bulkButton().boundingBox(),
    selected = await selectedButton().boundingBox();
  check(
    label + " unused cleanup is left of selected cleanup",
    bulk.x < selected.x && Math.abs(bulk.y - selected.y) < 2,
  );
};
const assertDialogFits = async (label) =>
  check(
    label + " cleanup dialog and actions fit viewport",
    await dialog().evaluate((node) => {
      const bounds = node.getBoundingClientRect();
      const actionBoxes = [
        ...node.querySelectorAll(".al-dialog-actions button"),
      ].map((button) => button.getBoundingClientRect());
      return (
        bounds.left >= 0 &&
        bounds.right <= innerWidth &&
        bounds.top >= 0 &&
        bounds.bottom <= innerHeight &&
        node.scrollWidth <= node.clientWidth &&
        actionBoxes.every(
          (box) =>
            box.left >= bounds.left &&
            box.right <= bounds.right &&
            box.top >= bounds.top &&
            box.bottom <= bounds.bottom &&
            box.bottom <= innerHeight,
        )
      );
    }),
  );
const verifyModalFiles = async (files, label) => {
  check(
    label + " lists every candidate once",
    (await dialog().locator(".al-cleanup-list li").count()) === files.length,
  );
  const entries = await dialog()
    .locator(".al-cleanup-list li")
    .allTextContents();
  check(
    label + " lists candidate names and individual sizes",
    files.every((file) =>
      entries.some(
        (entry) =>
          entry.includes(file.fileName) &&
          entry.includes(formatSize(file.size)),
      ),
    ),
  );
  const total = files.reduce((sum, file) => sum + file.size, 0);
  const text = await dialog().innerText();
  check(
    label + " shows total count and total storage",
    text.includes(`${files.length}개 파일`) &&
      text.includes("총용량") &&
      text.includes(formatSize(total)),
  );
};

try {
  await page.goto(base);
  await page.waitForSelector(".auth-form");
  const status = await api("/api/setup/status", undefined, "GET");
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
  const workspace = (
    await api("/api/editor/workspaces", { name: "디자인 스튜디오" })
  ).workspace;
  const owner = await api("/api/editor/notes", {
    workspaceId: workspace.id,
    title: "브랜드 디자인 가이드",
    content: "# 브랜드 디자인 가이드\n\n명료한 기록을 위한 디자인 자료입니다.",
  });
  const reference = await api("/api/editor/notes", {
    workspaceId: workspace.id,
    title: "팀 공유 자료",
    content: "# 팀 공유 자료",
  });
  const png = Buffer.from(
    await page.evaluate(() => {
      const canvas = document.createElement("canvas");
      canvas.width = 720;
      canvas.height = 360;
      const ctx = canvas.getContext("2d");
      ctx.fillStyle = "#eeeaf8";
      ctx.fillRect(0, 0, 720, 360);
      ctx.fillStyle = "#8a76b6";
      ctx.font = "36px sans-serif";
      ctx.fillText("A place for every idea.", 42, 76);
      [90, 130, 160, 200, 240].forEach((height, index) => {
        ctx.fillStyle = ["#d6c9ed", "#b5a2d5", "#9581ba"][index % 3];
        ctx.fillRect(45 + index * 130, 310 - height / 2, 84, height / 2);
      });
      return canvas.toDataURL().split(",")[1];
    }),
    "base64",
  );
  const upload = async (name, mimeType, buffer) => {
    const response = await context.request.post(
      base + "/api/editor/notes/" + owner.note.id + "/attachments",
      { multipart: { file: { name, mimeType, buffer } } },
    );
    assert.equal(response.status(), 201, await response.text());
    return response.json();
  };
  const picture = await upload("브랜드 심볼.png", "image/png", png);
  const shared = await upload(
    "팀 공유 가이드.txt",
    "text/plain",
    Buffer.alloc(2048, "G"),
  );
  const draft = await upload(
    "프로젝트 소개 초안.pdf",
    "application/pdf",
    Buffer.from("%PDF-1.4\n" + "D".repeat(1015)),
  );
  const memo = await upload(
    "지난 회의 메모.txt",
    "text/plain",
    Buffer.alloc(2048, "M"),
  );
  const unusedImage = await upload("사용하지 않는 시안.png", "image/png", png);
  await api(
    "/api/editor/notes/" + owner.note.id,
    {
      title: owner.note.title,
      content: "# 브랜드 디자인 가이드\n\n" + picture.markdown,
      revision: owner.revision,
    },
    "PUT",
  );
  await api(
    "/api/editor/notes/" + reference.note.id,
    {
      title: reference.note.title,
      content: "# 팀 공유 자료\n\n" + shared.markdown,
      revision: reference.revision,
    },
    "PUT",
  );
  await page.reload();
  await page.waitForSelector(".library-view");
  await page
    .getByRole("button", { name: "첨부 라이브러리", exact: true })
    .click();
  await waitCards(5);
  await page.waitForFunction(() =>
    [...document.querySelectorAll(".al-image")].every(
      (image) => image.complete && image.naturalWidth > 0,
    ),
  );
  const initial = await library();
  const candidates = initial.attachments.filter((file) => !file.referenced);
  check(
    "fixture has two referenced and three unused files",
    initial.attachments.length === 5 && candidates.length === 3,
  );
  check(
    "search appears in selection row",
    await search().evaluate((node) =>
      Boolean(node.closest(".al-selection-bar")),
    ),
  );
  check(
    "search no longer appears beside type filters",
    (await page.locator(".al-toolbar input[type=search]").count()) === 0,
  );
  check(
    "saved-documents text and icon are removed",
    (await page.getByText("저장된 문서 기준", { exact: true }).count()) === 0 &&
      (await page.locator(".al-scan-note").count()) === 0,
  );
  check(
    "visible-unused select-all checkbox is removed",
    (await page
      .getByLabel("표시된 미사용 파일 선택", { exact: true })
      .count()) === 0,
  );
  check(
    "individual unused-file checkboxes remain",
    (await page.locator(".al-file-selection input[type=checkbox]").count()) ===
      3,
  );
  check(
    "bulk cleanup works without manual selection",
    (await bulkButton().isEnabled()) && (await selectedButton().isDisabled()),
  );
  await assertLayout("1440px desktop");
  await shot("01-desktop-library");

  for (const [width, height] of [
    [390, 844],
    [320, 740],
  ]) {
    await page.setViewportSize({ width, height });
    await assertLayout(`${width}px mobile`);
    await shot(`02-mobile-${width}-library`);
    await bulkButton().click();
    await page
      .getByRole("heading", { name: "미사용 파일을 정리할까요?", exact: true })
      .waitFor();
    await verifyModalFiles(candidates, `${width}px mobile modal`);
    await assertDialogFits(`${width}px mobile`);
    await shot(`03-mobile-${width}-confirmation`);
    await dialog().getByRole("button", { name: "취소", exact: true }).click();
    if (width === 320) {
      const candidateCheckbox = page.getByLabel(
        unusedImage.attachment.fileName + " 정리 대상으로 선택",
        { exact: true },
      );
      await candidateCheckbox.check();
      await assertLayout("320px mobile with a selected file");
      await shot("03-mobile-320-selection");
      await candidateCheckbox.uncheck();
    }
  }
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page
    .getByRole("group", { name: "첨부 파일 종류", exact: true })
    .getByRole("button", { name: /^이미지/ })
    .click();
  await search().fill("사용하지 않는 시안");
  await waitCards(1);
  check(
    "search and image filter restrict the visible list",
    (await page.locator(".al-file-title").innerText()) ===
      unusedImage.attachment.fileName,
  );
  await bulkButton().click();
  await page
    .getByRole("heading", { name: "미사용 파일을 정리할까요?", exact: true })
    .waitFor();
  await verifyModalFiles(candidates, "filtered bulk modal");
  check(
    "bulk modal includes files hidden by search and type filters",
    (await dialog().innerText()).includes(draft.attachment.fileName) &&
      (await dialog().innerText()).includes(memo.attachment.fileName),
  );
  await shot("04-desktop-confirmation");
  await dialog().getByRole("button", { name: "취소", exact: true }).click();
  check("cancel never sends cleanup request", cleanupRequests.length === 0);
  check(
    "cancel preserves all files",
    (await library()).attachments.length === 5,
  );
  await bulkButton().click();
  const cleanupResponsePromise = page.waitForResponse(
    (response) =>
      response.url() === base + "/api/editor/attachments/cleanup" &&
      response.request().method() === "POST",
  );
  await dialog()
    .getByRole("button", { name: "3개 파일 삭제", exact: true })
    .click();
  const cleanupResponse = await cleanupResponsePromise;
  check(
    "bulk cleanup succeeds with all three candidates",
    cleanupResponse.status() === 200 &&
      (await cleanupResponse.json()).deletedCount === 3,
  );
  await page.waitForFunction(() => !document.querySelector("dialog[open]"));
  check(
    "bulk sends every unused ID once",
    cleanupRequests.length === 1 &&
      cleanupRequests[0].attachmentIds.length === 3 &&
      candidates.every((file) =>
        cleanupRequests[0].attachmentIds.includes(file.id),
      ),
  );
  const afterBulk = await library();
  check(
    "bulk preserves files referenced from both owner and other documents",
    afterBulk.attachments.length === 2 &&
      afterBulk.attachments.every((file) => file.referenced),
  );
  check(
    "deleted files no longer download",
    (
      await Promise.all(
        candidates.map((file) => context.request.get(base + file.url)),
      )
    ).every((response) => response.status() === 404),
  );
  check(
    "referenced files still download",
    (await context.request.get(base + picture.attachment.url)).status() ===
      200 &&
      (await context.request.get(base + shared.attachment.url)).status() ===
        200,
  );
  await page.waitForFunction(
    () =>
      [...document.querySelectorAll("button")].find(
        (button) => button.textContent.trim() === "미사용 파일 정리",
      )?.disabled,
  );
  check(
    "bulk cleanup is disabled when no candidates remain",
    await bulkButton().isDisabled(),
  );

  const racing = await upload(
    "참조가 추가될 예정인 자료.txt",
    "text/plain",
    Buffer.alloc(1024, "R"),
  );
  const independent = await upload(
    "오래된 기획안.pdf",
    "application/pdf",
    Buffer.from("%PDF-1.4\nSynthetic independent unused draft"),
  );
  await search().fill("");
  await page
    .getByRole("group", { name: "첨부 파일 종류", exact: true })
    .getByRole("button", { name: /^전체/ })
    .click();
  await refresh(4);
  await bulkButton().click();
  check(
    "race fixture opens two-file bulk modal",
    (await dialog().locator(".al-cleanup-list li").count()) === 2,
  );
  const referenceLatest = await api(
    "/api/editor/notes/" + reference.note.id,
    undefined,
    "GET",
  );
  await api(
    "/api/editor/notes/" + reference.note.id,
    {
      title: referenceLatest.note.title,
      content: referenceLatest.content + "\n\n" + racing.markdown,
      revision: referenceLatest.revision,
    },
    "PUT",
  );
  const conflictResponsePromise = page.waitForResponse(
    (response) =>
      response.url() === base + "/api/editor/attachments/cleanup" &&
      response.request().method() === "POST",
  );
  await dialog()
    .getByRole("button", { name: "2개 파일 삭제", exact: true })
    .click();
  const conflictResponse = await conflictResponsePromise;
  await dialog().getByRole("alert").waitFor();
  check(
    "new reference after opening modal blocks cleanup with conflict",
    conflictResponse.status() === 409,
  );
  const afterConflict = await library();
  check(
    "conflict keeps every file in the requested batch",
    afterConflict.attachments.length === 4 &&
      afterConflict.attachments.some(
        (file) => file.id === racing.attachment.id && file.referenced,
      ) &&
      afterConflict.attachments.some(
        (file) => file.id === independent.attachment.id && !file.referenced,
      ),
  );
  check(
    "conflict prevents another stale delete and offers refresh",
    (await dialog()
      .getByRole("button", { name: "2개 파일 삭제", exact: true })
      .count()) === 0 &&
      (await dialog()
        .getByRole("button", { name: "목록 다시 확인", exact: true })
        .isEnabled()),
  );
  await shot("05-conflict-protection");
  await dialog()
    .getByRole("button", { name: "목록 다시 확인", exact: true })
    .click();
  await page.waitForFunction(
    () =>
      document.querySelectorAll(".al-file-selection input[type=checkbox]")
        .length === 1,
  );
  await page
    .getByLabel(independent.attachment.fileName + " 정리 대상으로 선택", {
      exact: true,
    })
    .check();
  check(
    "individual selection still enables selected cleanup",
    await selectedButton().isEnabled(),
  );
  await selectedButton().click();
  await page
    .getByRole("heading", {
      name: "선택한 미사용 파일을 정리할까요?",
      exact: true,
    })
    .waitFor();
  check(
    "selected cleanup modal contains only selected candidate",
    (await dialog().locator(".al-cleanup-list li").count()) === 1 &&
      (await dialog().innerText()).includes(independent.attachment.fileName),
  );
  await shot("06-selected-confirmation");
  await dialog()
    .getByRole("button", { name: "1개 파일 삭제", exact: true })
    .click();
  await page.waitForFunction(() => !document.querySelector("dialog[open]"));
  const final = await library();
  check(
    "selected cleanup deletes only selected file",
    final.attachments.length === 3 &&
      final.attachments.every((file) => file.referenced) &&
      !final.attachments.some((file) => file.id === independent.attachment.id),
  );
  await waitCards(3);
  await page.waitForFunction(
    () =>
      [...document.querySelectorAll("button")].find(
        (button) => button.textContent.trim() === "미사용 파일 정리",
      )?.disabled,
  );
  check(
    "both cleanup buttons disabled after all unused files are removed",
    (await bulkButton().isDisabled()) && (await selectedButton().isDisabled()),
  );
  await shot("07-no-unused-files");
  // A realistically long list must scroll independently of the action footer.
  for (let index = 1; index <= 12; index++) {
    await upload(
      `보관용 초안 ${String(index).padStart(2, "0")}.txt`,
      "text/plain",
      Buffer.alloc(128, "L"),
    );
  }
  await refresh(15);
  await page.setViewportSize({ width: 320, height: 740 });
  await bulkButton().click();
  await dialog().locator(".al-cleanup-list li").first().waitFor();
  check(
    "long-list modal contains every unused file",
    (await dialog().locator(".al-cleanup-list li").count()) === 12,
  );
  await assertDialogFits("320px long-list initial");
  const footerBefore = await dialog()
    .locator(".al-dialog-actions")
    .boundingBox();
  check(
    "long-list body has its own scroll range",
    await dialog()
      .locator(".al-dialog-body")
      .evaluate((node) => node.scrollHeight > node.clientHeight),
  );
  await dialog()
    .locator(".al-dialog-body")
    .evaluate((node) => {
      node.scrollTop = node.scrollHeight;
    });
  check(
    "last file remains reachable by scrolling the dialog body",
    await dialog()
      .locator(".al-dialog-body")
      .evaluate((node) => {
        const body = node.getBoundingClientRect(),
          last = node
            .querySelector(".al-cleanup-list li:last-child")
            .getBoundingClientRect();
        return last.top >= body.top && last.bottom <= body.bottom + 1;
      }),
  );
  await assertDialogFits("320px long-list scrolled");
  const footerAfter = await dialog()
    .locator(".al-dialog-actions")
    .boundingBox();
  check(
    "dialog footer remains fixed while list scrolls",
    Math.abs(footerBefore.y - footerAfter.y) < 1,
  );
  await shot("08-mobile-320-long-list");
  const requestCountBeforeCancel = cleanupRequests.length;
  await dialog().getByRole("button", { name: "취소", exact: true }).click();
  check(
    "long-list cancellation preserves all twelve candidates",
    cleanupRequests.length === requestCountBeforeCancel &&
      (await library()).attachments.filter((file) => !file.referenced)
        .length === 12,
  );
  check("no browser JavaScript errors", errors.length === 0);
  await writeFile(
    out + "results.json",
    JSON.stringify(
      {
        checkedAt: new Date().toISOString(),
        fixtureOrigin: base,
        checks,
        errors,
        screenshots,
      },
      null,
      2,
    ) + "\n",
  );
  console.log(`Completed ${checks.length} attachment action checks`);
} catch (error) {
  await shot("failure");
  console.error("Page errors:", errors);
  throw error;
} finally {
  await browser.close();
}
