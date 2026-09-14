import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, expect, test, vi } from "vitest";
import Home from "../../routes/home";

afterEach(() => vi.unstubAllGlobals());

test("multiple apps -> switcher list", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify([
    { name: "shop", ask_enabled: false, ask_reason: "Ask is not configured" },
    { name: "billing", ask_enabled: true, ask_reason: null },
  ]))));
  render(<MemoryRouter><Home /></MemoryRouter>);
  await waitFor(() => expect(screen.getByRole("link", { name: /shop/ })).toHaveAttribute("href", "/shop"));
  expect(screen.getByRole("link", { name: /billing/ })).toBeInTheDocument();
});

test("single app redirects to it", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify([
    { name: "shop", ask_enabled: false, ask_reason: null },
  ]))));
  render(
    <MemoryRouter initialEntries={["/"]}>
      <Routes>
        <Route path="/" element={<Home />} />
        <Route path="/shop" element={<div>SHOP HOME</div>} />
      </Routes>
    </MemoryRouter>,
  );
  await waitFor(() => expect(screen.getByText("SHOP HOME")).toBeInTheDocument());
});

test("api failure shows an error message instead of a blank list", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(
    JSON.stringify({ error: { message: "database is on fire" } }),
    { status: 500 },
  )));
  render(<MemoryRouter><Home /></MemoryRouter>);
  await waitFor(() => expect(screen.getByText("database is on fire")).toBeInTheDocument());
  expect(screen.queryByRole("link")).not.toBeInTheDocument();
});

test("empty app list shows a no-apps message", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify([]))));
  render(<MemoryRouter><Home /></MemoryRouter>);
  await waitFor(() =>
    expect(screen.getByText("No applications configured.")).toBeInTheDocument());
});
