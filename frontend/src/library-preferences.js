const KEY = "notedown-library-preferences";
export function readLibraryPreferences() {
  let value = {};
  try {
    value = JSON.parse(localStorage.getItem(KEY) || "{}") || {};
  } catch {}
  return {
    sort: ["updated", "created", "name"].includes(value.sort)
      ? value.sort
      : "updated",
    direction: ["asc", "desc"].includes(value.direction)
      ? value.direction
      : "desc",
    layout: ["cards", "table"].includes(value.layout) ? value.layout : "cards",
  };
}
export function saveLibraryPreferences(preferences) {
  try {
    localStorage.setItem(KEY, JSON.stringify(preferences));
  } catch {}
}
export function compareNotes(a, b, sort, direction) {
  const sign = direction === "asc" ? 1 : -1;
  if (sort === "name")
    return (
      sign *
        a.title.localeCompare(b.title, "ko", {
          numeric: true,
          sensitivity: "base",
        }) || a.id.localeCompare(b.id)
    );
  const key = sort === "created" ? "createdAtMs" : "updatedAtMs";
  const left = Number(a[key]),
    right = Number(b[key]);
  const hasLeft = Number.isFinite(left) && left > 0,
    hasRight = Number.isFinite(right) && right > 0;
  // Missing historical creation dates stay last in either direction.
  if (hasLeft !== hasRight) return hasLeft ? -1 : 1;
  return (
    (hasLeft ? sign * (left - right) : 0) ||
    a.title.localeCompare(b.title, "ko", { numeric: true }) ||
    a.id.localeCompare(b.id)
  );
}
