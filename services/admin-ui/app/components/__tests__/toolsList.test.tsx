import { fireEvent, render, screen } from "@testing-library/react";
import ToolsList from "~/features/agents/ToolsList";

afterEach(() => vi.unstubAllGlobals());

const TOOLS = [
  { name: "search_documents", group: "documents", description: "Search the documents." },
  { name: "get_document", group: "documents", description: "Read a document." },
  { name: "ssh_run", group: "servers", description: "Run a command over SSH." },
];

function stub(tools = TOOLS) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(JSON.stringify({ tools }), { status: 200 })),
  );
}

test("groups the catalog, counts the tools on, and switches one off", async () => {
  stub();
  const onChange = vi.fn();
  render(<ToolsList off={["ssh_run"]} onChange={onChange} />);
  expect(await screen.findByText("2 of 3 tools on")).toBeInTheDocument();
  expect(screen.getByText("Documents")).toBeInTheDocument();
  expect(screen.getByText("Run a command over SSH.")).toBeInTheDocument();
  expect(screen.getByRole("checkbox", { name: "ssh_run" })).not.toBeChecked();
  fireEvent.click(screen.getByRole("checkbox", { name: "get_document" }));
  expect(onChange).toHaveBeenCalledWith(["ssh_run", "get_document"]);
});

test("All and None work on one group", async () => {
  stub();
  const onChange = vi.fn();
  render(<ToolsList off={["get_document", "ssh_run"]} onChange={onChange} />);
  await screen.findByText("1 of 3 tools on");
  fireEvent.click(screen.getByRole("button", { name: "All Documents" }));
  expect(onChange).toHaveBeenLastCalledWith(["ssh_run"]);
  fireEvent.click(screen.getByRole("button", { name: "None Servers" }));
  expect(onChange).toHaveBeenLastCalledWith(["get_document", "ssh_run"]);
  fireEvent.click(screen.getByRole("button", { name: "None Documents" }));
  expect(onChange).toHaveBeenLastCalledWith(["get_document", "ssh_run", "search_documents"]);
});

test("locked tools show off and disabled, and All leaves them off", async () => {
  stub();
  const onChange = vi.fn();
  render(<ToolsList off={[]} locked={["ssh_run", "get_document"]} onChange={onChange} />);
  expect(await screen.findByText("1 of 3 tools on")).toBeInTheDocument();
  const locked = screen.getByRole("checkbox", { name: "ssh_run" });
  expect(locked).not.toBeChecked();
  expect(locked).toBeDisabled();
  expect(screen.getAllByText("Off for this agent")).toHaveLength(2);
  fireEvent.click(screen.getByRole("button", { name: "None Documents" }));
  expect(onChange).toHaveBeenLastCalledWith(["search_documents"]);
});

test("disabled: nothing can be changed", async () => {
  stub();
  render(<ToolsList off={[]} onChange={() => {}} disabled />);
  await screen.findByText("3 of 3 tools on");
  expect(screen.getByRole("checkbox", { name: "ssh_run" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "None Servers" })).toBeDisabled();
});

test("a failed load says so", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response("{}", { status: 500 })),
  );
  render(<ToolsList off={[]} onChange={() => {}} />);
  expect(await screen.findByRole("alert")).toBeInTheDocument();
});
