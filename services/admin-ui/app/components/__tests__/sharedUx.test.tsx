import { useRef, useState } from "react";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Link, MemoryRouter, Outlet, useNavigate } from "react-router";
import { renderRoutes } from "~/lib/__tests__/dataRouter";
import BackLink from "../BackLink";
import SectionLinks from "../SectionLinks";
import ConfirmRemove, { type Removal } from "../ConfirmRemove";
import LeaveGuard, { useSheetGuard } from "../LeaveGuard";
import { Sheet, SheetContent, SheetTitle } from "~/components/ui/sheet";
import { Button } from "~/components/ui/button";

test("BackLink: a link button with an arrow back to the list, All <plural>", () => {
  render(
    <MemoryRouter>
      <BackLink to="/skills">All skills</BackLink>
    </MemoryRouter>,
  );
  const link = screen.getByRole("link", { name: "All skills" });
  expect(link).toHaveAttribute("href", "/skills");
  expect(link.querySelector("svg")).not.toBeNull();
});

function Removing({ removal, ...props }: { removal: Removal; busy?: boolean; error?: string | null }) {
  const [open, setOpen] = useState<Removal | null>(removal);
  return (
    <>
      <span>{open ? "open" : "closed"}</span>
      <ConfirmRemove removal={open} onClose={() => setOpen(null)} {...props}>
        <label>
          <input type="checkbox" /> Also delete history
        </label>
      </ConfirmRemove>
    </>
  );
}

test("ConfirmRemove: the title, description and children, Cancel, and a destructive action that runs and closes", async () => {
  const run = vi.fn();
  render(
    <Removing removal={{ title: "Delete “a”?", description: "It goes for good.", action: "Delete agent", run }} />,
  );
  const dialog = within(screen.getByRole("alertdialog"));
  expect(dialog.getByText("Delete “a”?")).toBeInTheDocument();
  expect(dialog.getByText("It goes for good.")).toBeInTheDocument();
  expect(dialog.getByLabelText("Also delete history")).toBeInTheDocument();
  const action = dialog.getByRole("button", { name: "Delete agent" });
  expect(action.className).toContain("bg-destructive");
  await userEvent.click(dialog.getByRole("button", { name: "Cancel" }));
  expect(run).not.toHaveBeenCalled();
  expect(screen.getByText("closed")).toBeInTheDocument();
});

test("ConfirmRemove: a run that returns a promise keeps the dialog open while busy; a failure is shown and it stays", async () => {
  let fail: (e: Error) => void = () => {};
  const run = vi
    .fn()
    .mockImplementationOnce(() => new Promise<void>((_, reject) => (fail = reject)))
    .mockImplementationOnce(async () => {});
  render(<Removing removal={{ title: "Delete “a”?", description: "", action: "Delete", run }} />);
  const dialog = within(screen.getByRole("alertdialog"));
  await userEvent.click(dialog.getByRole("button", { name: "Delete" }));
  expect(dialog.getByRole("button", { name: "Delete" })).toBeDisabled();
  fail(new Error("Still in use"));
  expect(await dialog.findByText("Still in use")).toBeInTheDocument();
  expect(screen.getByText("open")).toBeInTheDocument();
  await userEvent.click(dialog.getByRole("button", { name: "Delete" }));
  await waitFor(() => expect(screen.getByText("closed")).toBeInTheDocument());
});

test("ConfirmRemove: busy and error from the page", () => {
  render(
    <Removing
      busy
      error="Could not delete"
      removal={{ title: "Delete “a”?", description: "", action: "Delete", run: () => {} }}
    />,
  );
  const dialog = within(screen.getByRole("alertdialog"));
  expect(dialog.getByRole("button", { name: "Delete" })).toBeDisabled();
  expect(dialog.getByText("Could not delete")).toBeInTheDocument();
});

function Form({ samePage }: { samePage?: (a: string, b: string) => boolean }) {
  const [value, setValue] = useState("");
  const leaving = useRef(false);
  const navigate = useNavigate();
  return (
    <>
      <input aria-label="Name" value={value} onChange={(e) => setValue(e.target.value)} />
      <Link to="/other">Other</Link>
      <Link to="/form/agent/r1">Agent run</Link>
      <button
        type="button"
        onClick={() => {
          leaving.current = true;
          navigate("/other");
        }}
      >
        Save
      </button>
      <LeaveGuard dirty={value !== ""} samePage={samePage} leaving={leaving} />
    </>
  );
}

const route = (samePage?: (a: string, b: string) => boolean) =>
  renderRoutes(
    [
      { path: "/form/*", element: <Form samePage={samePage} /> },
      { path: "/other", element: <p>Other page</p> },
    ],
    "/form",
  ).router;

afterEach(() => vi.unstubAllGlobals());

test("LeaveGuard: a clean form leaves without asking", async () => {
  route();
  await userEvent.click(screen.getByRole("link", { name: "Other" }));
  expect(await screen.findByText("Other page")).toBeInTheDocument();
  expect(screen.queryByRole("alertdialog")).toBeNull();
});

test("LeaveGuard: a dirty form asks Stay / Leave", async () => {
  const router = route();
  await userEvent.type(screen.getByLabelText("Name"), "x");
  await userEvent.click(screen.getByRole("link", { name: "Other" }));
  let dialog = within(await screen.findByRole("alertdialog"));
  await userEvent.click(dialog.getByRole("button", { name: "Stay" }));
  expect(router.state.location.pathname).toBe("/form");
  expect(screen.getByLabelText("Name")).toHaveValue("x");
  await userEvent.click(screen.getByRole("link", { name: "Other" }));
  dialog = within(await screen.findByRole("alertdialog"));
  await userEvent.click(dialog.getByRole("button", { name: "Leave" }));
  expect(await screen.findByText("Other page")).toBeInTheDocument();
});

test("LeaveGuard: samePage paths and a page's own leaving never ask", async () => {
  const router = route((a, b) => a.replace(/\/agent\/[^/]+$/, "") === b.replace(/\/agent\/[^/]+$/, ""));
  await userEvent.type(screen.getByLabelText("Name"), "x");
  await userEvent.click(screen.getByRole("link", { name: "Agent run" }));
  await waitFor(() => expect(router.state.location.pathname).toBe("/form/agent/r1"));
  expect(screen.queryByRole("alertdialog")).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "Save" }));
  expect(await screen.findByText("Other page")).toBeInTheDocument();
  expect(screen.queryByRole("alertdialog")).toBeNull();
});

function GuardedSheet() {
  const [open, setOpen] = useState(true);
  const [value, setValue] = useState("");
  const guard = useSheetGuard(value !== "");
  return (
    <>
      <span>{open ? "sheet open" : "sheet closed"}</span>
      <Sheet open={open} onOpenChange={(next) => (next ? setOpen(true) : guard.close(() => setOpen(false)))}>
        <SheetContent>
          <SheetTitle>Edit</SheetTitle>
          <input aria-label="Name" value={value} onChange={(e) => setValue(e.target.value)} />
          <Button type="button" onClick={() => guard.close(() => setOpen(false))}>
            Cancel
          </Button>
        </SheetContent>
      </Sheet>
      {guard.dialog}
    </>
  );
}

test("useSheetGuard: a clean sheet closes at once", async () => {
  const user = userEvent.setup();
  render(<GuardedSheet />);
  await user.click(screen.getByRole("button", { name: "Cancel" }));
  expect(screen.getByText("sheet closed")).toBeInTheDocument();
  expect(screen.queryByRole("alertdialog")).toBeNull();
});

test("useSheetGuard: a dirty sheet asks Stay / Leave on close (Escape or Cancel)", async () => {
  const user = userEvent.setup();
  render(<GuardedSheet />);
  await user.type(screen.getByLabelText("Name"), "x");
  await user.keyboard("{Escape}");
  let dialog = within(await screen.findByRole("alertdialog"));
  expect(dialog.getByText("Unsaved changes")).toBeInTheDocument();
  await user.click(dialog.getByRole("button", { name: "Stay" }));
  expect(screen.getByText("sheet open")).toBeInTheDocument();
  expect(screen.getByLabelText("Name")).toHaveValue("x");
  await user.click(screen.getByRole("button", { name: "Cancel" }));
  dialog = within(await screen.findByRole("alertdialog"));
  await user.click(dialog.getByRole("button", { name: "Leave" }));
  await waitFor(() => expect(screen.getByText("sheet closed")).toBeInTheDocument());
});

test("SectionLinks: the open section's help line under the links; a section without help shows none", async () => {
  const sections = [
    { to: "/page", label: "One", end: true, help: "What one is." },
    { to: "/page/two", label: "Two", help: "What two is." },
    { to: "/page/three", label: "Three" },
  ];
  const layout = (
    <>
      <SectionLinks label="Page sections" sections={sections} />
      <Outlet />
    </>
  );
  const { router } = renderRoutes(
    [
      {
        path: "/page",
        element: layout,
        children: [
          { index: true, element: <p>one</p> },
          { path: "two/*", element: <p>two</p> },
          { path: "three", element: <p>three</p> },
        ],
      },
    ],
    "/page",
  );
  expect(await screen.findByText("What one is.")).toBeInTheDocument();
  expect(screen.queryByText("What two is.")).toBeNull();
  await userEvent.click(screen.getByRole("link", { name: "Two" }));
  expect(await screen.findByText("What two is.")).toBeInTheDocument();
  expect(screen.queryByText("What one is.")).toBeNull();
  await router.navigate("/page/two/deeper");
  expect(await screen.findByText("What two is.")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("link", { name: "Three" }));
  await screen.findByText("three");
  expect(screen.queryByText(/What .* is\./)).toBeNull();
});
