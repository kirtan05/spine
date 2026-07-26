/**
 * The document catalogue: doc_hash -> what the book actually is.
 *
 * Two feeds, with a deliberate precedence rule between them:
 *
 * 1. KOReader's metadata push (Phase 1). Off by default on-device — "Send
 *    document metadata" has to be enabled on every device — and it carries only
 *    filename, title, authors.
 * 2. The ingest pipeline's catalogue sync (Phase 2 step 9), which also carries
 *    series and series_index, and whose comic titles come from ComicInfo rather
 *    than from whatever the file happens to be called.
 *
 * The push here fills gaps only (COALESCE existing first) while the pipeline
 * overwrites. Otherwise every sync would clobber a curated ComicInfo title with
 * KOReader's display title, on a 25-second debounce, forever.
 */

export interface PushMetadata {
  filename?: string;
  title?: string;
  authors?: string;
}

/** Extract the optional `metadata` object off a push body, or null. */
export function parseMetadata(value: unknown): PushMetadata | null {
  if (typeof value !== "object" || value === null) return null;
  const raw = value as Record<string, unknown>;
  const pick = (k: string): string | undefined => {
    const v = raw[k];
    if (typeof v !== "string") return undefined;
    const trimmed = v.trim();
    return trimmed === "" ? undefined : trimmed;
  };
  const meta: PushMetadata = {
    filename: pick("filename"),
    title: pick("title"),
    // KOReader joins multiple authors with newlines; store what it sent.
    authors: pick("authors"),
  };
  return meta.filename || meta.title || meta.authors ? meta : null;
}

export function upsertFromPush(
  db: D1Database,
  docHash: string,
  meta: PushMetadata,
  now: number,
): D1PreparedStatement {
  return db
    .prepare(
      `INSERT INTO documents (doc_hash, title, authors, filename, first_seen)
       VALUES (?1, ?2, ?3, ?4, ?5)
       ON CONFLICT(doc_hash) DO UPDATE SET
         title    = COALESCE(documents.title,    excluded.title),
         authors  = COALESCE(documents.authors,  excluded.authors),
         filename = COALESCE(documents.filename, excluded.filename)`,
    )
    .bind(docHash, meta.title ?? null, meta.authors ?? null, meta.filename ?? null, now);
}
