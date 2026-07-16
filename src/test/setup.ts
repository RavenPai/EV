import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";

// Node 25 exposes an incomplete `localStorage` global when no
// --localstorage-file is configured. It can take precedence over jsdom's
// implementation, so install a deterministic Storage implementation for tests.
const values = new Map<string, string>();
const testStorage: Storage = {
  get length() {
    return values.size;
  },
  clear() {
    values.clear();
  },
  getItem(key) {
    return values.get(String(key)) ?? null;
  },
  key(index) {
    return [...values.keys()][index] ?? null;
  },
  removeItem(key) {
    values.delete(String(key));
  },
  setItem(key, value) {
    values.set(String(key), String(value));
  },
};

Object.defineProperty(globalThis, "localStorage", {
  configurable: true,
  value: testStorage,
});

afterEach(() => {
  cleanup();
  localStorage.clear();
  window.history.pushState({}, "", "/");
});
