import assert from "node:assert/strict";
import test from "node:test";
import MarkdownIt from "markdown-it";
import { annotateMarkdownSource, buildScrollAnchors, interpolateScroll } from "./scroll-sync.js";

test("Markdown headings, images, table rows, and fenced code retain source lines", () => {
  const md = annotateMarkdownSource(new MarkdownIt());
  const html = md.render([
    "# Heading", "", "![photo](picture.png)", "", "| Name |", "| --- |", "| Cell |", "",
    "```js", "const value = 1;", "```",
  ].join("\n"));
  assert.match(html, /<h1 data-source-line="1" data-source-end="2">/);
  assert.match(html, /<p data-source-line="3" data-source-end="4"><img/);
  assert.match(html, /<tr data-source-line="7" data-source-end="8">/);
  assert.match(html, /<pre data-source-line="9" data-source-end="12"><code class="language-js">/);
});

test("source annotation is idempotent and does not expose hidden list paragraphs", () => {
  const md = annotateMarkdownSource(annotateMarkdownSource(new MarkdownIt()));
  const html = md.render("- First\n- Second\n\n    code");
  assert.match(html, /<li data-source-line="1"/);
  assert.equal((md.render("```\ncode\n```").match(/data-source-line=/g) || []).length, 1);
});

test("interpolation follows block geometry instead of proportional document heights", () => {
  const anchors = buildScrollAnchors([
    { source: 100, preview: 120 },
    { source: 200, preview: 900 }, // A tall image occupies only a few source lines.
    { source: 400, preview: 1100 },
  ], 1000, 1500);
  assert.equal(interpolateScroll(anchors, 150), 510);
  assert.equal(interpolateScroll(anchors, 510, "preview"), 150);
  assert.equal(interpolateScroll(anchors, 300), 1000);
});

test("nested blocks choose the most precise anchor and discard non-monotonic positions", () => {
  const anchors = buildScrollAnchors([
    { source: 100, preview: 90 },
    { source: 100, preview: 105 },
    { source: 200, preview: 100 },
    { source: 300, preview: 300 },
    { source: NaN, preview: 10 },
    { source: 600, preview: 900 },
  ], 500, 500);
  assert.deepEqual(anchors, [
    { source: 0, preview: 0 },
    { source: 100, preview: 105 },
    { source: 300, preview: 300 },
    { source: 500, preview: 500 },
  ]);
});

test("both panes reach exact beginning and end even with unequal scrollable extents", () => {
  const anchors = buildScrollAnchors([{ source: 100, preview: 500 }], 600, 1300);
  assert.equal(interpolateScroll(anchors, -10), 0);
  assert.equal(interpolateScroll(anchors, 600), 1300);
  assert.equal(interpolateScroll(anchors, 2000, "preview"), 600);
  assert.equal(interpolateScroll(anchors, 0, "preview"), 0);
});

test("empty documents and absent anchors never produce invalid offsets", () => {
  assert.equal(interpolateScroll(buildScrollAnchors([], 0, 0), 0), 0);
  assert.equal(interpolateScroll(buildScrollAnchors([], 1000, 2000), 250), 500);
  assert.equal(interpolateScroll([], 10), 0);
});
