import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, expect, test, vi } from "vitest";
import AppLayout from "../AppLayout";

// stubMobileViewport() below redefines window.innerWidth directly (not via
// vi.stubGlobal), so vi.unstubAllGlobals() alone won't revert it — without
// restoring it here too, one mobile test would leak innerWidth=375 into
// every later test in this file (useIsMobile reads window.innerWidth).
const ORIGINAL_INNER_WIDTH = window.innerWidth;
afterEach(() => {
  vi.unstubAllGlobals();
  Object.defineProperty(window, "innerWidth", { configurable: true, writable: true, value: ORIGINAL_INNER_WIDTH });
});

const TOC = [
  { group: "Guides", docs: [{ path: "how-to/refunds.md", title: "Refunds", freshness: "current" }] },
];

function mockAppFetch(toc: unknown, opts?: { isAdmin?: boolean }) {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    const u = String(url);
    if (u.endsWith("/api/apps")) {
      return new Response(JSON.stringify([
        { name: "shop", ask_enabled: true, ask_reason: null, is_admin: opts?.isAdmin ?? false },
      ]));
    }
    if (u.includes("/toc")) return new Response(JSON.stringify(toc));
    return new Response(JSON.stringify([]));
  }));
}

// Scopes queries to the Breadcrumb's own <nav aria-label="breadcrumb">,
// since doc titles / section names also appear as ordinary nav links in
// AppSidebar — without scoping, "Refunds" or "Search" would be ambiguous.
function breadcrumb() {
  return within(screen.getByRole("navigation", { name: "breadcrumb" }));
}

// Scopes queries to the sidebar's own content region ([data-sidebar=
// "content"], set by ui/sidebar.tsx), since section names like "Search"
// also render as the breadcrumb's current-page label (itself exposed with
// role="link" via BreadcrumbPage) — without scoping, that's ambiguous too.
function sidebarNav() {
  return within(document.querySelector('[data-sidebar="content"]') as HTMLElement);
}

// useIsMobile (app/hooks/use-mobile.tsx) derives its state from
// window.innerWidth, using matchMedia only to listen for resize-driven
// changes — so a mobile-mode test needs innerWidth below the 768px
// breakpoint stubbed in (per the plan's guidance, matchMedia is stubbed
// too, matching the mobile query's `matches`).
function stubMobileViewport() {
  Object.defineProperty(window, "innerWidth", { configurable: true, writable: true, value: 375 });
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: query.includes("max-width"),
    media: query,
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
  }));
}

test("mobile viewport: sidebar renders behind the trigger as a Sheet, nav content reachable once opened", async () => {
  stubMobileViewport();
  mockAppFetch(TOC);

  render(
    <MemoryRouter initialEntries={["/shop"]}>
      <AppLayout app="shop"><div>page body</div></AppLayout>
    </MemoryRouter>,
  );

  // The trigger is always visible/tappable, mobile or not — renders without
  // crashing on a sub-768px viewport.
  const trigger = await screen.findByRole("button", { name: "Toggle Sidebar" });
  expect(trigger).toBeInTheDocument();

  // Sidebar nav lives inside a closed Sheet until tapped open on mobile.
  expect(screen.queryByRole("link", { name: "Search" })).toBeNull();

  await userEvent.click(trigger);

  // Radix Sheet portals its content to document.body; `screen` queries the
  // whole document, so the nav (icons row + quadrant docs) is reachable.
  await waitFor(() => expect(screen.getByRole("link", { name: "Search" })).toBeInTheDocument());
  expect(screen.getByRole("link", { name: "Chat" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Gaps" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: /^Refunds/ })).toBeInTheDocument();
});

test("doc route: activePath resolved in toc — breadcrumb shows the app link, quadrant label, and doc title as the current page", async () => {
  mockAppFetch(TOC);

  render(
    <MemoryRouter initialEntries={["/shop/doc/how-to/refunds.md"]}>
      <AppLayout app="shop" activePath="how-to/refunds.md"><div>doc body</div></AppLayout>
    </MemoryRouter>,
  );

  // Quadrant label only appears once the toc fetch resolves.
  await waitFor(() => expect(breadcrumb().getByText("Guides")).toBeInTheDocument());
  expect(breadcrumb().getByRole("link", { name: "shop" })).toHaveAttribute("href", "/shop");
  expect(breadcrumb().getByRole("link", { name: "Refunds", current: "page" })).toBeInTheDocument();
});

test("doc route: activePath not found in any toc group — only the app crumb renders, never crashes", async () => {
  mockAppFetch(TOC);

  render(
    <MemoryRouter initialEntries={["/shop/doc/no-such.md"]}>
      <AppLayout app="shop" activePath="no-such.md"><div>doc body</div></AppLayout>
    </MemoryRouter>,
  );

  // Wait for the toc fetch to settle (sidebar's "Refunds" nav link appears)
  // before asserting on the breadcrumb's post-resolution state.
  await waitFor(() => expect(screen.getByRole("link", { name: /^Refunds/ })).toBeInTheDocument());

  // The app crumb is the only item, rendered as the current page (not a
  // link out) since there's no trailing quadrant/title crumb to lead to it.
  expect(breadcrumb().getByRole("link", { name: "shop", current: "page" })).toBeInTheDocument();
  expect(breadcrumb().queryByText("Guides")).toBeNull();
  expect(breadcrumb().queryByText("Refunds")).toBeNull();
});

test("section route (no activePath): breadcrumb trails with the section name as the current page", async () => {
  mockAppFetch(TOC);

  render(
    <MemoryRouter initialEntries={["/shop/search"]}>
      <AppLayout app="shop"><div>search body</div></AppLayout>
    </MemoryRouter>,
  );

  await waitFor(() =>
    expect(breadcrumb().getByRole("link", { name: "Search", current: "page" })).toBeInTheDocument());
  expect(breadcrumb().getByRole("link", { name: "shop" })).toHaveAttribute("href", "/shop");
  // No quadrant crumb on a non-doc route.
  expect(breadcrumb().queryByText("Guides")).toBeNull();
});

test("sidebar state: cookie sidebar_state=false renders sidebar collapsed on load", async () => {
  mockAppFetch(TOC);
  document.cookie = "sidebar_state=false";

  render(
    <MemoryRouter initialEntries={["/shop"]}>
      <AppLayout app="shop"><div>page body</div></AppLayout>
    </MemoryRouter>,
  );

  // Wait for the sidebar to render (the toc fetch completes and the nav link appears).
  // SidebarProvider writes defaultOpen state to data-state on the sidebar group container.
  await screen.findByRole("link", { name: "Search" });
  const sidebarGroup = document.querySelector("[data-state][data-collapsible]");
  expect(sidebarGroup).toHaveAttribute("data-state", "collapsed");
});

test("sidebar state: absent sidebar_state cookie renders sidebar expanded on load", async () => {
  mockAppFetch(TOC);
  // Clear any existing sidebar_state cookie.
  document.cookie = "sidebar_state=; expires=Thu, 01 Jan 1970 00:00:00 UTC";

  render(
    <MemoryRouter initialEntries={["/shop"]}>
      <AppLayout app="shop"><div>page body</div></AppLayout>
    </MemoryRouter>,
  );

  // Wait for the sidebar to render (the toc fetch completes and the nav link appears).
  // SidebarProvider writes defaultOpen state to data-state on the sidebar group container.
  await screen.findByRole("link", { name: "Search" });
  const sidebarGroup = document.querySelector("[data-state][data-collapsible]");
  expect(sidebarGroup).toHaveAttribute("data-state", "expanded");
});


test("sidebar doc entry shows the full title in a hover card on hover", async () => {
  const longTitle = "A very long operational runbook document title that truncates";
  mockAppFetch([
    { group: "Guides", docs: [{ path: "how-to/long.md", title: longTitle, freshness: "unknown" }] },
  ]);
  render(
    <MemoryRouter initialEntries={["/shop"]}>
      <AppLayout app="shop"><div>body</div></AppLayout>
    </MemoryRouter>,
  );
  const entry = await screen.findByRole("link", { name: longTitle });
  await userEvent.hover(entry);
  // Radix HoverCard portals the content; the full title then appears twice.
  await waitFor(() =>
    expect(screen.getAllByText(longTitle).length).toBeGreaterThan(1));
});

test("reader sidebar excludes administration even for admins", async () => {
  mockAppFetch(TOC, { isAdmin: true });
  render(
    <MemoryRouter initialEntries={["/shop/search"]}>
      <AppLayout app="shop"><div /></AppLayout>
    </MemoryRouter>,
  );
  await waitFor(() =>
    expect(screen.queryByRole("link", { name: /admin/i })).toBeNull());
});

test("sidebar hides Admin nav for non-admins", async () => {
  mockAppFetch(TOC, { isAdmin: false });
  render(
    <MemoryRouter initialEntries={["/shop/search"]}>
      <AppLayout app="shop"><div /></AppLayout>
    </MemoryRouter>,
  );
  await waitFor(() =>
    expect(sidebarNav().getByRole("link", { name: "Search" })).toBeInTheDocument());
  expect(screen.queryByRole("link", { name: /admin/i })).toBeNull();
});
