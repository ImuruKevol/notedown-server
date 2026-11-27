const normalize = (value) => String(value || "").normalize("NFC").toLowerCase();

export function searchTerms(query) {
  return [...new Set(normalize(query).trim().split(/\s+/).filter(Boolean))];
}

function occurrences(text, term) {
  let count = 0, position = 0;
  while ((position = text.indexOf(term, position)) !== -1) {
    count += 1;
    position += term.length;
  }
  return count;
}

const byName = (a, b) =>
  a.title.localeCompare(b.title, "ko", { numeric: true, sensitivity: "base" }) ||
  (a.workspaceName || "").localeCompare(b.workspaceName || "", "ko") ||
  a.id.localeCompare(b.id);

export function searchDocuments(notes, query) {
  const terms = searchTerms(query);
  if (!terms.length)
    return [...notes].sort((a, b) =>
      (Number(b.updatedAtMs) || 0) - (Number(a.updatedAtMs) || 0) || byName(a, b),
    );
  const phrase = normalize(query).trim().replace(/\s+/g, " ");
  return notes.flatMap((note) => {
    const title = normalize(note.title), workspace = normalize(note.workspaceName);
    const hits = terms.map((term) => occurrences(title, term) + occurrences(workspace, term));
    if (hits.some((count) => count === 0)) return [];
    return [{ note, score: hits.reduce((sum, count) => sum + count, 0),
      exact: Number(title === phrase), prefix: Number(title.startsWith(phrase)) }];
  }).sort((a, b) => b.score - a.score || b.exact - a.exact || b.prefix - a.prefix || byName(a.note, b.note))
    .map(({ note }) => note);
}

// Return display segments rather than HTML, so titles are always rendered as text.
export function matchingSegments(value, query) {
  const text = String(value || "").normalize("NFC");
  const terms = searchTerms(query);
  if (!terms.length) return [{ text, match: false }];
  const escaped = terms.sort((a, b) => b.length - a.length)
    .map((term) => term.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  const pattern = new RegExp(escaped.join("|"), "giu");
  const parts = [];
  let end = 0;
  for (const match of text.matchAll(pattern)) {
    if (match.index > end) parts.push({ text: text.slice(end, match.index), match: false });
    parts.push({ text: match[0], match: true });
    end = match.index + match[0].length;
  }
  if (end < text.length) parts.push({ text: text.slice(end), match: false });
  return parts;
}
