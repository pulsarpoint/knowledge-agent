import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import ServiceTalaGraph from "~/features/servers/ServiceTalaGraph";

const engine = vi.hoisted(() => ({ compile: vi.fn(), render: vi.fn(), dispose: vi.fn() }));
vi.mock("@d2lang/d2", () => ({
  D2: vi.fn(function () {
    return engine;
  }),
}));

beforeEach(() => {
  vi.clearAllMocks();
  engine.compile.mockResolvedValue({ diagram: {}, renderOptions: { pad: 24 } });
  engine.render.mockResolvedValue(
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 600 400"><text>API</text></svg>',
  );
  engine.dispose.mockResolvedValue(undefined);
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
  engine.render.mockReturnValueOnce(
    new Promise<string>((resolve) => {
      finish = resolve;
    }),
  );
  const { rerender, unmount } = render(<ServiceTalaGraph source="old" />);
  await waitFor(() => expect(engine.render).toHaveBeenCalledTimes(1));
  rerender(<ServiceTalaGraph source="new" />);
  const image = await screen.findByRole("img");
  const currentUrl = image.getAttribute("src");
  await act(async () => {
    finish('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><text>Stale</text></svg>');
  });
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
    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_001);
    });
    expect(screen.getByRole("alert")).toHaveTextContent("The diagram layout took too long");
    expect(engine.dispose).toHaveBeenCalled();
    unmount();
  } finally {
    vi.useRealTimers();
  }
});
