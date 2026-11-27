import { createRequire } from 'node:module';
import { mkdir, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import assert from 'node:assert/strict';
const require = createRequire(import.meta.url);
const { chromium } = require('../frontend/node_modules/@playwright/test');
// All writes stay on the dedicated synthetic verification server.
const useHttps = process.env.NOTEDOWN_BROWSER_HTTPS === '1';
const base = useHttps ? 'https://127.0.0.1:5543' : 'http://127.0.0.1:5502';
const out = process.env.NOTEDOWN_BROWSER_OUTPUT || new URL('../docs/verification/sidebar/', import.meta.url).pathname;
await mkdir(out, { recursive: true });
const browser = await chromium.launch({ channel: 'chrome', headless: true });
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, ignoreHTTPSErrors: useHttps });
const page = await context.newPage();
const checks = [], screenshots = [], errors = [];
page.on('pageerror', e => errors.push(e.message));
const check = (name, value) => { assert.ok(value, name); checks.push(name); console.log('PASS', name); };
const api = async (path, body, method = 'POST') => {
  const r = await context.request.fetch(base + path, { method, ...(body === undefined ? {} : { data: body }) });
  assert.ok(r.ok(), `${method} ${path}: ${r.status()}`);
  return r.json();
};
const shot = async name => {
  await page.evaluate(() => document.fonts.ready);
  await page.waitForTimeout(220);
  await page.screenshot({ path: join(out, name + '.png'), animations: 'disabled' });
  screenshots.push(name + '.png');
};
const install = () => page.locator('.topbar-right').getByRole('button', { name: '앱 설치', exact: true });
const headerFits = async label => {
  check(label + ' install and navigation controls fit without overlap', await page.locator('.topbar').evaluate(header => {
    const rect = header.getBoundingClientRect();
    const nodes = [...header.querySelectorAll('button')].filter(n => n.getBoundingClientRect().width > 0);
    const boxes = nodes.map(n => n.getBoundingClientRect());
    const overlap = boxes.some((a,i) => boxes.slice(i + 1).some(b => Math.min(a.right,b.right)-Math.max(a.left,b.left)>1 && Math.min(a.bottom,b.bottom)-Math.max(a.top,b.top)>1));
    const breadcrumb = header.querySelector('.breadcrumb').getBoundingClientRect();
    const actions = header.querySelector('.topbar-right').getBoundingClientRect();
    return !overlap && breadcrumb.right <= actions.left && boxes.every(b => b.top >= rect.top && b.bottom <= rect.bottom && b.left >= 0 && b.right <= innerWidth) && document.documentElement.scrollWidth <= innerWidth;
  }));
  check(label + ' install button stays in the upper-right corner', await install().evaluate(n => {
    const r = n.getBoundingClientRect(); return r.left > innerWidth / 2 && r.top >= 0 && r.bottom <= 68;
  }));
};
const sidebarFits = async label => {
  check(label + ' sidebar brand, account and actions remain visible', await page.locator('.sidebar').evaluate(sidebar => {
    const sr = sidebar.getBoundingClientRect();
    const els = [sidebar.querySelector('.brand'), sidebar.querySelector('.account'), sidebar.querySelector('.add-workspace'), sidebar.querySelector('.sidebar-bottom > button'), sidebar.querySelector('.workspace-filter')];
    return sidebar.scrollHeight <= sidebar.clientHeight && els.every(n => { const r=n.getBoundingClientRect();return r.top>=sr.top && r.bottom<=sr.bottom && r.left>=sr.left && r.right<=sr.right; }) && sidebar.querySelector('.brand').scrollWidth <= sidebar.querySelector('.brand').clientWidth;
  }));
};
const openInstall = async label => {
  await install().click();
  const dialog = page.getByRole('dialog');
  await dialog.waitFor();
  check(label + ' install guide opens from the header', await dialog.isVisible());
  await page.keyboard.press('Escape');
  await dialog.waitFor({ state: 'detached' });
  check(label + ' closing guide restores keyboard focus to install button', await install().evaluate(n => n === document.activeElement));
};
try {
  await page.goto(base);
  await page.locator('.auth-form').waitFor();
  const setup = await api('/api/setup/status', undefined, 'GET');
  await page.getByLabel('아이디', { exact: true }).fill('reviewer');
  await page.getByLabel('비밀번호', { exact: true }).fill('browser-verification-only');
  if (!setup.configured) await page.getByLabel('비밀번호 확인', { exact: true }).fill('browser-verification-only');
  await page.getByRole('button', { name: setup.configured ? '로그인' : '워크스페이스 만들기', exact: true }).click();
  await page.locator('.library-view').waitFor();
  await api('/api/admin/storage/reset', { confirm: 'RESET' });
  const names = ['프로젝트 기획','디자인 자료','서비스 개발','회의와 메모','읽고 싶은 책','배움의 기록','여행 계획','생활의 기록','아이디어 보관함','Team 공유 자료','참고 문서','개인 프로젝트','긴 워크스페이스 이름으로 상단 설치 버튼 공간을 확인합니다'];
  const workspaces = [];
  for (const name of names) workspaces.push((await api('/api/editor/workspaces', { name })).workspace);
  for (const [index, title] of ['새로운 공간을 위한 아이디어','이번 주의 작업 노트','다음 프로젝트의 디자인','공유할 참고 자료'].entries()) {
    await api('/api/editor/notes', { workspaceId: workspaces[index].id, title, content: '# ' + title + '\n\n작은 기록부터 시작해 보세요.' });
  }
  await page.reload();
  await page.locator('.workspace-nav .nav-item').nth(12).waitFor();
  check('Decorative storage card and copy are removed', await page.locator('.storage-card').count() === 0 && !(await page.locator('.sidebar').innerText()).includes('나만의 기록, 한곳에'));
  check('Sidebar has no install button', await page.locator('.sidebar .pwa-install-button').count() === 0);
  check('Authenticated view has one header installation action', await page.getByRole('button', { name: '앱 설치', exact: true }).count() === 1 && await install().isVisible());
  await headerFits('1440px desktop');
  await sidebarFits('1440px desktop');
  check('All thirteen workspaces fit the tall desktop sidebar', await page.locator('.workspace-nav').evaluate(n => n.scrollHeight <= n.clientHeight));
  await shot('01-desktop-sidebar');
  await page.locator('.workspace-nav button').first().focus();
  await page.keyboard.press('Tab');
  check('Keyboard navigation reaches the next workspace', await page.locator('.workspace-nav button').nth(1).evaluate(n => n === document.activeElement));
  check('Keyboard outline fits inside the scrollable navigation', await page.locator('.workspace-nav button').nth(1).evaluate(n => {
    const style=getComputedStyle(n); return n.matches(':focus-visible') && parseFloat(style.outlineWidth)>0 && parseFloat(style.outlineOffset)+parseFloat(style.outlineWidth)<=0;
  }));
  check('Truncated workspace names retain the full hover label', await page.locator('.workspace-nav button').last().getAttribute('title') === names.at(-1));
  await shot('01-desktop-keyboard-focus');
  const workspaceFilter = page.getByRole('searchbox', { name: '워크스페이스 필터' });
  const clearFilter = page.getByRole('button', { name: '워크스페이스 필터 지우기' });
  const workspaceButtons = page.locator('.workspace-nav .nav-item');
  await workspaceButtons.filter({ hasText: names[0] }).click();
  await workspaceFilter.fill('  기획 프로젝트  ');
  check('Workspace filter matches all words in the name', await workspaceButtons.count() === 1 && await workspaceButtons.first().getAttribute('title') === names[0]);
  await workspaceFilter.fill(' tEAm ');
  check('Workspace filter ignores letter case and surrounding spaces', await workspaceButtons.count() === 1 && await workspaceButtons.first().getAttribute('title') === names[9]);
  check('Filtering the sidebar leaves the selected workspace and documents visible',
    (await page.locator('.library-view h1').textContent()).startsWith(names[0]) &&
    await page.getByRole('heading', { name: '새로운 공간을 위한 아이디어', exact: true }).isVisible());
  await workspaceFilter.fill('찾을 수 없는 이름');
  check('Workspace filter shows an empty result message', await workspaceButtons.count() === 0 && await page.locator('.workspace-nav').getByRole('status').isVisible());
  await clearFilter.focus();
  await clearFilter.press('Enter');
  check('Clearing the filter restores the list and focuses the input', await workspaceButtons.count() === names.length && await workspaceFilter.evaluate(n => n === document.activeElement));
  await openInstall('Desktop');
  await page.getByRole('button', { name: '첨부 라이브러리', exact: true }).click();
  await page.locator('.attachment-library').waitFor();
  await headerFits('Attachment library');
  await page.getByRole('button', { name: '서버 관리', exact: true }).click();
  await page.locator('.admin-panel').waitFor();
  await headerFits('Server administration');
  await page.getByRole('button', { name: '문서 라이브러리', exact: false }).click();
  await page.setViewportSize({ width: 1280, height: 600 });
  await sidebarFits('600px-tall desktop');
  check('Short screen scrolls only workspace navigation', await page.locator('.workspace-nav').evaluate(n => n.scrollHeight > n.clientHeight));
  const longWorkspace = () => page.locator('.workspace-nav').getByRole('button', { name: new RegExp(names.at(-1)) });
  await longWorkspace().scrollIntoViewIfNeeded();
  await longWorkspace().click();
  await headerFits('Desktop long workspace name');
  await shot('02-short-desktop-sidebar');
  for (const width of [390,320]) {
    await page.setViewportSize({ width, height: 844 });
    await headerFits(width + 'px mobile long workspace name');
    await shot('03-mobile-' + width + '-header');
    await openInstall(width + 'px mobile');
    await page.getByRole('button', { name: '메뉴 열기', exact: true }).click();
    await page.waitForTimeout(220);
    await sidebarFits(width + 'px mobile');
    await workspaceFilter.fill('설치 버튼 공간');
    check(width + 'px mobile filter finds workspaces in the scrollable list', await workspaceButtons.count() === 1);
    await shot('04-mobile-' + width + '-sidebar');
    await longWorkspace().scrollIntoViewIfNeeded();
    await longWorkspace().click();
    check(width + 'px mobile workspace selection closes the sidebar', await page.locator('.sidebar.open').count() === 0);
    await headerFits(width + 'px mobile after navigation');
    await page.getByRole('button', { name: '메뉴 열기', exact: true }).click();
    check(width + 'px mobile keeps the filter when reopening the sidebar', (await workspaceFilter.inputValue()) === '설치 버튼 공간');
    await clearFilter.click();
    await page.locator('.sidebar-scrim').click({ position: { x: width - 20, y: 100 } });
  }
  await install().click();
  await page.getByRole('dialog').waitFor();
  await shot('05-mobile-install-guide');
  await page.keyboard.press('Escape');
  await page.evaluate(() => window.dispatchEvent(new Event('appinstalled')));
  check('Installed PWA hides the header install action', await page.locator('.topbar-install').count() === 0);
  check('No browser JavaScript errors', errors.length === 0);
  await writeFile(join(out, 'results.json'), JSON.stringify({ checkedAt:new Date().toISOString(), checks, screenshots, errors }, null, 2) + '\n');
} finally { await browser.close(); }
