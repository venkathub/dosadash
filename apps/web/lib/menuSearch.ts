// Menu search (customer home). Pure, unit-tested in menuSearch.test.mts.
//
// A query finds a dish when EVERY word in it is in the dish's name (shown
// name or English name), in any order. Words are compared after folding the
// spellings people actually type for South Indian food, so "idly", "dosai"
// and "vadai" find idli, dosa and vada. See searchMenu for the category rule.

/** Spelling folds, applied to both the query and the dish text. */
const FOLDS: [RegExp, string][] = [
  [/\bidl[iy]s?\b/g, "idli"],
  [/\bidlies\b/g, "idli"],
  [/\b(dosai|dosas|dosha|thosai|dose)\b/g, "dosa"],
  [/\b(vadai|vadas|wada|vade)\b/g, "vada"],
  [/\b(biriyani|briyani|biriani|biryanis)\b/g, "biryani"],
  [/\b(kaapi|kapi|coffe|cofee)\b/g, "coffee"],
  [/\b(tea|chaya)\b/g, "chai"],
  [/\b(parota|paratha|barotta|porotta)\b/g, "parotta"],
  [/\b(uthappam|uttappam|oothappam|uthapam)\b/g, "uttapam"],
  [/\b(sambhar|sambaar)\b/g, "sambar"],
  [/\b(pongali|pongals)\b/g, "pongal"],
];

/** Lowercase, drop punctuation (Tamil and other scripts are kept), fold spellings. */
export function normalizeSearch(text: string): string {
  let t = text
    .toLowerCase()
    .replace(/[^\p{L}\p{M}\p{N}\s]/gu, " ")
    .replace(/\s+/g, " ")
    .trim();
  for (const [pattern, to] of FOLDS) t = t.replace(pattern, to);
  return t;
}

export type Searchable = {
  name: string;
  canonical_name: string | null;
  category: string;
  category_label: string | null;
};

/** Queries shorter than this do not filter (one letter matches almost everything). */
export const MIN_QUERY = 2;

export function isSearching(query: string): boolean {
  return normalizeSearch(query).length >= MIN_QUERY;
}

const allWordsIn = (haystack: string, q: string) => q.split(" ").every((word) => haystack.includes(word));

/**
 * The dishes a query finds, in their original order.
 *
 * Dish NAMES decide. Category names are only a fallback, used when no dish
 * name matches at all — otherwise "idli" would also return every vada,
 * because they share the "Idli & Vada" category.
 */
export function searchMenu<T extends Searchable>(items: T[], query: string): T[] {
  const q = normalizeSearch(query);
  if (q.length < MIN_QUERY) return items;
  const byName = items.filter((m) => allWordsIn(normalizeSearch(`${m.name} ${m.canonical_name ?? ""}`), q));
  if (byName.length > 0) return byName;
  return items.filter((m) => allWordsIn(normalizeSearch(`${m.category} ${m.category_label ?? ""}`), q));
}
