import { useEffect } from "react";

const SOURCE_SELECTOR = "[data-source-line]";

/** Keep Markdown source positions on rendered blocks, including fenced code. */
export function annotateMarkdownSource(markdown) {
  if (markdown.__notedownSourcePositions) return markdown;
  markdown.__notedownSourcePositions = true;
  markdown.core.ruler.push("notedown_source_positions", (state) => {
    for (const token of state.tokens) {
      if (!token.map || !token.block || token.hidden || token.nesting < 0) continue;
      // The fence renderer puts token attributes on <code>; its scroll anchor
      // belongs to the outer <pre>, whose full height includes the padding.
      if (token.type === "fence" || token.type === "inline") continue;
      token.attrSet("data-source-line", String(token.map[0] + 1));
      token.attrSet("data-source-end", String(token.map[1] + 1));
    }
  });
  const renderFence = markdown.renderer.rules.fence;
  markdown.renderer.rules.fence = (tokens, index, ...args) => {
    const html = renderFence(tokens, index, ...args);
    const map = tokens[index].map;
    if (!map) return html;
    return html.replace(
      /<pre(?=[\s>])/,
      `<pre data-source-line="${map[0] + 1}" data-source-end="${map[1] + 1}"`,
    );
  };
  return markdown;
}

/** Produce a reversible, monotonic map with explicit start/end positions. */
export function buildScrollAnchors(candidates, sourceMax, previewMax) {
  sourceMax = Math.max(0, sourceMax);
  previewMax = Math.max(0, previewMax);
  const bySource = new Map();
  for (const candidate of candidates) {
    const { source, preview } = candidate;
    if (
      Number.isFinite(source) &&
      Number.isFinite(preview) &&
      source > 0 && source < sourceMax &&
      preview > 0 && preview < previewMax
    ) {
      // Nested paragraphs/rows are encountered after their containers and
      // provide the more precise position when both begin on the same line.
      bySource.set(source, { source, preview });
    }
  }
  const anchors = [{ source: 0, preview: 0 }];
  for (const anchor of [...bySource.values()].sort((a, b) => a.source - b.source)) {
    if (anchor.preview > anchors[anchors.length - 1].preview) anchors.push(anchor);
  }
  anchors.push({ source: sourceMax, preview: previewMax });
  return anchors;
}

export function interpolateScroll(anchors, position, from = "source") {
  const to = from === "source" ? "preview" : "source";
  const last = anchors[anchors.length - 1];
  if (!last || position <= 0) return 0;
  if (position >= last[from]) return last[to];
  let low = 0;
  let high = anchors.length - 1;
  while (high - low > 1) {
    const middle = (low + high) >> 1;
    if (anchors[middle][from] <= position) low = middle;
    else high = middle;
  }
  const before = anchors[low];
  const after = anchors[high];
  const distance = after[from] - before[from];
  return distance > 0
    ? before[to] + ((position - before[from]) / distance) * (after[to] - before[to])
    : before[to];
}

function measureAnchors(editor, preview) {
  const model = editor.getModel();
  if (!model) return null;
  const lineCount = model.getLineCount();
  const sourceMax = Math.max(0, editor.getScrollHeight() - editor.getLayoutInfo().height);
  const previewMax = Math.max(0, preview.scrollHeight - preview.clientHeight);
  const previewTop = preview.getBoundingClientRect().top + preview.clientTop;
  const candidates = [];
  for (const block of preview.querySelectorAll(SOURCE_SELECTOR)) {
    const line = Number(block.dataset.sourceLine);
    const end = Number(block.dataset.sourceEnd);
    if (!Number.isInteger(line) || line < 1 || line > lineCount) continue;
    const rect = block.getBoundingClientRect();
    candidates.push({
      source: editor.getTopForLineNumber(line),
      preview: rect.top - previewTop + preview.scrollTop,
    });
    // Endpoints for leaf blocks make tall images and code interpolate across
    // their actual heights. Containers defer to their finer-grained children.
    if (end > line && !block.querySelector(SOURCE_SELECTOR)) {
      candidates.push({
        source: editor.getBottomForLineNumber(Math.min(lineCount, end - 1)),
        preview: rect.bottom - previewTop + preview.scrollTop,
      });
    }
  }
  return buildScrollAnchors(candidates, sourceMax, previewMax);
}

/**
 * Synchronize a Monaco instance and the scrollable Markdown article.
 * contentKey must change when the rendered HTML changes. The article ref must
 * point to the element with overflow:auto, not its inner HTML wrapper.
 */
export function useScrollSync({ editor, previewRef, enabled, contentKey }) {
  useEffect(() => {
    const preview = previewRef.current;
    if (!enabled || !editor || !preview || !editor.getModel()) return;
    let active = true;
    let frame = 0;
    let anchors = null;
    let measureNeeded = true;
    let leader = "source";
    let expectedSource = null;
    let expectedPreview = null;

    function synchronize() {
      frame = 0;
      if (!active || !editor.getModel()) return;
      if (measureNeeded) {
        anchors = measureAnchors(editor, preview);
        measureNeeded = false;
      }
      if (!anchors) return;
      const end = anchors[anchors.length - 1];
      if (end.source <= 0 || end.preview <= 0) return;
      if (leader === "source") {
        const target = interpolateScroll(anchors, editor.getScrollTop());
        if (Math.abs(preview.scrollTop - target) > 0.5) {
          preview.scrollTop = target;
          expectedPreview = preview.scrollTop;
        }
      } else {
        const target = interpolateScroll(anchors, preview.scrollTop, "preview");
        if (Math.abs(editor.getScrollTop() - target) > 0.5) {
          expectedSource = target;
          editor.setScrollTop(target);
          expectedSource = editor.getScrollTop();
        }
      }
    }

    function schedule() {
      if (active && !frame) frame = requestAnimationFrame(synchronize);
    }

    function remeasure() {
      measureNeeded = true;
      schedule();
    }

    function onPreviewScroll() {
      if (expectedPreview !== null && Math.abs(preview.scrollTop - expectedPreview) < 1) {
        expectedPreview = null;
        return;
      }
      expectedPreview = null;
      leader = "preview";
      schedule();
    }

    const subscriptions = [
      editor.onDidScrollChange((event) => {
        if (!active || !event.scrollTopChanged) return;
        if (expectedSource !== null && Math.abs(event.scrollTop - expectedSource) < 1) {
          expectedSource = null;
          return;
        }
        expectedSource = null;
        leader = "source";
        schedule();
      }),
      editor.onDidLayoutChange(remeasure),
      editor.onDidContentSizeChange(remeasure),
      editor.onDidChangeModel(remeasure),
      editor.onDidDispose(() => {
        active = false;
        cancelAnimationFrame(frame);
      }),
    ];
    preview.addEventListener("scroll", onPreviewScroll, { passive: true });
    // Capture catches non-bubbling image events. Failed images change geometry,
    // too, and cached images are covered by the first measurement.
    preview.addEventListener("load", remeasure, true);
    preview.addEventListener("error", remeasure, true);
    const observer = new ResizeObserver(remeasure);
    observer.observe(preview);
    if (preview.firstElementChild) observer.observe(preview.firstElementChild);
    schedule();
    return () => {
      active = false;
      cancelAnimationFrame(frame);
      for (const subscription of subscriptions) subscription.dispose();
      observer.disconnect();
      preview.removeEventListener("scroll", onPreviewScroll);
      preview.removeEventListener("load", remeasure, true);
      preview.removeEventListener("error", remeasure, true);
    };
  }, [editor, previewRef, enabled, contentKey]);
}
