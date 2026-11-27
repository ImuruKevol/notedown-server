import test from "node:test";
import assert from "node:assert/strict";
import { matchingSegments, searchDocuments, searchTerms } from "../src/document-search.js";

const note = (id, title, workspaceName, updatedAtMs = 0) => ({ id, title, workspaceName, updatedAtMs });
const ids = (notes, query) => searchDocuments(notes, query).map((n) => n.id);

test("all words must occur across document and workspace names, regardless of case or whitespace", () => {
  const notes = [note("a", "Release Plan", "Research Lab"), note("b", "Release Plan", "Personal"), note("c", "Ideas", "Research Lab")];
  assert.deepEqual(ids(notes, "  RESEARCH\t release\n"), ["a"]);
  assert.deepEqual(ids(notes, "research release missing"), []);
  assert.deepEqual(ids(notes, "lab"), ["c", "a"]);
});
test("total occurrences across both fields determine ranking before exact or prefix matches", () => {
  const notes = [note("exact", "Alpha guide", "Product"), note("many", "ALPHA Alpha Guide", "Product alpha"), note("workspace", "Guide", "Alpha")];
  assert.deepEqual(ids(notes, "alpha guide"), ["many", "exact", "workspace"]);
  assert.deepEqual(ids(notes, "ALPHA alpha guide guide"), ["many", "exact", "workspace"]);
});
test("equal scores favor exact titles, then prefixes, then deterministic names and ids", () => {
  const notes = [note("middle", "The Alpha", "Work"), note("prefix", "Alpha plan", "Work"), note("exact2", "Alpha", "Work"), note("exact1", "Alpha", "Work")];
  assert.deepEqual(ids(notes, "alpha"), ["exact1", "exact2", "prefix", "middle"]);
});
test("blank search returns recent documents without mutating the source list", () => {
  const notes = [note("a", "A", "Work", 1), note("b", "B", "Work", 3), note("c", "C", "Work", 2)];
  const original = structuredClone(notes);
  assert.deepEqual(ids(notes, " \n"), ["b", "c", "a"]);
  assert.deepEqual(notes, original);
  assert.deepEqual(searchDocuments([], ""), []);
});
test("Korean Unicode composition and repeated words are normalized", () => {
  const notes = [note("ko", "회의 기록".normalize("NFD"), "개발 팀")];
  assert.deepEqual(ids(notes, "개발 회의"), ["ko"]);
  assert.deepEqual(searchTerms("  회의  회의  개발 "), ["회의", "개발"]);
});
test("literal punctuation is searchable and highlighting never interprets HTML or regex", () => {
  const notes = [note("code", "C++ [draft] <script>", "Docs")];
  assert.deepEqual(ids(notes, "c++ [draft]"), ["code"]);
  const parts = matchingSegments(notes[0].title, "c++ [draft] <script>");
  assert.equal(parts.map((p) => p.text).join(""), notes[0].title);
  assert.deepEqual(parts.filter((p) => p.match).map((p) => p.text), ["C++", "[draft]", "<script>"]);
});
test("content, attachment paths and absent workspace labels do not leak into title search", () => {
  const notes = [{ ...note("a", "Guide", null), excerpt: "secret", relativePath: "secret.md" }];
  assert.deepEqual(ids(notes, "secret"), []);
  assert.deepEqual(ids(notes, "guide"), ["a"]);
});
