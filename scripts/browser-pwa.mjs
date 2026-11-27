import { createRequire } from "node:module";
import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import assert from "node:assert/strict";

const require = createRequire(import.meta.url);
const { chromium } = require("../frontend/node_modules/@playwright/test");
const base = "http://127.0.0.1:5502"; // Synthetic verification server only. This script never resets storage.
const out = new URL("../docs/verification/pwa/", import.meta.url).pathname;
await mkdir(out, { recursive: true });
const profile = await mkdtemp(join(tmpdir(), "notedown-pwa-verification-"));
const context = await chromium.launchPersistentContext(profile, {
  channel: "chrome",
  headless: true,
  viewport: { width: 1440, height: 1000 },
  deviceScaleFactor: 1,
});
const browser = context.browser();
const checks = [],
  errors = [],
  evidence = {};
const observe = (page) =>
  page.on("pageerror", (error) => errors.push(error.message));
const page = await context.newPage();
observe(page);
await page.addInitScript(() => {
  window.__pwaInstallPromptEvents = 0;
  window.addEventListener("beforeinstallprompt", () => {
    window.__pwaInstallPromptEvents += 1;
  });
});
const check = (name, value = true) => {
  assert.ok(value, name);
  checks.push(name);
  console.log("PASS", name);
};
const screenshot = async (target, name) => {
  await target.evaluate(() => document.fonts.ready);
  await target.waitForTimeout(200);
  await target.screenshot({
    path: out + name + ".png",
    fullPage: true,
    animations: "disabled",
  });
};
const cacheSnapshot = (target) =>
  target.evaluate(async () => {
    const result = [];
    for (const name of await caches.keys()) {
      const cache = await caches.open(name);
      result.push({
        name,
        urls: (await cache.keys()).map((request) => request.url).sort(),
      });
    }
    return result;
  });
const noPrivateCaches = (snapshot) =>
  snapshot.every((cache) =>
    cache.urls.every((url) => {
      const path = new URL(url).pathname;
      return (
        path === "/offline.html" ||
        path.startsWith("/icons/") ||
        path === "/favicon.ico"
      );
    }),
  );
const noHorizontalOverflow = (target) =>
  target.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1);
const ensureImages = (target) =>
  target.waitForFunction(
    () =>
      [...document.querySelectorAll(".brand img")].length > 0 &&
      [...document.querySelectorAll(".brand img")].every(
        (img) => img.complete && img.naturalWidth > 0,
      ),
  );
const closeDialog = async (target) => {
  await target.keyboard.press("Escape");
  await target.getByRole("dialog").waitFor({ state: "hidden" });
};
let mobileContext;
try {
  await page.goto(base + "/login");
  await page.waitForSelector(".auth-form");
  await ensureImages(page);
  check(
    "login uses the shared loaded symbol SVG",
    await page
      .locator(".brand img")
      .evaluateAll((images) =>
        images.every(
          (img) => new URL(img.src).pathname === "/icons/notedown.svg",
        ),
      ),
  );
  check(
    "manifest is linked from the application document",
    await page
      .locator('link[rel="manifest"]')
      .evaluate(
        (link) => new URL(link.href).pathname === "/manifest.webmanifest",
      ),
  );
  const icons = await page
    .locator('link[rel~="icon"]')
    .evaluateAll((links) => links.map((link) => new URL(link.href).pathname));
  check(
    "favicon uses the shared symbol assets",
    icons.length > 0 &&
      icons.every(
        (path) => path === "/icons/favicon.svg" || path === "/favicon.ico",
      ),
  );
  check(
    "Apple touch icon is linked",
    await page
      .locator('link[rel="apple-touch-icon"]')
      .evaluate(
        (link) => new URL(link.href).pathname === "/icons/apple-touch-icon.png",
      ),
  );
  check(
    "browser theme matches the symbol brand color",
    (
      await page.locator('meta[name="theme-color"]').getAttribute("content")
    ).toLowerCase() === "#7165d9",
  );
  await screenshot(page, "01-login-desktop");
  const manifestResponse = await context.request.get(
    base + "/manifest.webmanifest",
  );
  const manifest = await manifestResponse.json();
  evidence.manifest = manifest;
  check(
    "manifest is served successfully with a manifest content type",
    manifestResponse.ok() &&
      /application\/(manifest\+json|json)/.test(
        manifestResponse.headers()["content-type"],
      ),
  );
  check(
    "manifest defines a standalone app at the root scope",
    manifest.id === "/" &&
      manifest.start_url === "/" &&
      manifest.scope === "/" &&
      manifest.display === "standalone",
  );
  check(
    "manifest has a usable name and matching theme",
    Boolean(manifest.name && manifest.short_name) &&
      manifest.theme_color.toLowerCase() === "#7165d9",
  );
  for (const [name, size] of [
    ["icon-192.png", 192],
    ["icon-512.png", 512],
    ["maskable-512.png", 512],
    ["apple-touch-icon.png", 180],
  ]) {
    const response = await context.request.get(base + "/icons/" + name);
    const bytes = await response.body();
    check(
      `${name} is a real ${size}×${size} PNG`,
      response.ok() &&
        bytes
          .subarray(0, 8)
          .equals(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10])) &&
        bytes.readUInt32BE(16) === size &&
        bytes.readUInt32BE(20) === size,
    );
  }
  check(
    "manifest includes standard 192 and 512 app icons",
    [192, 512].every((size) =>
      manifest.icons.some(
        (icon) =>
          icon.sizes === `${size}x${size}` &&
          icon.src === `/icons/icon-${size}.png` &&
          (!icon.purpose || icon.purpose.split(" ").includes("any")),
      ),
    ),
  );
  check(
    "manifest has a dedicated maskable icon",
    manifest.icons.some(
      (icon) =>
        icon.src === "/icons/maskable-512.png" &&
        icon.sizes === "512x512" &&
        icon.purpose.split(" ").includes("maskable"),
    ),
  );
  const ico = await (await context.request.get(base + "/favicon.ico")).body();
  check(
    "favicon.ico is a valid multi-resolution ICO",
    ico.readUInt16LE(0) === 0 &&
      ico.readUInt16LE(2) === 1 &&
      ico.readUInt16LE(4) >= 2,
  );
  const symbol = await context.request.get(base + "/icons/notedown.svg");
  const favicon = await context.request.get(base + "/icons/favicon.svg");
  check(
    "symbol and SVG favicon have identical artwork",
    symbol.ok() &&
      favicon.ok() &&
      (await symbol.text()) === (await favicon.text()),
  );
  await page.waitForFunction(
    () => Boolean(navigator.serviceWorker?.controller),
    undefined,
    { timeout: 15000 },
  );
  evidence.registration = await page.evaluate(async () => {
    const registration = await navigator.serviceWorker.ready;
    return {
      scope: registration.scope,
      scriptURL: registration.active.scriptURL,
      controller: navigator.serviceWorker.controller.scriptURL,
    };
  });
  check(
    "root-scoped service worker actively controls the page",
    evidence.registration.scope === base + "/" &&
      evidence.registration.scriptURL === base + "/sw.js" &&
      evidence.registration.controller === base + "/sw.js",
  );
  evidence.cacheBeforeLogin = await cacheSnapshot(page);
  check(
    "precache contains the public offline page",
    evidence.cacheBeforeLogin.some((cache) =>
      cache.urls.includes(base + "/offline.html"),
    ),
  );
  check(
    "precache contains only public offline and icon assets",
    noPrivateCaches(evidence.cacheBeforeLogin),
  );

  const cdp = await context.newCDPSession(page);
  await cdp.send("Page.enable");
  evidence.cdpManifest = await cdp.send("Page.getAppManifest");
  check(
    "Chrome parses the manifest without critical errors",
    evidence.cdpManifest.url === base + "/manifest.webmanifest" &&
      !(evidence.cdpManifest.errors || []).some((error) => error.critical),
  );
  for (let attempt = 0; attempt < 20; attempt++) {
    evidence.installability = await cdp.send("Page.getInstallabilityErrors");
    if (!evidence.installability.installabilityErrors.length) break;
    await page.waitForTimeout(500);
  }
  check(
    "Chrome reports no app installability errors",
    evidence.installability.installabilityErrors.length === 0,
  );
  await page.getByRole("button", { name: "앱 설치", exact: true }).click();
  await page.getByRole("dialog").waitFor();
  check(
    "login install action opens the installation guide",
    await page
      .getByRole("heading", { name: "Notedown 앱 설치", exact: true })
      .isVisible(),
  );
  evidence.nativePromptEvents = await page.evaluate(
    () => window.__pwaInstallPromptEvents,
  );
  if (evidence.nativePromptEvents > 0) {
    check(
      "a real browser install event exposes the native install action",
      await page
        .getByRole("button", { name: "지금 설치", exact: true })
        .isVisible(),
    );
  }
  evidence.nativeInstallNotTriggered = true;
  await screenshot(page, "02-install-guide-desktop");
  await closeDialog(page);

  const status = await (
    await context.request.get(base + "/api/setup/status")
  ).json();
  assert.equal(
    status.configured,
    true,
    "Synthetic reviewer account must already be configured; this script does not modify account setup.",
  );
  await page.getByLabel("아이디", { exact: true }).fill("reviewer");
  await page
    .getByLabel("비밀번호", { exact: true })
    .fill("browser-verification-only");
  await page.getByRole("button", { name: "로그인", exact: true }).click();
  await page.waitForSelector(".library-view");
  await ensureImages(page);
  check(
    "authenticated app retains the shared symbol logo",
    await page
      .locator(".brand img")
      .evaluateAll((images) =>
        images.every(
          (img) => new URL(img.src).pathname === "/icons/notedown.svg",
        ),
      ),
  );
  await page.getByRole("button", { name: "앱 설치", exact: true }).click();
  await page.getByRole("dialog").waitFor();
  check(
    "top-right installation action is available after login",
    await page
      .getByRole("heading", { name: "Notedown 앱 설치", exact: true })
      .isVisible(),
  );
  await closeDialog(page);
  const privateRead = await page.evaluate(async () => {
    const response = await fetch("/api/editor/library");
    return { status: response.status, body: await response.json() };
  });
  check(
    "authenticated API response is exercised under worker control",
    privateRead.status === 200,
  );
  const noteId = privateRead.body.notes?.[0]?.id;
  if (noteId) {
    check(
      "a document response is exercised under worker control",
      await page.evaluate(
        async (id) =>
          (await fetch("/api/editor/notes/" + encodeURIComponent(id))).ok,
        noteId,
      ),
    );
  }
  evidence.cacheAfterPrivateReads = await cacheSnapshot(page);
  check(
    "authenticated API and document responses never enter Cache Storage",
    noPrivateCaches(evidence.cacheAfterPrivateReads) &&
      JSON.stringify(evidence.cacheAfterPrivateReads) ===
        JSON.stringify(evidence.cacheBeforeLogin),
  );
  await screenshot(page, "03-library-desktop");

  // Explicit fallback-platform simulation: no native prompt is dispatched to the app.
  // This verifies iOS instructions, not an iOS OS-level installation.
  mobileContext = await browser.newContext({
    viewport: { width: 390, height: 844 },
    deviceScaleFactor: 1,
    isMobile: true,
    hasTouch: true,
    userAgent:
      "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1",
  });
  await mobileContext.addInitScript(() => {
    window.addEventListener(
      "beforeinstallprompt",
      (event) => {
        event.preventDefault();
        event.stopImmediatePropagation();
      },
      { capture: true },
    );
  });
  const mobile = await mobileContext.newPage();
  observe(mobile);
  await mobile.goto(base + "/login");
  await mobile.waitForSelector(".auth-form");
  await ensureImages(mobile);
  check(
    "mobile login has no horizontal page overflow",
    await noHorizontalOverflow(mobile),
  );
  await screenshot(mobile, "04-login-mobile");
  await mobile.getByRole("button", { name: "앱 설치", exact: true }).click();
  await mobile.getByRole("dialog").waitFor();
  check(
    "manual iOS installation guide is usable without a native prompt",
    (await mobile
      .getByRole("heading", { name: "Notedown 앱 설치", exact: true })
      .isVisible()) &&
      /홈 화면/.test(await mobile.getByRole("dialog").innerText()),
  );
  check(
    "mobile guide fits the page without horizontal overflow",
    await noHorizontalOverflow(mobile),
  );
  evidence.mobileGuideMode =
    "iPhone Safari user-agent with beforeinstallprompt suppressed; OS installation not performed";
  await screenshot(mobile, "05-install-guide-mobile");
  await closeDialog(mobile);

  await context.setOffline(true);
  await page.goto(base + "/login?verify-offline=1");
  await page.getByRole("button", { name: "다시 연결", exact: true }).waitFor();
  check(
    "failed network navigation shows the public offline fallback",
    /연결|오프라인/.test(await page.locator("body").innerText()) &&
      !(await page.locator(".library-view,.auth-form").count()),
  );
  const offlineText = await page.locator("body").innerText();
  check(
    "offline fallback does not reveal private document titles",
    !(privateRead.body.notes || []).some(
      (note) => note.title && offlineText.includes(note.title),
    ),
  );
  check(
    "private API requests fail offline instead of replaying cached content",
    await page.evaluate(async () => {
      try {
        return !(await fetch("/api/editor/library")).ok;
      } catch {
        return true;
      }
    }),
  );
  await screenshot(page, "06-offline-desktop");
  await page.setViewportSize({ width: 390, height: 844 });
  check("offline screen fits mobile width", await noHorizontalOverflow(page));
  await screenshot(page, "07-offline-mobile");
  await context.setOffline(false);
  await page.getByRole("button", { name: "다시 연결", exact: true }).click();
  await page.waitForSelector(".library-view");
  check("reconnect action restores the authenticated online app");
  evidence.cacheAfterReconnect = await cacheSnapshot(page);
  check(
    "offline navigation and reconnect keep private content out of caches",
    noPrivateCaches(evidence.cacheAfterReconnect),
  );
  check("no uncaught browser JavaScript errors", errors.length === 0);
  await writeFile(
    out + "browser-results.json",
    JSON.stringify(
      {
        passed: checks.length,
        checks,
        errors,
        evidence,
        screenshots: [
          "01-login-desktop.png",
          "02-install-guide-desktop.png",
          "03-library-desktop.png",
          "04-login-mobile.png",
          "05-install-guide-mobile.png",
          "06-offline-desktop.png",
          "07-offline-mobile.png",
        ],
      },
      null,
      2,
    ),
  );
  await rm(out + "failure.png", { force: true });
  console.log(`Completed ${checks.length} PWA checks.`);
} catch (error) {
  await writeFile(
    out + "browser-results.json",
    JSON.stringify(
      { passed: checks.length, checks, errors, evidence, failure: error.stack },
      null,
      2,
    ),
  );
  await page
    .screenshot({ path: out + "failure.png", fullPage: true })
    .catch(() => {});
  throw error;
} finally {
  await mobileContext?.close();
  await context.close();
  await browser.close();
  await rm(profile, { recursive: true, force: true });
}
