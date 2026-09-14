import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, expect, test, vi } from "vitest";
import DocView from "../DocView";

// Mirrors the innerWidth-restore pattern established in
// appLayout.test.tsx/chatPage.test.tsx: Object.defineProperty on innerWidth
// isn't reverted by vi.unstubAllGlobals(), so it's captured/restored here too
// — otherwise a stubbed viewport would leak into later tests in this file
// (useIsDesktop reads window.innerWidth).
const ORIGINAL_INNER_WIDTH = window.innerWidth;
afterEach(() => {
  vi.unstubAllGlobals();
  Object.defineProperty(window, "innerWidth", { configurable: true, writable: true, value: ORIGINAL_INNER_WIDTH });
});

function stubViewport(width: number) {
  Object.defineProperty(window, "innerWidth", { configurable: true, writable: true, value: width });
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: query.includes("min-width") ? width >= 1024 : width < 768,
    media: query,
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
  }));
}

// A richer stub for the one test below that simulates a LIVE breakpoint
// crossing within a single mount — an actual window resize or tablet
// rotation, as opposed to comparing two separate mounts. It captures the
// "change" listener useIsDesktop's effect registers on window.matchMedia so
// the test can invoke it after moving innerWidth, the same way a real
// matchMedia "change" event fires in a browser.
function stubResizableViewport(initialWidth: number) {
  let width = initialWidth;
  let changeListener: (() => void) | null = null;
  Object.defineProperty(window, "innerWidth", { configurable: true, writable: true, value: width });
  vi.stubGlobal("matchMedia", (query: string) => ({
    get matches() { return query.includes("min-width") ? width >= 1024 : width < 768; },
    media: query,
    onchange: null,
    addEventListener: (event: string, cb: () => void) => { if (event === "change") changeListener = cb; },
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
  }));
  return {
    resizeTo(newWidth: number) {
      width = newWidth;
      Object.defineProperty(window, "innerWidth", { configurable: true, writable: true, value: newWidth });
      changeListener?.();
    },
  };
}

const DOC = {
  path: "how-to/refunds.md", title: "Refunds",
  markdown: "# Refunds\n\n## Requesting\nBody here.",
  front_matter: {
    updated: "2026-08-01",
    analyzed_commits: {
      helpers: "2441f2cda8902cd38e393a53d09a1dda5ffc3d27",
      tusers: "5bcbd625835b6e29bf52fade0113e321636a68ce",
    },
  }, freshness: "current",
  headings: [{ text: "Requesting", anchor: "requesting", level: 2 }],
};

test("renders markdown, TOC links, and the build-metadata line from front matter", async () => {
  vi.stubGlobal("fetch", vi.fn(async (url: string) =>
    new Response(JSON.stringify(String(url).includes("/doc?") ? DOC : []))));
  render(<MemoryRouter><DocView app="shop" path="how-to/refunds.md" /></MemoryRouter>);
  await waitFor(() => expect(screen.getByRole("heading", { name: "Refunds", level: 1 })).toBeInTheDocument());
  const tocLink = screen.getByRole("link", { name: "Requesting" });
  expect(tocLink).toHaveAttribute("href", "#requesting");
  // Freshness badge is gone (it read "unknown" on every real install);
  // replaced by a metadata line built purely from front matter.
  expect(screen.queryByText("current")).toBeNull();
  expect(screen.getByText(/Updated 2026-08-01/)).toBeInTheDocument();
  expect(screen.getByText(/helpers @ 2441f2c\b/)).toBeInTheDocument();
  expect(screen.getByText(/tusers @ 5bcbd62\b/)).toBeInTheDocument();
});

test("doc without front matter renders no metadata line", async () => {
  const bare = { ...DOC, front_matter: {} };
  vi.stubGlobal("fetch", vi.fn(async (url: string) =>
    new Response(JSON.stringify(String(url).includes("/doc?") ? bare : []))));
  render(<MemoryRouter><DocView app="shop" path="how-to/refunds.md" /></MemoryRouter>);
  await waitFor(() => expect(screen.getByRole("heading", { name: "Refunds", level: 1 })).toBeInTheDocument());
  expect(screen.queryByText(/Updated/)).toBeNull();
  expect(screen.queryByText(/built from/)).toBeNull();
});

test("404 shows in-app panel, not a crash", async () => {
  vi.stubGlobal("fetch", vi.fn(async () =>
    new Response(JSON.stringify({ error: { message: "no such document: nope.md" } }), { status: 404 })));
  render(<MemoryRouter><DocView app="shop" path="nope.md" /></MemoryRouter>);
  await waitFor(() => expect(screen.getByText(/no such document/)).toBeInTheDocument());
});

function mockDocFetch() {
  return vi.fn(async (url: string) =>
    new Response(JSON.stringify(String(url).includes("/doc?") ? DOC : [])));
}

// At `lg`+ (1024px, useIsDesktop's breakpoint) the TOC renders as a plain
// static list — no collapsible chrome at all, matching the original
// desktop-only aside exactly.
test("desktop viewport: TOC list visible, no collapsible trigger", async () => {
  stubViewport(1280);
  vi.stubGlobal("fetch", mockDocFetch());
  render(<MemoryRouter><DocView app="shop" path="how-to/refunds.md" /></MemoryRouter>);
  await waitFor(() => expect(screen.getByRole("heading", { name: "Refunds", level: 1 })).toBeInTheDocument());

  expect(screen.getByRole("link", { name: "Requesting" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "On this page" })).toBeNull();
});

// Below `lg` the desktop-only aside is replaced by a single shared heading
// list behind a "On this page" Collapsible trigger, so the TOC stays
// reachable on small screens without duplicating the anchor links (which
// would make the desktop test's unscoped `getByRole("link", ...)` query
// ambiguous — jsdom applies no stylesheet, so a CSS-only duplicate would be
// "visible" to both queries at once). Defaults open — the trigger only needs
// to prove it can genuinely hide/reveal the same links.
test("mobile viewport: 'On this page' collapsible trigger toggles the same TOC links", async () => {
  stubViewport(375);
  vi.stubGlobal("fetch", mockDocFetch());
  render(<MemoryRouter><DocView app="shop" path="how-to/refunds.md" /></MemoryRouter>);
  await waitFor(() => expect(screen.getByRole("heading", { name: "Refunds", level: 1 })).toBeInTheDocument());

  const trigger = screen.getByRole("button", { name: "On this page" });
  expect(screen.getByRole("link", { name: "Requesting" })).toBeInTheDocument();

  await userEvent.click(trigger);
  await waitFor(() => expect(screen.queryByRole("link", { name: "Requesting" })).toBeNull());

  await userEvent.click(trigger);
  await waitFor(() => expect(screen.getByRole("link", { name: "Requesting" })).toBeInTheDocument());
});

// The regression this whole breakpoint-branch exists to fix: a single
// Radix Collapsible instance shared across breakpoints (trigger `lg:hidden`
// only) let a user collapse the TOC below `lg`, then widen past `lg`
// (tablet rotation, split-screen, window resize) with the TOC stuck closed
// and no visible control to reopen it, since the trigger itself vanished at
// `lg`+. Branching on useIsDesktop() means the two renderings are genuinely
// different mounts — collapsing one never leaks into the other. This test
// proves the weakest form of that guarantee (a fresh desktop mount, e.g. a
// reload/re-navigation at desktop width, is never affected by whatever
// happened on a prior mobile mount); see the test below for a live,
// same-mount breakpoint crossing.
test("regression: collapsing the TOC on mobile does not strand it closed on a later desktop render", async () => {
  stubViewport(375);
  vi.stubGlobal("fetch", mockDocFetch());
  const mobile = render(<MemoryRouter><DocView app="shop" path="how-to/refunds.md" /></MemoryRouter>);
  await waitFor(() => expect(mobile.getByRole("heading", { name: "Refunds", level: 1 })).toBeInTheDocument());

  await userEvent.click(mobile.getByRole("button", { name: "On this page" }));
  await waitFor(() => expect(mobile.queryByRole("link", { name: "Requesting" })).toBeNull());
  mobile.unmount();

  stubViewport(1280);
  render(<MemoryRouter><DocView app="shop" path="how-to/refunds.md" /></MemoryRouter>);
  await waitFor(() => expect(screen.getByRole("heading", { name: "Refunds", level: 1 })).toBeInTheDocument());
  expect(screen.getByRole("link", { name: "Requesting" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "On this page" })).toBeNull();
});

// Bonus (not required, offered per the review's "welcome but not required"
// note): the stub above can be extended to capture the "change" listener
// useIsDesktop's effect registers on its matchMedia query, so this test can
// simulate a LIVE resize/rotation within a single mount — not just two
// separate mounts — reproducing the exact scenario from the finding (crossing
// the breakpoint without a page reload). What the stub can honestly exercise:
// it invokes the same listener a real matchMedia "change" event would call,
// after moving window.innerWidth, so it validates the same code path
// (onChange -> setIsDesktop -> re-render -> branch swap -> Collapsible
// remount) a real resize would take — it does not use an actual browser
// resize/orientation event.
test("live breakpoint crossing within one mount: widening past lg after collapsing reopens the TOC", async () => {
  const viewport = stubResizableViewport(375);
  vi.stubGlobal("fetch", mockDocFetch());
  render(<MemoryRouter><DocView app="shop" path="how-to/refunds.md" /></MemoryRouter>);
  await waitFor(() => expect(screen.getByRole("heading", { name: "Refunds", level: 1 })).toBeInTheDocument());

  await userEvent.click(screen.getByRole("button", { name: "On this page" }));
  await waitFor(() => expect(screen.queryByRole("link", { name: "Requesting" })).toBeNull());

  act(() => viewport.resizeTo(1280));
  await waitFor(() => expect(screen.getByRole("link", { name: "Requesting" })).toBeInTheDocument());
  expect(screen.queryByRole("button", { name: "On this page" })).toBeNull();
});
