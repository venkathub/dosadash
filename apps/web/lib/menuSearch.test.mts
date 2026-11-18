import assert from "node:assert/strict";
import { test } from "node:test";
import { isSearching, normalizeSearch, searchMenu, type Searchable } from "./menuSearch.ts";

const dish = (name: string, category = "Dosa", canonical: string | null = null): Searchable => ({
  name,
  canonical_name: canonical,
  category,
  category_label: null,
});
const MENU = [
  dish("Masala Dosa"),
  dish("Mysore Masala Dosa"),
  dish("Idli (2 pcs)", "Idli & Vada"),
  dish("Mini Idli Sambar", "Idli & Vada"),
  dish("Medu Vada (2 pcs)", "Idli & Vada"),
  dish("Filter Coffee", "Beverages"),
  dish("Masala Chai", "Beverages"),
  dish("Chicken Biryani", "Biryani"),
  dish("மசாலா தோசை", "Dosa", "Paneer Masala Dosa"),
];
const find = (q: string) => searchMenu(MENU, q).map((m) => m.canonical_name ?? m.name);

test("exact and partial names match", () => {
  assert.deepEqual(find("mysore masala dosa"), ["Mysore Masala Dosa"]);
  assert.deepEqual(find("coff"), ["Filter Coffee"]);
});
test("word order does not matter", () => {
  assert.deepEqual(find("dosa mysore"), ["Mysore Masala Dosa"]);
});
test("every word must match", () => {
  assert.deepEqual(find("filter dosa"), []);
});
test("common spellings are folded", () => {
  assert.deepEqual(find("idly"), ["Idli (2 pcs)", "Mini Idli Sambar"]);
  assert.deepEqual(find("vadai"), ["Medu Vada (2 pcs)"]);
  assert.deepEqual(find("biriyani"), ["Chicken Biryani"]);
  assert.deepEqual(find("kaapi"), ["Filter Coffee"]);
  assert.deepEqual(find("tea"), ["Masala Chai"]);
  assert.ok(find("dosai").includes("Masala Dosa"));
});
test("a dish name beats a shared category: idli does not return the vadas", () => {
  assert.ok(!find("idli").includes("Medu Vada (2 pcs)"));
});
test("a category name is the fallback when no dish name matches", () => {
  assert.deepEqual(find("beverages"), ["Filter Coffee", "Masala Chai"]);
});
test("punctuation and case are ignored", () => {
  assert.deepEqual(find("IDLI 2 pcs"), ["Idli (2 pcs)"]);
  assert.equal(normalizeSearch("  Idli (2 pcs)!  "), "idli 2 pcs");
});
test("a translated dish is found by Tamil or English", () => {
  assert.deepEqual(find("மசாலா"), ["Paneer Masala Dosa"]);
  assert.deepEqual(find("paneer"), ["Paneer Masala Dosa"]);
});
test("short or empty queries return the whole menu", () => {
  assert.equal(isSearching("d"), false);
  assert.equal(isSearching(" do "), true);
  assert.equal(searchMenu(MENU, "x").length, MENU.length);
  assert.equal(searchMenu(MENU, "").length, MENU.length);
});
