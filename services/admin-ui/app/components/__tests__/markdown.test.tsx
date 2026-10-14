import { render, screen, waitFor } from "@testing-library/react";
import { vi } from "vitest";
import mermaid from "mermaid";
import svgPanZoom from "svg-pan-zoom";
import Markdown from "../Markdown";
import Mermaid from "../Mermaid";

test("renders markdown structure", () => {
  render(<Markdown content={"# Title\n\nSome **bold** text."} />);
  expect(screen.getByRole("heading", { name: "Title" })).toBeInTheDocument();
  expect(screen.getByText("bold")).toBeInTheDocument();
});

test("raw HTML renders inert, never as elements", () => {
  const { container } = render(
    <Markdown content={'before <script>window.pwned=1</script> <img src=x onerror="window.pwned=2"> after'} />,
  );
  expect(container.querySelector("script")).toBeNull();
  expect(container.querySelector("img")).toBeNull();
  expect((window as unknown as { pwned?: number }).pwned).toBeUndefined();
});

test("gfm tables render", () => {
  render(<Markdown content={"|a|b|\n|-|-|\n|1|2|"} />);
  expect(screen.getByRole("table")).toBeInTheDocument();
});

test("mermaid initializes with strict securityLevel (model answers/docs can carry fences)", async () => {
  render(<Markdown content={"```mermaid\ngraph TD; A-->B;\n```"} />);
  await waitFor(() => {
    expect(vi.mocked(mermaid.initialize).mock.calls.length).toBeGreaterThan(0);
  });
  const calls = vi.mocked(mermaid.initialize).mock.calls;
  // securityLevel must never regress to 'loose': that permits scriptable content
  // inside the SVG mermaid produces, which we render via dangerouslySetInnerHTML.
  expect(calls.some(([config]) => (config as { securityLevel?: string }).securityLevel === "strict")).toBe(true);
});

test("heading ids dedupe repeats with -2/-3 suffixes, matching the API's slugger", () => {
  const content = "## Requesting\ntext\n\n## Example\ntext\n\n## Example\ntext\n";
  const { container } = render(<Markdown content={content} />);
  const ids = Array.from(container.querySelectorAll("h2")).map((h) => h.id);
  expect(ids).toEqual(["requesting", "example", "example-2"]);
});

test("heading id dedupe counter resets per render, not shared across renders", () => {
  const content = "## Example\ntext\n\n## Example\ntext\n";
  const first = render(<Markdown content={content} />);
  expect(Array.from(first.container.querySelectorAll("h2")).map((h) => h.id)).toEqual(["example", "example-2"]);

  const second = render(<Markdown content={content} />);
  // Same content, fresh render: must repeat example/example-2, never carry
  // over the previous render's counts into example-3/example-4.
  expect(Array.from(second.container.querySelectorAll("h2")).map((h) => h.id)).toEqual(["example", "example-2"]);
});

test("partially streamed markdown with dangling tags stays inert", () => {
  const { container } = render(<Markdown content={"Answer with <scr"} />);
  expect(container.querySelector("script")).toBeNull();
  expect(container.textContent).toContain("Answer with <scr");
});

test("diagram validation waits until streaming has completed", async () => {
  vi.mocked(mermaid.parse).mockClear();
  vi.mocked(mermaid.render).mockClear();
  const content = '```mermaid\nflowchart LR\nA["One"] --> B\n```';
  const view = render(<Markdown content={content} isStreaming />);
  expect(screen.getByText("Diagram will appear when the answer is complete.")).toBeInTheDocument();
  expect(mermaid.parse).not.toHaveBeenCalled();
  expect(mermaid.render).not.toHaveBeenCalled();
  view.rerender(<Markdown content={content} />);
  await waitFor(() => expect(mermaid.render).toHaveBeenCalledWith(expect.any(String), 'flowchart LR\nA["One"] --> B'));
  expect(mermaid.parse).toHaveBeenCalledWith('flowchart LR\nA["One"] --> B');
  expect(view.container.querySelector("pre")).toBeNull();
});

test("invalid Mermaid preserves the answer and safely displays its source", async () => {
  vi.mocked(mermaid.parse).mockRejectedValueOnce(new Error("Parse error"));
  vi.mocked(mermaid.render).mockClear();
  const { container } = render(
    <Markdown content={"Useful explanation.\n\n```mermaid\nflowchart LR\nA[<img src=x onerror=alert(1)>\n```"} />,
  );
  expect(await screen.findByText("This diagram could not be rendered. Its source is shown below.")).toBeInTheDocument();
  expect(screen.getByText("Useful explanation.")).toBeInTheDocument();
  expect(container.querySelector("img")).toBeNull();
  expect(container.querySelector("pre")?.textContent).toContain("<img src=x onerror=alert(1)>");
  expect(mermaid.render).not.toHaveBeenCalled();
});

test("diagram releases pan and zoom listeners when it unmounts", async () => {
  vi.mocked(svgPanZoom).mockClear();
  const view = render(<Markdown content={"```mermaid\nflowchart LR\nA --> B\n```"} />);
  await waitFor(() => expect(svgPanZoom).toHaveBeenCalledOnce());
  const controls = vi.mocked(svgPanZoom).mock.results[0].value;
  view.unmount();
  expect(controls.destroy).toHaveBeenCalledOnce();
});

test("unrelated renders preserve the SVG initialized by pan and zoom", async () => {
  vi.mocked(svgPanZoom).mockClear();
  const view = render(<Mermaid chart="flowchart LR; A --> B" zoomingEnabled />);
  await waitFor(() => expect(svgPanZoom).toHaveBeenCalledOnce());
  const svg = view.container.querySelector("svg")!;
  svg.setAttribute("data-pan-zoom", "initialized");
  view.rerender(<Mermaid chart="flowchart LR; A --> B" zoomingEnabled />);
  expect(view.container.querySelector("svg")).toBe(svg);
  expect(svg).toHaveAttribute("data-pan-zoom", "initialized");
});
