import { afterEach, describe, expect, it } from "vitest";
import { createId } from "../lib/id";

const originalCrypto = Object.getOwnPropertyDescriptor(globalThis, "crypto");

afterEach(() => {
  if (originalCrypto) {
    Object.defineProperty(globalThis, "crypto", originalCrypto);
  } else {
    Reflect.deleteProperty(globalThis, "crypto");
  }
});

describe("createId", () => {
  it("creates a UUID when randomUUID is unavailable", () => {
    let value = 0;
    Object.defineProperty(globalThis, "crypto", {
      configurable: true,
      value: {
        getRandomValues: (bytes: Uint8Array) => {
          bytes.forEach((_, index) => {
            bytes[index] = value++;
          });
          return bytes;
        },
      },
    });

    expect(createId()).toBe("00010203-0405-4607-8809-0a0b0c0d0e0f");
  });

  it("still creates a local identifier when the Web Crypto API is unavailable", () => {
    Object.defineProperty(globalThis, "crypto", {
      configurable: true,
      value: undefined,
    });

    expect(createId()).toMatch(/^local-[a-z0-9]+-[a-z0-9]+$/);
  });
});
