import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, expect, test, vi } from "vitest";
import SearchView, { renderSnippet } from "../SearchView";

afterEach(() => vi.unstubAllGlobals());

test("types a query, renders hits with links", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify([{
    path: "how-to/refunds.md", title: "Refunds", heading_path: "Refunds > Requesting",
    snippet: "are <mark>asynchronous</mark> via", anchor: "requesting",
  }]))));
  render(<MemoryRouter><SearchView app="shop" /></MemoryRouter>);
  await userEvent.type(screen.getByRole("searchbox"), "asynchronous");
  await waitFor(() => expect(screen.getByText("Refunds")).toBeInTheDocument());
  expect(screen.getByRole("link", { name: /Refunds/ }))
    .toHaveAttribute("href", "/shop/doc/how-to/refunds.md#requesting");
  expect(screen.getByText("asynchronous").tagName).toBe("MARK");
});

test("snippet renderer escapes everything except <mark>", () => {
  const { container } = render(<span>{renderSnippet('x <script>bad()</script> <mark>hit</mark> <b>no</b>')}</span>);
  expect(container.querySelector("script")).toBeNull();
  expect(container.querySelector("b")).toBeNull();
  expect(container.querySelector("mark")).toHaveTextContent("hit");
  expect(container.textContent).toContain("<b>no</b>");
});
