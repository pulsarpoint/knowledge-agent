import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { renderRoutes } from "~/lib/__tests__/dataRouter";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";
import LlmSettingsView from "~/features/llm/LlmSettingsView";
import LlmModelForm from "~/features/llm/LlmModelForm";
import type { ModelProfile } from "~/features/llm/api";
import { Toaster } from "~/components/ui/sonner";
import { menuItems, rowAction, rowButtons } from "~/lib/__tests__/rowMenu";

afterEach(() => vi.unstubAllGlobals());
const bedrock: ModelProfile = {
  id: "bedrock-1",
  name: "Bedrock Claude",
  provider: "bedrock",
  model: "eu.anthropic.claude-test-v1:0",
  api_format: "native",
  base_url: null,
  aws_region: "eu-central-1",
  enabled: true,
  has_api_key: true,
  revision: 1,
  created_at: "",
  updated_at: "",
};

/** A model form alone, in a data router (its leave guard needs one). */
const form = (element: React.ReactElement) => renderRoutes([{ path: "/", element }], "/");
/** The model settings page in a data router, with the Agents page to leave to. */
const page = () => {
  render(<Toaster />);
  return renderRoutes(
    [
      { path: "/llm-settings", element: <LlmSettingsView /> },
      { path: "/agents", element: <p>Agents list</p> },
    ],
    "/llm-settings",
  ).router;
};

test("leaving with unsaved changes in the model sheet asks Stay / Leave; an untouched sheet does not", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(JSON.stringify({ models: [bedrock], assignments: { revision: 1 } }))),
  );
  const router = page();
  await waitFor(() => expect(screen.getByRole("button", { name: "New model" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "New model" }));
  await act(() => router.navigate("/agents"));
  expect(await screen.findByText("Agents list")).toBeInTheDocument();
  expect(screen.queryByRole("alertdialog")).toBeNull();
  await act(() => router.navigate("/llm-settings"));
  await waitFor(() => expect(screen.getByRole("button", { name: "New model" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "New model" }));
  fireEvent.change(screen.getByLabelText("Connection name"), { target: { value: "Search" } });
  await act(() => router.navigate("/agents"));
  const dialog = within(await screen.findByRole("alertdialog"));
  fireEvent.click(dialog.getByRole("button", { name: "Stay" }));
  await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
  expect(router.state.location.pathname).toBe("/llm-settings");
  await act(() => router.navigate("/agents"));
  fireEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Leave" }));
  expect(await screen.findByText("Agents list")).toBeInTheDocument();
});

test("New model is a right sheet with Create model and Cancel; Cancel discards, Escape with input asks Stay / Leave", async () => {
  const user = userEvent.setup();
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(JSON.stringify({ models: [bedrock], assignments: { revision: 1 } }))),
  );
  page();
  await waitFor(() => expect(screen.getByRole("button", { name: "New model" })).toBeEnabled());
  await user.click(screen.getByRole("button", { name: "New model" }));
  const sheet = within(screen.getByRole("dialog", { name: "New model" }));
  expect(sheet.getByRole("button", { name: "Create model" })).toBeInTheDocument();
  await user.click(sheet.getByRole("button", { name: "Cancel" }));
  expect(screen.queryByRole("dialog", { name: "New model" })).toBeNull();
  await user.click(screen.getByRole("button", { name: "New model" }));
  await user.type(screen.getByLabelText("Connection name"), "Search");
  await user.click(screen.getByRole("button", { name: "Cancel" }));
  expect(screen.queryByRole("alertdialog")).toBeNull();
  expect(screen.queryByRole("dialog", { name: "New model" })).toBeNull();
  await user.click(screen.getByRole("button", { name: "New model" }));
  await user.type(screen.getByLabelText("Connection name"), "Search");
  await user.keyboard("{Escape}");
  await user.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Leave" }));
  await waitFor(() => expect(screen.queryByRole("dialog", { name: "New model" })).toBeNull());
});

test("the model list: Skeleton rows while loading, an empty state with New model, search and sort", async () => {
  const user = userEvent.setup();
  let answer: (value: Response) => void = () => {};
  vi.stubGlobal(
    "fetch",
    vi.fn(() => new Promise<Response>((done) => (answer = done))),
  );
  page();
  expect(await screen.findAllByTestId("skeleton-row")).not.toHaveLength(0);
  await act(async () => answer(new Response(JSON.stringify({ models: [], assignments: { revision: 1 } }))));
  expect(await screen.findByText("No models yet")).toBeInTheDocument();
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async () =>
        new Response(
          JSON.stringify({
            models: [bedrock, { ...bedrock, id: "a", name: "Atlas", enabled: false, model: "atlas-1" }],
            assignments: { revision: 1 },
          }),
        ),
    ),
  );
  cleanup();
  page();
  await screen.findByRole("cell", { name: "Atlas" });
  const names = () =>
    screen
      .getAllByRole("row")
      .slice(1)
      .map((row) => row.querySelector("td")?.textContent);
  expect(names()).toEqual(["Atlas", "Bedrock Claude"]);
  await user.click(screen.getByRole("button", { name: "Status" }));
  expect(names()).toEqual(["Bedrock Claude", "Atlas"]);
  await user.type(screen.getByRole("searchbox", { name: "Search models" }), "atlas");
  expect(names()).toEqual(["Atlas"]);
});

test("editing a Bedrock model preserves its saved key and supports disabling", async () => {
  const saved = vi.fn();
  const fetch = vi.fn(
    async (_url: string, init?: RequestInit) =>
      new Response(JSON.stringify({ ...bedrock, ...JSON.parse(init!.body as string), revision: 2 })),
  );
  vi.stubGlobal("fetch", fetch);
  form(<LlmModelForm model={bedrock} onSaved={saved} onClose={() => {}} />);
  expect(screen.getByLabelText("AWS region")).toHaveValue("eu-central-1");
  expect(screen.getByLabelText("Bedrock API key")).toHaveValue("");
  expect(screen.queryByLabelText("Base URL")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("switch", { name: "Enabled" }));
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(saved).toHaveBeenCalled());
  const body = JSON.parse(fetch.mock.calls[0][1]!.body as string);
  expect(body).toMatchObject({
    provider: "bedrock",
    aws_region: "eu-central-1",
    base_url: null,
    enabled: false,
    revision: 1,
  });
  expect(body).not.toHaveProperty("api_key");
});

test("a stale model edit keeps the new key and other unsaved values", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async () =>
        new Response(
          JSON.stringify({ error: { message: "This model changed. Cancel and reopen it before saving." } }),
          { status: 409 },
        ),
    ),
  );
  form(<LlmModelForm model={bedrock} onSaved={() => {}} onClose={() => {}} />);
  fireEvent.change(screen.getByLabelText("Bedrock API key"), { target: { value: "replacement-key" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await screen.findByRole("alert");
  expect(screen.getByLabelText("Bedrock API key")).toHaveValue("replacement-key");
});

test("connection test is explicit and uses the saved revision", async () => {
  const fetch = vi.fn(
    async (_url: string, init?: RequestInit) =>
      new Response(
        JSON.stringify(
          init?.method
            ? { ok: true, message: "Model responded successfully." }
            : { models: [bedrock], assignments: { search_model_id: null, code_model_id: null, revision: 1 } },
        ),
      ),
  );
  vi.stubGlobal("fetch", fetch);
  page();
  const button = await screen.findByRole("button", { name: "Test connection for Bedrock Claude" });
  expect(fetch.mock.calls.filter(([, init]) => init?.method)).toHaveLength(0);
  fireEvent.click(button);
  await screen.findByText("Bedrock Claude: Model responded successfully.");
  const call = fetch.mock.calls.find(([, init]) => init?.method)!;
  expect(call[0]).toBe("/api/llm/models/bedrock-1/test");
  expect(JSON.parse(call[1]!.body as string)).toEqual({ revision: 1 });
});

test("a model row keeps Test inline and Edit in its menu; Edit opens the model's form", async () => {
  const user = userEvent.setup();
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(JSON.stringify({ models: [bedrock], assignments: { revision: 1 } }))),
  );
  page();
  const row = (await screen.findByRole("cell", { name: "Bedrock Claude" })).closest("tr")!;
  expect(rowButtons(row)).toEqual(["Test connection for Bedrock Claude", "Actions for Bedrock Claude"]);
  await user.click(within(row).getByRole("button", { name: "Actions for Bedrock Claude" }));
  expect(menuItems()).toEqual(["Edit"]);
  await user.keyboard("{Escape}");
  await rowAction(user, "Bedrock Claude", "Edit");
  expect(await screen.findByLabelText("Connection name")).toHaveValue("Bedrock Claude");
});

test("model settings manages connections and links to Agents for model selection", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(JSON.stringify({ models: [bedrock], assignments: { revision: 1 } }))),
  );
  page();
  expect(await screen.findByRole("cell", { name: "Bedrock Claude" })).toBeVisible();
  expect(screen.getByRole("link", { name: "Agents page" })).toHaveAttribute("href", "/agents");
  expect(screen.queryByText("Agent models")).not.toBeInTheDocument();
  expect(screen.queryByRole("combobox", { name: /model/i })).not.toBeInTheDocument();
});

test("creating an embedding connection includes its dimensions and version", async () => {
  const user = userEvent.setup();
  const saved = vi.fn();
  const fetch = vi.fn(
    async (_url: string, init?: RequestInit) =>
      new Response(JSON.stringify({ ...JSON.parse(init!.body as string), id: "embed", revision: 1 })),
  );
  vi.stubGlobal("fetch", fetch);
  form(<LlmModelForm model={null} onSaved={saved} onClose={() => {}} />);
  await user.click(screen.getByRole("combobox", { name: "Model type" }));
  await user.click(screen.getByRole("option", { name: "Embeddings" }));
  expect(screen.getByRole("combobox", { name: "Provider" })).toBeEnabled();
  expect(screen.queryByLabelText("API format")).not.toBeInTheDocument();
  await user.type(screen.getByLabelText("Connection name"), "Document embeddings");
  await user.type(screen.getByLabelText("Model ID"), "text-embedding-test");
  fireEvent.change(screen.getByLabelText("Embedding dimensions"), { target: { value: "1536" } });
  await user.click(screen.getByRole("button", { name: "Create model" }));
  await waitFor(() => expect(saved).toHaveBeenCalled());
  expect(JSON.parse(fetch.mock.calls[0][1]!.body as string)).toMatchObject({
    api_format: "embeddings",
    provider: "openai",
    embedding_dimensions: 1536,
    embedding_version: "1",
  });
});

test("Bedrock embedding form keeps embedding type and saves its region", async () => {
  const user = userEvent.setup();
  const saved = vi.fn();
  const fetch = vi.fn(
    async (_url: string, init?: RequestInit) =>
      new Response(JSON.stringify({ ...JSON.parse(init!.body as string), id: "embed", revision: 1 })),
  );
  vi.stubGlobal("fetch", fetch);
  form(<LlmModelForm model={null} onSaved={saved} onClose={() => {}} />);
  await user.click(screen.getByRole("combobox", { name: "Model type" }));
  await user.click(screen.getByRole("option", { name: "Embeddings" }));
  await user.click(screen.getByRole("combobox", { name: "Provider" }));
  expect(screen.queryByRole("option", { name: "Anthropic" })).not.toBeInTheDocument();
  await user.click(screen.getByRole("option", { name: "AWS Bedrock" }));
  expect(screen.getByRole("combobox", { name: "Model type" })).toHaveTextContent("Embeddings");
  expect(screen.queryByLabelText("Base URL")).not.toBeInTheDocument();
  await user.type(screen.getByLabelText("Connection name"), "Titan embeddings");
  await user.type(screen.getByLabelText("Model ID"), "amazon.titan-embed-text-v2:0");
  await user.type(screen.getByLabelText("AWS region"), "eu-central-1");
  await user.type(screen.getByLabelText("Bedrock API key"), "test-key");
  await user.click(screen.getByRole("button", { name: "Create model" }));
  await waitFor(() => expect(saved).toHaveBeenCalled());
  expect(JSON.parse(fetch.mock.calls[0][1]!.body as string)).toMatchObject({
    api_format: "embeddings",
    provider: "bedrock",
    base_url: null,
    aws_region: "eu-central-1",
    embedding_dimensions: 1024,
    api_key: "test-key",
  });
});

test("a chat model saves Supports structured output (on by default for OpenAI, kept when editing)", async () => {
  const saved = vi.fn();
  const fetch = vi.fn(
    async (_url: string, init?: RequestInit) =>
      new Response(JSON.stringify({ ...bedrock, ...JSON.parse(init!.body as string), revision: 2 })),
  );
  vi.stubGlobal("fetch", fetch);
  const { unmount } = form(
    <LlmModelForm
      model={{ ...bedrock, capabilities: { supports_structured_output: true } }}
      onSaved={saved}
      onClose={() => {}}
    />,
  );
  const toggle = screen.getByRole("switch", { name: "Supports structured output" });
  expect(toggle).toBeChecked();
  fireEvent.click(toggle);
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(saved).toHaveBeenCalled());
  expect(JSON.parse(fetch.mock.calls[0][1]!.body as string).capabilities).toEqual({
    supports_structured_output: false,
  });
  unmount();
  form(<LlmModelForm model={null} onSaved={() => {}} onClose={() => {}} />);
  expect(screen.getByRole("switch", { name: "Supports structured output" })).toBeChecked();
});
