import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { Pencil, RefreshCw, Upload, X } from "lucide-react";
import RowMenu, { deleteMenuItem } from "../RowMenu";

function show(props: Partial<React.ComponentProps<typeof RowMenu>> = {}) {
  const run = vi.fn();
  const file = vi.fn();
  const remove = vi.fn();
  render(
    <MemoryRouter>
      <RowMenu
        name="notes"
        items={[
          { label: "Edit", icon: Pencil, to: "/skills/s1" },
          { label: "Update from source", icon: RefreshCw, onSelect: run },
          false,
          deleteMenuItem(remove),
          { label: "Update from upload…", icon: Upload, accept: ".zip", onFile: file },
        ]}
        {...props}
      />
    </MemoryRouter>,
  );
  return { run, file, remove };
}

test("RowMenu: a ghost ⋯ trigger named for the row opens icon-plus-label items, Delete last after a separator", async () => {
  const user = userEvent.setup();
  show();
  const trigger = screen.getByRole("button", { name: "Actions for notes" });
  await user.click(trigger);
  const items = screen.getAllByRole("menuitem");
  expect(items.map((item) => item.textContent)).toEqual([
    "Edit",
    "Update from source",
    "Update from upload…",
    "Delete",
  ]);
  for (const item of items) expect(item.querySelector("svg")).not.toBeNull();
  expect(screen.getByRole("separator")).toBeInTheDocument();
  expect(items[3].className).toContain("text-destructive");
  expect(screen.getByRole("menuitem", { name: "Edit" })).toHaveAttribute("href", "/skills/s1");
});

test("RowMenu: an action item runs, Delete calls remove", async () => {
  const user = userEvent.setup();
  const { run, remove } = show();
  await user.click(screen.getByRole("button", { name: "Actions for notes" }));
  await user.click(screen.getByRole("menuitem", { name: "Update from source" }));
  expect(run).toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "Actions for notes" }));
  await user.click(screen.getByRole("menuitem", { name: "Delete" }));
  expect(remove).toHaveBeenCalled();
});

test("RowMenu: a file item hands the chosen file over", () => {
  const { file } = show();
  const zip = new File(["zip"], "n.zip");
  fireEvent.change(screen.getByLabelText("File for notes"), { target: { files: [zip] } });
  expect(file).toHaveBeenCalledWith(zip);
});

test("RowMenu: every destructive item (Discard, Remove) goes last in destructive text, in its order", async () => {
  const user = userEvent.setup();
  render(
    <MemoryRouter>
      <RowMenu
        name="db"
        items={[
          { label: "Discard", icon: X, onSelect: () => {}, destructive: true },
          deleteMenuItem(() => {}, "Remove"),
          { label: "Edit", icon: Pencil, onSelect: () => {} },
        ]}
      />
    </MemoryRouter>,
  );
  await user.click(screen.getByRole("button", { name: "Actions for db" }));
  const items = screen.getAllByRole("menuitem");
  expect(items.map((item) => item.textContent)).toEqual(["Edit", "Discard", "Remove"]);
  expect(items[1].className).toContain("text-destructive");
  expect(items[2].className).toContain("text-destructive");
});

test("RowMenu: disabled turns the trigger off", () => {
  show({ disabled: true });
  expect(screen.getByRole("button", { name: "Actions for notes" })).toBeDisabled();
});

test("RowMenu: without a destructive item there is no separator, and Delete alone has none either", async () => {
  const user = userEvent.setup();
  show({ items: [{ label: "Edit", icon: Pencil, to: "/x" }] });
  await user.click(screen.getByRole("button", { name: "Actions for notes" }));
  expect(screen.queryByRole("separator")).toBeNull();
  await user.keyboard("{Escape}");
  render(
    <MemoryRouter>
      <RowMenu name="solo" items={[deleteMenuItem(() => {})]} />
    </MemoryRouter>,
  );
  await user.click(screen.getByRole("button", { name: "Actions for solo" }));
  expect(screen.getByRole("menuitem", { name: "Delete" })).toBeInTheDocument();
  expect(screen.queryByRole("separator")).toBeNull();
});
