// Regenerate every installed-app and browser icon from the existing brand symbol:
//   node scripts/generate-icons.mjs [--preview /tmp/notedown-icons.png]
// Uses the project's existing Playwright installation and local Chrome.
import { createRequire } from "node:module";
import { readFile, writeFile, mkdir } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const { chromium } = require("../frontend/node_modules/@playwright/test");
const publicDir = fileURLToPath(new URL("../frontend/public/", import.meta.url));
const iconDir = resolve(publicDir, "icons");
const svg = await readFile(resolve(iconDir, "notedown.svg"), "utf8");
// iOS and maskable surfaces receive an opaque full-bleed background. The same
// book glyph stays wholly within the central 80%-diameter maskable safe circle.
const fullBleedSvg = svg.replace('rx="11"', 'rx="0"');
const browser = await chromium.launch({ channel: "chrome", headless: true });
try {
  const page = await browser.newPage({ deviceScaleFactor: 1 });
  const pngs = new Map();
  for (const [name, size, source] of [
    ["icon-192.png", 192, svg],
    ["icon-512.png", 512, svg],
    ["maskable-512.png", 512, fullBleedSvg],
    ["apple-touch-icon.png", 180, fullBleedSvg],
    ["favicon-16.png", 16, svg],
    ["favicon-32.png", 32, svg],
    ["favicon-48.png", 48, svg],
  ]) {
    const encoded = await page.evaluate(async ({ source, size }) => {
      const image = new Image();
      image.src = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(source)}`;
      await image.decode();
      const canvas = document.createElement("canvas");
      canvas.width = size;
      canvas.height = size;
      canvas.getContext("2d").drawImage(image, 0, 0, size, size);
      return canvas.toDataURL("image/png").split(",")[1];
    }, { source, size });
    const png = Buffer.from(encoded, "base64");
    await writeFile(resolve(iconDir, name), png);
    pngs.set(size, png);
    console.log(`Generated icons/${name} (${size} × ${size})`);
  }
  await writeFile(resolve(iconDir, "favicon.svg"), svg);

  // ICO permits PNG-encoded entries; include common browser / Windows sizes.
  const sizes = [16, 32, 48];
  const header = Buffer.alloc(6 + sizes.length * 16);
  header.writeUInt16LE(1, 2);
  header.writeUInt16LE(sizes.length, 4);
  let offset = header.length;
  for (let i = 0; i < sizes.length; i++) {
    const size = sizes[i];
    const png = pngs.get(size);
    const entry = 6 + i * 16;
    header.writeUInt8(size, entry);
    header.writeUInt8(size, entry + 1);
    header.writeUInt16LE(1, entry + 4);
    header.writeUInt16LE(32, entry + 6);
    header.writeUInt32LE(png.length, entry + 8);
    header.writeUInt32LE(offset, entry + 12);
    offset += png.length;
  }
  await writeFile(
    resolve(publicDir, "favicon.ico"),
    Buffer.concat([header, ...sizes.map((size) => pngs.get(size))]),
  );
  console.log("Generated favicon.ico (16, 32, 48px) and icons/favicon.svg");

  const previewIndex = process.argv.indexOf("--preview");
  if (previewIndex !== -1) {
    if (!process.argv[previewIndex + 1]) throw new Error("--preview requires a path");
    const previewPath = resolve(process.argv[previewIndex + 1]);
    const items = [
      ["Notedown app · 192px", "icon-192.png", "app"],
      ["Notedown app · 512px", "icon-512.png", "app"],
      ["Maskable · circular crop", "maskable-512.png", "circle"],
      ["Apple touch · 180px", "apple-touch-icon.png", "apple"],
      ["Favicon · 32px", "favicon-32.png", "favicon"],
    ];
    await page.setViewportSize({ width: 1100, height: 400 });
    const cards = await Promise.all(items.map(async ([label, file, cls]) => {
      const data = (await readFile(resolve(iconDir, file))).toString("base64");
      return `<section><div class="sample"><img class="${cls}" src="data:image/png;base64,${data}"></div><p>${label}</p></section>`;
    }));
    await page.setContent(`<style>
      *{box-sizing:border-box}body{margin:0;padding:36px;background:#f7f8fa;color:#262a38;font:14px -apple-system,BlinkMacSystemFont,sans-serif}
      h1{font-size:24px;margin:0 0 28px}main{display:grid;grid-template-columns:repeat(5,1fr);gap:16px}
      section{padding:20px 10px 10px;border:1px solid #e9ebf0;border-radius:16px;background:white;text-align:center}
      .sample{height:150px;display:flex;align-items:center;justify-content:center}img{width:128px;height:128px}.circle{border-radius:50%}.apple{border-radius:28px}.favicon{width:32px;height:32px}p{font-size:12px;margin:18px 0 8px}
    </style><h1>Notedown · one symbol, every surface</h1><main>${cards.join("")}</main>`);
    await page.locator("img").evaluateAll((images) => Promise.all(images.map((img) => img.decode())));
    await mkdir(dirname(previewPath), { recursive: true });
    await page.screenshot({ path: previewPath });
    console.log(`Icon preview: ${previewPath}`);
  }
} finally {
  await browser.close();
}
