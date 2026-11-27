function decode(value) {
  try {
    return decodeURIComponent(value);
  } catch {
    return value;
  }
}
// Synced macOS filenames and copied links can use decomposed Unicode even when
// the same visible storage path uses composed characters (or vice versa).
const normalize = (value) => value?.normalize("NFC");

export function resolveAttachment(value, notePath, attachments) {
  // Paths in Notedown are storage-root relative. Also accept ordinary Markdown
  // document-relative URLs. Never rewrite a remote site's URL to a local file.
  if (/^(?:[a-z][a-z\d+.-]*:|\/\/)/i.test(value)) return null;
  const withoutSuffix = value.split(/[?#]/, 1)[0];
  const candidates = new Set([
    normalize(decode(withoutSuffix).replace(/^\.?\//, "")),
  ]);
  try {
    candidates.add(
      normalize(
        decode(
          new URL(withoutSuffix, "https://notedown.invalid/" + notePath).pathname,
        ).replace(/^\//, ""),
      ),
    );
  } catch {}
  return attachments.find(
    (a) =>
      value === a.url ||
      value === a.markdownPath ||
      candidates.has(normalize(a.relativePath)),
  );
}
