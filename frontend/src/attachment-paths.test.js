import assert from "node:assert/strict";
import test from "node:test";
import { resolveAttachment } from "./attachment-paths.js";

const notePath = "프로젝트/회의록.md";
const attachment = {
  id: "att-diagram",
  relativePath: "프로젝트/.attachments/도표 (최종)#1?.png",
  url: "/api/editor/attachments/att-diagram",
};
attachment.markdownPath = attachment.relativePath
  .split("/")
  .map(encodeURIComponent)
  .join("/");
const attachments = [attachment];

test("resolves storage-root paths, optional leading slash, and API URLs", () => {
  const simple = {
    id: "att-image",
    relativePath: "프로젝트/.attachments/도표.png",
    url: "/api/editor/attachments/att-image",
  };
  for (const value of [
    simple.relativePath,
    "/" + simple.relativePath,
    "./" + simple.relativePath,
    encodeURI(simple.relativePath),
    simple.url,
  ]) {
    assert.equal(resolveAttachment(value, notePath, [simple]), simple, value);
  }
});

test("resolves document-relative and parent-relative Markdown attachments", () => {
  const local = { relativePath: "프로젝트/.attachments/도표.png" };
  const shared = { relativePath: "공유/diagram.png" };
  assert.equal(
    resolveAttachment(".attachments/도표.png", notePath, [local, shared]),
    local,
  );
  assert.equal(
    resolveAttachment("./.attachments/도표.png", notePath, [local, shared]),
    local,
  );
  assert.equal(
    resolveAttachment("../공유/diagram.png", notePath, [local, shared]),
    shared,
  );
});

test("decodes names after stripping URL query and fragment suffixes", () => {
  assert.equal(
    resolveAttachment(attachment.markdownPath, notePath, attachments),
    attachment,
  );
  assert.equal(
    resolveAttachment(
      attachment.markdownPath + "?download=1#preview",
      notePath,
      attachments,
    ),
    attachment,
  );
  assert.equal(
    resolveAttachment(
      attachment.markdownPath.split("/").slice(1).join("/") + "#preview",
      notePath,
      attachments,
    ),
    attachment,
  );
});

test("matches decomposed Korean links to composed storage filenames", () => {
  const encodedNfd = attachment.relativePath
    .normalize("NFD")
    .split("/")
    .map(encodeURIComponent)
    .join("/");
  for (const value of [
    encodedNfd,
    "/" + encodedNfd,
    encodedNfd.split("/").slice(1).join("/") + "#preview",
  ]) {
    assert.equal(resolveAttachment(value, notePath, attachments), attachment, value);
  }
  const plain = { relativePath: "프로젝트/.attachments/도표.png" };
  assert.equal(
    resolveAttachment(plain.relativePath.normalize("NFD"), notePath, [plain]),
    plain,
  );
});

test("matches composed links and decomposed note paths to decomposed storage filenames", () => {
  const decomposed = {
    ...attachment,
    relativePath: attachment.relativePath.normalize("NFD"),
  };
  assert.equal(
    resolveAttachment(attachment.markdownPath, notePath, [decomposed]),
    decomposed,
  );
  assert.equal(
    resolveAttachment(
      attachment.markdownPath.split("/").slice(1).join("/"),
      notePath.normalize("NFD"),
      attachments,
    ),
    attachment,
  );
});

test("never rewrites remote or protocol URLs to local attachments", () => {
  for (const value of [
    "https://example.com/" + attachment.markdownPath,
    "HTTP://example.com/" + attachment.markdownPath,
    "//example.com/" + attachment.markdownPath,
    "data:image/png;base64,iVBORw0KGgo=",
    "file:///" + attachment.markdownPath,
    "mailto:notes@example.com",
  ]) {
    // Even a synced metadata match must not override an explicit URL scheme.
    const misleading = { ...attachment, markdownPath: value, url: value };
    assert.equal(resolveAttachment(value, notePath, [misleading]), null, value);
  }
});

test("leaves unmatched and malformed percent-encoded paths unresolved", () => {
  assert.equal(resolveAttachment(".attachments/missing.png", notePath, attachments), undefined);
  assert.equal(resolveAttachment(".attachments/%E0%A4%A.png", notePath, attachments), undefined);
});
