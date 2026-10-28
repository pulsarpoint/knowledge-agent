import { rowAction, rowButtons } from "~/lib/__tests__/rowMenu";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { renderRoutes } from "~/lib/__tests__/dataRouter";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import OutputSchemasView from "~/features/outputSchemas/OutputSchemasView";
import OutputSchemaEditorView from "~/features/outputSchemas/OutputSchemaEditorView";
import OutputSchemaPicker from "~/features/outputSchemas/OutputSchemaPicker";
import RunOutputPanel from "~/features/outputSchemas/RunOutputPanel";
import { Toaster } from "~/components/ui/sonner";
import { expectOneToolbarRow } from "~/lib/__tests__/toolbarRow";

const SHAPE = {
  type: "object",
  properties: { verdict: { type: "string" } },
  required: ["verdict"],
  additionalProperties: false,
};
const summary = (extra = {}) => ({
  id: "s1",
  name: "Verdict",
  description: "A judge's answer",
  current_version: 2,
  revision: 3,
  created_at: "2026-10-24T10:00:00Z",
  updated_at: "2026-10-24T11:00:00Z",
  used_by: ["Triage tickets"],
  ...extra,
});
const detail = (extra = {}) => ({
  ...summary(),
  schema: SHAPE,
  versions: [
    { version: 2, created_at: "2026-10-24T11:00:00Z", created_by_email: "ana@example.com" },
    { version: 1, created_at: "2026-10-24T10:00:00Z", created_by_email: null },
  ],
  ...extra,
});

function api(handler: (url: string, init?: RequestInit) => unknown) {
  const fetch = vi.fn(async (url: string, init?: RequestInit) => {
    const body = handler(url, init);
    if (body instanceof Response) return body;
    if (body === undefined) throw new Error(`unexpected ${init?.method ?? "GET"} ${url}`);
    return new Response(JSON.stringify(body));
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}
const refused = (message: string, status = 409) => new Response(JSON.stringify({ error: { message } }), { status });
const bodyOf = (fetch: ReturnType<typeof api>, method: string, url?: string) =>
  JSON.parse(fetch.mock.calls.find(([u, init]) => init?.method === method && (!url || u === url))![1]!.body as string);

function editor(url: string) {
  render(<Toaster />);
  return renderRoutes(
    [
      { path: "/output-schemas", element: <p>Schema list</p> },
      { path: "/output-schemas/new", element: <OutputSchemaEditorView /> },
      { path: "/output-schemas/:schemaId", element: <OutputSchemaEditorView /> },
    ],
    url,
  );
}

afterEach(() => vi.unstubAllGlobals());

test("the list shows each schema with its version and the handlers that use it", async () => {
  api((url) => (url === "/api/output-schemas" ? { schemas: [summary()] } : undefined));
  render(
    <MemoryRouter>
      <OutputSchemasView />
    </MemoryRouter>,
  );
  const row = (await screen.findByRole("link", { name: "Verdict" })).closest("tr")!;
  expect(screen.getByRole("link", { name: "Verdict" })).toHaveAttribute("href", "/output-schemas/s1");
  expect(within(row).getByText("A judge's answer")).toBeInTheDocument();
  expect(within(row).getByText("v2")).toBeInTheDocument();
  expect(within(row).getByText("Triage tickets")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "New schema" })).toHaveAttribute("href", "/output-schemas/new");
  expectOneToolbarRow(
    screen.getByRole("searchbox", { name: "Search output schemas" }),
    screen.getByRole("link", { name: "New schema" }),
  );
});

test("the schema list: Skeleton rows while loading, then search and sort by a header", async () => {
  const user = userEvent.setup();
  api((url) =>
    url === "/api/output-schemas"
      ? { schemas: [summary(), summary({ id: "s2", name: "Alert", description: "Pager", current_version: 5 })] }
      : undefined,
  );
  render(
    <MemoryRouter>
      <OutputSchemasView />
    </MemoryRouter>,
  );
  expect(screen.getAllByTestId("skeleton-row").length).toBeGreaterThan(0);
  await screen.findByRole("link", { name: "Verdict" });
  const names = () => screen.getAllByRole("link", { name: /^(Alert|Verdict)$/ }).map((link) => link.textContent);
  expect(names()).toEqual(["Alert", "Verdict"]);
  await user.click(screen.getByRole("button", { name: "Version" }));
  expect(names()).toEqual(["Verdict", "Alert"]);
  await user.type(screen.getByRole("searchbox", { name: "Search output schemas" }), "pager");
  expect(names()).toEqual(["Alert"]);
});

test("an empty library says how schemas are used", async () => {
  api((url) => (url === "/api/output-schemas" ? { schemas: [] } : undefined));
  render(
    <MemoryRouter>
      <OutputSchemasView />
    </MemoryRouter>,
  );
  expect(await screen.findByText("No output schemas yet")).toBeInTheDocument();
  expect(screen.getAllByRole("link", { name: "New schema" })).toHaveLength(2);
});

test("Check shows the problems with their paths, or that the schema is accepted; bad JSON never reaches the API", async () => {
  const fetch = api((url, init) => {
    if (url === "/api/output-schemas/check") {
      const body = JSON.parse(init!.body as string);
      return { problems: body.schema.properties ? [] : ["$: 'properties' is required"], sample_errors: null };
    }
  });
  editor("/output-schemas/new");
  const schema = await screen.findByLabelText("Schema (JSON)");
  fireEvent.change(schema, { target: { value: "{ not json" } });
  fireEvent.click(screen.getByRole("button", { name: "Check" }));
  expect(await screen.findByText(/Not valid JSON/)).toBeInTheDocument();
  expect(fetch).not.toHaveBeenCalled();
  fireEvent.change(schema, { target: { value: '{"type": "object"}' } });
  fireEvent.click(screen.getByRole("button", { name: "Check" }));
  expect(await screen.findByText("$: 'properties' is required")).toBeInTheDocument();
  fireEvent.change(schema, { target: { value: JSON.stringify(SHAPE) } });
  fireEvent.click(screen.getByRole("button", { name: "Check" }));
  expect(await screen.findByText("The schema is accepted.")).toBeInTheDocument();
});

test("Validate sample sends the schema and the sample and shows whether it matches", async () => {
  const fetch = api((url, init) => {
    if (url === "/api/output-schemas/check") {
      const { sample } = JSON.parse(init!.body as string);
      return { problems: [], sample_errors: sample.verdict ? [] : ["$: 'verdict' is a required property"] };
    }
  });
  editor("/output-schemas/new");
  fireEvent.change(await screen.findByLabelText("Schema (JSON)"), { target: { value: JSON.stringify(SHAPE) } });
  const sample = screen.getByLabelText("Sample (JSON)");
  fireEvent.change(sample, { target: { value: "{}" } });
  fireEvent.click(screen.getByRole("button", { name: "Validate sample" }));
  expect(await screen.findByText("$: 'verdict' is a required property")).toBeInTheDocument();
  expect(bodyOf(fetch, "POST")).toEqual({ schema: SHAPE, sample: {} });
  fireEvent.change(sample, { target: { value: '{"verdict": "pass"}' } });
  fireEvent.click(screen.getByRole("button", { name: "Validate sample" }));
  expect(await screen.findByText("The sample matches the schema.")).toBeInTheDocument();
});

test("a new schema is created and opened on its own page", async () => {
  const fetch = api((url, init) => {
    if (url === "/api/output-schemas" && init?.method === "POST") return { schema: detail({ id: "s9" }) };
    if (url === "/api/output-schemas/s9") return { schema: detail({ id: "s9" }) };
  });
  editor("/output-schemas/new");
  fireEvent.change(await screen.findByLabelText("Name"), { target: { value: "Verdict" } });
  fireEvent.change(screen.getByLabelText("Description"), { target: { value: "A judge's answer" } });
  fireEvent.change(screen.getByLabelText("Schema (JSON)"), { target: { value: JSON.stringify(SHAPE) } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(fetch).toHaveBeenCalledWith("/api/output-schemas/s9", expect.anything()));
  expect(bodyOf(fetch, "POST", "/api/output-schemas")).toEqual({
    name: "Verdict",
    description: "A judge's answer",
    schema: SHAPE,
  });
  expect(await screen.findByRole("button", { name: "Versions (2)" })).toBeInTheDocument();
  expect(await screen.findByText("Output schema “Verdict” created.")).toBeInTheDocument();
});

test("editing saves with the revision and shows the API's refusal", async () => {
  let saves = 0;
  const fetch = api((url, init) => {
    if (url === "/api/output-schemas/s1" && init?.method === "PUT")
      return saves++ === 0
        ? refused("Not an output schema: $.properties.tags: 'minItems' is not supported", 400)
        : { schema: detail({ revision: 4 }) };
    if (url === "/api/output-schemas/s1") return { schema: detail() };
  });
  editor("/output-schemas/s1");
  expect(await screen.findByDisplayValue("Verdict")).toBeInTheDocument();
  expect(screen.getByText(/Used by: Triage tickets/)).toBeInTheDocument();
  expect(screen.getByLabelText("Schema (JSON)")).toHaveValue(JSON.stringify(SHAPE, null, 2));
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  expect(await screen.findByText(/'minItems' is not supported/)).toBeInTheDocument();
  expect(bodyOf(fetch, "PUT")).toMatchObject({ name: "Verdict", revision: 3, schema: SHAPE });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(screen.queryByText(/'minItems' is not supported/)).toBeNull());
  expect(await screen.findByText("Output schema “Verdict” saved.")).toBeInTheDocument();
});

test("Versions opens a side sheet; a version's JSON can be viewed and copied into the editor", async () => {
  const old = { type: "object", properties: {}, required: [], additionalProperties: false };
  api((url) => {
    if (url === "/api/output-schemas/s1/versions/1")
      return { version: { version: 1, schema: old, created_at: "2026-10-24T10:00:00Z" } };
    if (url === "/api/output-schemas/s1") return { schema: detail() };
  });
  editor("/output-schemas/s1");
  fireEvent.click(await screen.findByRole("button", { name: "Versions (2)" }));
  const sheet = await screen.findByRole("dialog", { name: "Versions of Verdict" });
  expect(within(sheet).getByText(/ana@example.com/)).toBeInTheDocument();
  expect(within(sheet).getByText("Current")).toBeInTheDocument();
  fireEvent.click(within(sheet).getByRole("button", { name: "View version 1" }));
  expect(await within(sheet).findByLabelText("Version 1 JSON")).toHaveTextContent('"properties": {}');
  fireEvent.click(within(sheet).getByRole("button", { name: "Copy to editor" }));
  await waitFor(() => expect(screen.getByLabelText("Schema (JSON)")).toHaveValue(JSON.stringify(old, null, 2)));
});

test("a version that cannot be read shows its error inside the Versions sheet", async () => {
  api((url) => {
    if (url === "/api/output-schemas/s1/versions/1") return refused("Output schema version not found.", 404);
    if (url === "/api/output-schemas/s1") return { schema: detail() };
  });
  editor("/output-schemas/s1");
  fireEvent.click(await screen.findByRole("button", { name: "Versions (2)" }));
  const sheet = await screen.findByRole("dialog", { name: "Versions of Verdict" });
  fireEvent.click(within(sheet).getByRole("button", { name: "View version 1" }));
  expect(await within(sheet).findByText("Output schema version not found.")).toBeInTheDocument();
});

test("Delete asks first from the header's ⋯ menu; a schema a handler uses is refused with the handler names", async () => {
  let deletes = 0;
  const fetch = api((url, init) => {
    if (url === "/api/output-schemas/s1?revision=3" && init?.method === "DELETE")
      return deletes++ === 0 ? refused("Used by: Triage tickets.") : new Response(null, { status: 204 });
    if (url === "/api/output-schemas/s1") return { schema: detail() };
  });
  editor("/output-schemas/s1");
  // Save is in the header; Delete is in its ⋯ menu, never a footer button.
  const header = (await screen.findByRole("heading", { name: "Verdict" })).parentElement!.parentElement!;
  expect(rowButtons(header)).toEqual(["Versions (2)", "Save", "Actions for Verdict"]);
  await rowAction(userEvent, "Verdict", "Delete");
  const dialog = await screen.findByRole("alertdialog");
  expect(within(dialog).getByText(/Runs that used it keep their output/)).toBeInTheDocument();
  expect(within(dialog).getByText("Delete “Verdict”?")).toBeInTheDocument();
  fireEvent.click(within(dialog).getByRole("button", { name: "Delete output schema" }));
  // The refusal is shown in the dialog, which stays open.
  expect(await within(dialog).findByText("Used by: Triage tickets.")).toBeInTheDocument();
  fireEvent.click(within(dialog).getByRole("button", { name: "Delete output schema" }));
  expect(await screen.findByText("Schema list")).toBeInTheDocument();
  expect(await screen.findByText("Output schema “Verdict” deleted.")).toBeInTheDocument();
  expect(fetch.mock.calls.filter(([, init]) => init?.method === "DELETE")).toHaveLength(2);
});

test("leaving the editor with unsaved changes asks Stay / Leave; unchanged it leaves at once", async () => {
  api((url) => (url === "/api/output-schemas/s1" ? { schema: detail() } : undefined));
  const { router } = editor("/output-schemas/s1");
  await userEvent.click(await screen.findByRole("link", { name: "All output schemas" }));
  expect(await screen.findByText("Schema list")).toBeInTheDocument();
  expect(screen.queryByRole("alertdialog")).toBeNull();
  await router.navigate("/output-schemas/s1");
  await userEvent.type(await screen.findByLabelText("Description"), " more");
  await userEvent.click(screen.getByRole("link", { name: "All output schemas" }));
  const dialog = within(await screen.findByRole("alertdialog"));
  await userEvent.click(dialog.getByRole("button", { name: "Stay" }));
  expect(router.state.location.pathname).toBe("/output-schemas/s1");
  await userEvent.click(screen.getByRole("link", { name: "All output schemas" }));
  await userEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Leave" }));
  expect(await screen.findByText("Schema list")).toBeInTheDocument();
});

test("the picker offers none or a schema, then the current version or a fixed one", async () => {
  const user = userEvent.setup();
  api((url) => (url === "/api/output-schemas" ? { schemas: [summary()] } : undefined));
  const changed = vi.fn();
  const { rerender } = render(<OutputSchemaPicker idPrefix="t" value={null} onChange={changed} />);
  const schema = screen.getByRole("combobox", { name: "Output schema" });
  expect(schema).toHaveTextContent("None");
  expect(screen.queryByRole("combobox", { name: "Schema version" })).toBeNull();
  await waitFor(() => expect(fetch).toHaveBeenCalled());
  await user.click(schema);
  await user.click(await screen.findByRole("option", { name: "Verdict" }));
  expect(changed).toHaveBeenLastCalledWith({ id: "s1", name: "Verdict", version: null });
  rerender(<OutputSchemaPicker idPrefix="t" value={{ id: "s1", name: "Verdict", version: null }} onChange={changed} />);
  const version = screen.getByRole("combobox", { name: "Schema version" });
  expect(version).toHaveTextContent("Current (v2)");
  await user.click(version);
  expect(screen.getAllByRole("option").map((item) => item.textContent)).toEqual(["Current (v2)", "v2", "v1"]);
  await user.click(screen.getByRole("option", { name: "v1" }));
  expect(changed).toHaveBeenLastCalledWith({ id: "s1", name: "Verdict", version: 1 });
});

test("the Output panel shows the JSON with a copy button", async () => {
  const writeText = vi.fn(async () => {});
  Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
  api((url) =>
    url === "/api/sessions/r1/output"
      ? { schema: { id: "s1", name: "Verdict", version: 2 }, output: { verdict: "pass" }, valid: true }
      : undefined,
  );
  render(<RunOutputPanel sessionId="r1" status="waiting" turns={1} />);
  const panel = await screen.findByRole("region", { name: "Output" });
  expect(within(panel).getByText("Verdict · v2")).toBeInTheDocument();
  expect(await within(panel).findByLabelText("Output JSON")).toHaveTextContent('"verdict": "pass"');
  fireEvent.click(within(panel).getByRole("button", { name: "Copy" }));
  await waitFor(() => expect(writeText).toHaveBeenCalledWith(JSON.stringify({ verdict: "pass" }, null, 2)));
  expect(await within(panel).findByRole("button", { name: "Copied" })).toBeInTheDocument();
});

test("the Output panel shows the errors and the raw answer of a run that did not match", async () => {
  api((url) =>
    url === "/api/sessions/r1/output"
      ? {
          schema: { id: "s1", name: "Verdict", version: 2 },
          output: null,
          valid: false,
          errors: ["$: 'verdict' is a required property"],
          raw: "I think it passes.",
        }
      : undefined,
  );
  render(<RunOutputPanel sessionId="r1" status="failed" turns={1} />);
  const panel = await screen.findByRole("region", { name: "Output" });
  expect(await within(panel).findByText("Output did not match the schema")).toBeInTheDocument();
  expect(within(panel).getByText("$: 'verdict' is a required property")).toBeInTheDocument();
  expect(within(panel).getByLabelText("Raw answer")).toHaveTextContent("I think it passes.");
});

test("the Output panel waits while the run works, without asking the API", () => {
  const fetch = api(() => undefined);
  render(<RunOutputPanel sessionId="r1" status="working" turns={1} />);
  expect(screen.getByText("The output appears when the run finishes.")).toBeInTheDocument();
  expect(fetch).not.toHaveBeenCalled();
});
