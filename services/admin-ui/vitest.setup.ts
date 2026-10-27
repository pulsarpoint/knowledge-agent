import "@testing-library/jest-dom/vitest";
import { afterEach, vi } from "vitest";
import { toast } from "sonner";

// Sonner keeps its toasts in module state and replays the active ones to a newly mounted Toaster: dismiss them after
// each test so one test's toast never shows up in the next.
afterEach(() => {
  toast.dismiss();
});

// Radix form controls measure their hidden inputs; jsdom has no layout observer.
if (typeof globalThis.ResizeObserver === "undefined") {
  globalThis.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
}

// Radix Select uses pointer capture and scrolls the highlighted option into view.
if (!Element.prototype.hasPointerCapture) Element.prototype.hasPointerCapture = () => false;
if (!Element.prototype.setPointerCapture) Element.prototype.setPointerCapture = () => {};
if (!Element.prototype.releasePointerCapture) Element.prototype.releasePointerCapture = () => {};
if (!Element.prototype.scrollIntoView) Element.prototype.scrollIntoView = () => {};

// SVG rendering/geometry requires a browser; parser tests can unmock Mermaid.
vi.mock("mermaid", () => ({
  default: { initialize: vi.fn(), parse: vi.fn(async () => ({ diagramType: "flowchart-v2" })), render: vi.fn(async () => ({ svg: "<svg></svg>" })) },
}));
vi.mock("svg-pan-zoom", () => ({ default: vi.fn(() => ({ destroy: vi.fn(), getSizes: () => ({ realZoom: 1 }) })) }));

// Node >=22 ships an experimental global `localStorage` accessor that throws/returns
// undefined unless started with --localstorage-file. Because it already exists on
// globalThis, vitest's jsdom environment skips installing jsdom's real Storage there
// (see vitest's populateGlobal: keys already `in global` are dropped unless allow-listed).
// Result: globalThis.localStorage resolves to undefined during tests. Polyfill with an
// in-memory Storage only when that's the case; leave jsdom's own implementation alone
// on Node versions/configs where it works correctly.
if (typeof globalThis.localStorage === "undefined") {
  class MemoryStorage implements Storage {
    private store = new Map<string, string>();
    get length() {
      return this.store.size;
    }
    clear(): void {
      this.store.clear();
    }
    getItem(key: string): string | null {
      return this.store.has(key) ? this.store.get(key)! : null;
    }
    key(index: number): string | null {
      return Array.from(this.store.keys())[index] ?? null;
    }
    removeItem(key: string): void {
      this.store.delete(key);
    }
    setItem(key: string, value: string): void {
      this.store.set(key, String(value));
    }
  }
  Object.defineProperty(globalThis, "localStorage", {
    value: new MemoryStorage(),
    configurable: true,
    writable: true,
  });
}

// jsdom doesn't implement window.matchMedia. shadcn's Sidebar (via useIsMobile)
// calls it unconditionally, so tests that mount Sidebar-family components need
// a minimal stand-in. Guarded: only installed when the environment doesn't
// already provide one, so a real browser-like matchMedia is never shadowed.
if (typeof window.matchMedia === "undefined") {
  window.matchMedia = (): MediaQueryList =>
    ({
      matches: false,
      media: "",
      onchange: null,
      addEventListener() {},
      removeEventListener() {},
      addListener() {},
      removeListener() {},
      dispatchEvent: () => false,
    }) as MediaQueryList;
}
