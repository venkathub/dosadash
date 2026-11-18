import assert from "node:assert/strict";
import { test } from "node:test";
import { cutoutKeyFor, shortestTurn, shouldRender3D, type SceneEnv } from "./scene.ts";

const capable: SceneEnv = { reducedMotion: false, saveData: false, deviceMemoryGb: 8, cores: 8 };

test("capable device gets the scene", () => assert.equal(shouldRender3D(capable), true));
test("reduced motion always wins", () =>
  assert.equal(shouldRender3D({ ...capable, reducedMotion: true }), false));
test("data saver opts out", () => assert.equal(shouldRender3D({ ...capable, saveData: true }), false));
test("low memory or few cores opt out", () => {
  assert.equal(shouldRender3D({ ...capable, deviceMemoryGb: 2 }), false);
  assert.equal(shouldRender3D({ ...capable, cores: 2 }), false);
});
test("unknown capability counts as capable (Safari hides both)", () =>
  assert.equal(shouldRender3D({ ...capable, deviceMemoryGb: null, cores: null }), true));

test("cutouts match exact English names only", () => {
  assert.equal(cutoutKeyFor("Masala Dosa"), "masala-dosa");
  assert.equal(cutoutKeyFor("  idli (2 pcs) "), "idli");
  assert.equal(cutoutKeyFor("Mysore Masala Dosa"), null); // never borrow a sibling's photo
  assert.equal(cutoutKeyFor("மசாலா தோசை"), null); // callers must pass the canonical name
});

test("the table takes the short way round", () => {
  assert.equal(shortestTurn(0, 1, 5), 1);
  assert.equal(shortestTurn(0, 4, 5), -1);
  assert.equal(shortestTurn(3, 0, 5), 2);
  assert.equal(shortestTurn(2, 2, 5), 0);
});
