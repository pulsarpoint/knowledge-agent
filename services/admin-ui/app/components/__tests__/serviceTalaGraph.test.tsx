import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import ServiceDependencyGraph from "../ServiceDependencyGraph";
import ServiceTalaGraph from "../ServiceTalaGraph";
import type { ServiceAsset } from "../../lib/services";

const engine = vi.hoisted(() => ({ compile: vi.fn(), render: vi.fn(), dispose: vi.fn() }));
vi.mock("@d2lang/d2", () => ({ D2: vi.fn(function () { return engine; }) }));

beforeEach(() => {
  vi.clearAllMocks();
  engine.compile.mockResolvedValue({ diagram: {}, renderOptions: { pad: 24 } });
  engine.render.mockResolvedValue('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 600 400"><text>API</text></svg>');
  engine.dispose.mockResolvedValue(undefined);
});

test.each(["/environments?environment_id=dev&view=graph", "/environments?environment_id=dev&view=graph&layout=tala"])("TALA is selected at %s, keeps service details and allows switching layouts", async (url) => {
  const services = [
    { id: "api", name: "API", service_type: "custom", service_role: "application", dependencies: [{ service_id: "db", name: "DB", description: "Stores data" }] },
    { id: "db", name: "DB", service_type: "builtin", service_role: "database", dependencies: [] },
  ] as ServiceAsset[];
  const onSelect = vi.fn();
  render(<MemoryRouter initialEntries={[url]}><ServiceDependencyGraph services={services} onSelect={onSelect} /></MemoryRouter>);
  expect(screen.getByRole("tab", { name: "TALA" })).toHaveAttribute("aria-selected", "true");
  const image = await screen.findByRole("img", { name: /Service dependencies laid out by TALA/ });
  expect(image.getAttribute("src")).toMatch(/^data:image\/svg\+xml/);
  expect(engine.compile).toHaveBeenCalledWith(expect.stringContaining("service0 -> service1"), { layout: "tala", pad: 24 });
  expect(engine.dispose).toHaveBeenCalled();
  expect(screen.getByRole("button", { name: "Zoom In" })).toBeInTheDocument();
  expect(within(screen.getByRole("region", { name: "Relationship details" })).getByText("Stores data")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "DB" }));
  expect(onSelect).toHaveBeenCalledWith("db");
  await userEvent.click(screen.getByRole("tab", { name: "Interactive (Dagre)" }));
  expect(screen.getByRole("button", { name: "Auto layout" })).toBeInTheDocument();
  expect(screen.queryByRole("img")).toBeNull();
  await userEvent.click(screen.getByRole("tab", { name: "TALA" }));
  expect(await screen.findByRole("img", { name: /Service dependencies laid out by TALA/ })).toBeInTheDocument();
});

test("render errors provide a working retry", async () => {
  engine.compile.mockRejectedValueOnce(new Error("Layout failed"));
  render(<ServiceTalaGraph source="api -> db" />);
  expect(await screen.findByRole("alert")).toHaveTextContent("Could not draw the diagram");
  fireEvent.click(screen.getByRole("button", { name: "Retry layout" }));
  expect(await screen.findByRole("img")).toBeInTheDocument();
  expect(engine.compile).toHaveBeenCalledTimes(2);
});

test("changing inventory cancels pending work and ignores a stale render", async () => {
  let finish!: (value: string) => void;
  engine.render.mockReturnValueOnce(new Promise<string>(resolve => { finish = resolve; }));
  const { rerender, unmount } = render(<ServiceTalaGraph source="old" />);
  await waitFor(() => expect(engine.render).toHaveBeenCalledTimes(1));
  rerender(<ServiceTalaGraph source="new" />);
  const image = await screen.findByRole("img");
  const currentUrl = image.getAttribute("src");
  await act(async () => { finish('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><text>Stale</text></svg>'); });
  expect(screen.getByRole("img").getAttribute("src")).toBe(currentUrl);
  expect(engine.compile.mock.calls.map(([source]) => source)).toEqual(["old", "new"]);
  unmount();
  expect(engine.dispose).toHaveBeenCalled();
});

test("a stalled layout times out and releases its worker", async () => {
  vi.useFakeTimers();
  engine.compile.mockReturnValueOnce(new Promise(() => {}));
  try {
    const { unmount } = render(<ServiceTalaGraph source="api -> db" />);
    await act(async () => { await vi.advanceTimersByTimeAsync(60_001); });
    expect(screen.getByRole("alert")).toHaveTextContent("The diagram layout took too long");
    expect(engine.dispose).toHaveBeenCalled();
    unmount();
  } finally { vi.useRealTimers(); }
});
