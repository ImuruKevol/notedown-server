import assert from "node:assert/strict";
import test from "node:test";
import {
  compareNotes,
  readLibraryPreferences,
  saveLibraryPreferences,
} from "./library-preferences.js";

const defaults = { sort: "updated", direction: "desc", layout: "cards" };
const ids = (notes, sort, direction) =>
  [...notes].sort((a, b) => compareNotes(a, b, sort, direction)).map((note) => note.id);

function withStorage(descriptor, run) {
  const original = Object.getOwnPropertyDescriptor(globalThis, "localStorage");
  try {
    if (descriptor) {
      Object.defineProperty(globalThis, "localStorage", {
        configurable: true,
        ...descriptor,
      });
    } else {
      delete globalThis.localStorage;
    }
    run();
  } finally {
    if (original) Object.defineProperty(globalThis, "localStorage", original);
    else delete globalThis.localStorage;
  }
}

test("unknown creation and modification dates stay after known dates in either direction", () => {
  for (const [sort, field] of [["created", "createdAtMs"], ["updated", "updatedAtMs"]]) {
    const unknown = [null, undefined, 0, -1, "invalid-date", Infinity].map((value, index) => ({
      id: `unknown-${index}`,
      title: `날짜 없음 ${index}`,
      [field]: value,
    }));
    const notes = [
      ...unknown,
      { id: "later", title: "가장 최근", [field]: 1750000000000 },
      { id: "earlier", title: "오래된 문서", [field]: 1700000000000 },
    ];
    for (const direction of ["asc", "desc"]) {
      const ordered = ids(notes, sort, direction);
      assert.deepEqual(
        ordered.slice(0, 2),
        direction === "asc" ? ["earlier", "later"] : ["later", "earlier"],
        `${sort} ${direction}: known dates sort chronologically`,
      );
      assert.deepEqual(
        new Set(ordered.slice(2)),
        new Set(unknown.map((note) => note.id)),
        `${sort} ${direction}: every unknown date remains last`,
      );
    }
  }
});

test("creation and modification sorting use their respective timestamps", () => {
  const notes = [
    { id: "old-revised", title: "오래된 기록", createdAtMs: 1704067200000, updatedAtMs: 1735689600000 },
    { id: "middle", title: "중간 기록", createdAtMs: 1719792000000, updatedAtMs: 1722470400000 },
    { id: "new-untouched", title: "새 기록", createdAtMs: 1725148800000, updatedAtMs: 1725148800000 },
  ];
  assert.deepEqual(ids(notes, "created", "asc"), ["old-revised", "middle", "new-untouched"]);
  assert.deepEqual(ids(notes, "created", "desc"), ["new-untouched", "middle", "old-revised"]);
  assert.deepEqual(ids(notes, "updated", "asc"), ["middle", "new-untouched", "old-revised"]);
  assert.deepEqual(ids(notes, "updated", "desc"), ["old-revised", "new-untouched", "middle"]);
});

test("Korean names sort naturally with numeric suffixes in both directions", () => {
  const notes = [
    { id: "10", title: "기록 10" },
    { id: "meeting", title: "회의 1" },
    { id: "2", title: "기록 2" },
    { id: "1", title: "기록 1" },
  ];
  assert.deepEqual(ids(notes, "name", "asc"), ["1", "2", "10", "meeting"]);
  assert.deepEqual(ids(notes, "name", "desc"), ["meeting", "10", "2", "1"]);
});

test("equal titles and timestamps have a deterministic order across reloads", () => {
  const notes = ["note-b", "note-c", "note-a"].map((id) => ({
    id, title: "같은 이름", createdAtMs: 1700000000000, updatedAtMs: 1700000000000,
  }));
  for (const sort of ["name", "created", "updated"]) {
    for (const direction of ["asc", "desc"]) {
      assert.deepEqual(ids(notes, sort, direction), ids([...notes].reverse(), sort, direction));
    }
  }
});

test("valid ordering and layout preferences survive a storage round trip", () => {
  const entries = new Map();
  withStorage({ value: {
    getItem: (key) => entries.get(key) ?? null,
    setItem: (key, value) => entries.set(key, value),
  } }, () => {
    assert.deepEqual(readLibraryPreferences(), defaults);
    const preferences = { sort: "created", direction: "asc", layout: "table" };
    saveLibraryPreferences(preferences);
    assert.deepEqual(readLibraryPreferences(), preferences);
  });
});

test("corrupt or unsupported stored preferences fall back to usable defaults", () => {
  for (const raw of ["{broken", "null", "[]", '"unexpected"', '{"sort":"size","direction":"sideways","layout":"tiles"}']) {
    withStorage({ value: { getItem: () => raw } }, () => {
      assert.deepEqual(readLibraryPreferences(), defaults, raw);
    });
  }
  withStorage({ value: { getItem: () => '{"sort":"name","direction":"invalid","layout":"table"}' } }, () => {
    assert.deepEqual(readLibraryPreferences(), { sort: "name", direction: "desc", layout: "table" });
  });
});

test("missing or denied localStorage never prevents opening the library", () => {
  for (const descriptor of [
    null,
    { get: () => { throw new Error("Storage access denied"); } },
    { value: {
      getItem: () => { throw new Error("Storage access denied"); },
      setItem: () => { throw new Error("Storage quota exceeded"); },
    } },
  ]) {
    withStorage(descriptor, () => {
      assert.deepEqual(readLibraryPreferences(), defaults);
      assert.doesNotThrow(() => saveLibraryPreferences({ sort: "created", direction: "asc", layout: "table" }));
    });
  }
});
