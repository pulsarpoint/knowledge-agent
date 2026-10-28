import type { ReactElement } from "react";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import AddConnectionSheet from "./AddConnectionSheet";
import AddDeploymentSheet from "./AddDeploymentSheet";
import AddRelationSheet from "./AddRelationSheet";
import AddServerSheet from "./AddServerSheet";
import AddServiceSheet from "./AddServiceSheet";

// Every list the sheets load is empty here: they are about closing, not picking.
beforeEach(() =>
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async () =>
        new Response(JSON.stringify({ servers: [], services: [], environments: [], projects: [], code_projects: [] })),
    ),
  ),
);
afterEach(() => vi.unstubAllGlobals());

type User = ReturnType<typeof userEvent.setup>;
const typeIn = (label: string) => async (user: User) => user.type(await screen.findByLabelText(label), "8");
const sheets: [string, (onClose: () => void) => ReactElement, (user: User) => Promise<void>][] = [
  [
    "Add server",
    (onClose) => <AddServerSheet environmentId="e1" memberIds={[]} onAdded={vi.fn()} onClose={onClose} />,
    async (user) => {
      await user.click(await screen.findByRole("combobox", { name: "Server" }));
      await user.click(screen.getByRole("option", { name: "A new server" }));
    },
  ],
  [
    "Add service",
    (onClose) => (
      <AddServiceSheet environmentId="e1" server={{ id: "s1", name: "app-1" }} onAdded={vi.fn()} onClose={onClose} />
    ),
    typeIn("Runtime"),
  ],
  [
    "Add deployment",
    (onClose) => <AddDeploymentSheet serviceId="v1" serviceName="api" onSaved={vi.fn()} onClose={onClose} />,
    typeIn("Runtime"),
  ],
  [
    "Add relation",
    (onClose) => (
      <AddRelationSheet serviceId="v1" services={[]} kind="depends_on" onSaved={vi.fn()} onClose={onClose} />
    ),
    typeIn("Description"),
  ],
  [
    "Add connection",
    (onClose) => (
      <AddConnectionSheet from={["d1"]} services={[]} onSaved={vi.fn()} onConflict={vi.fn()} onClose={onClose} />
    ),
    typeIn("Port"),
  ],
];

test.each(sheets)("%s: a clean sheet closes on Escape at once", async (_, sheet) => {
  const user = userEvent.setup();
  const onClose = vi.fn();
  render(sheet(onClose));
  await screen.findByRole("dialog");
  await user.keyboard("{Escape}");
  expect(onClose).toHaveBeenCalledTimes(1);
  expect(screen.queryByRole("alertdialog")).toBeNull();
});

test.each(sheets)("%s: with input, Escape asks Stay / Leave; Cancel discards at once", async (_, sheet, edit) => {
  const user = userEvent.setup();
  const onClose = vi.fn();
  const { unmount } = render(sheet(onClose));
  await edit(user);
  await user.keyboard("{Escape}");
  const ask = within(await screen.findByRole("alertdialog"));
  expect(ask.getByText("You have unsaved changes. Leave without saving?")).toBeVisible();
  await user.click(ask.getByRole("button", { name: "Stay" }));
  await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
  expect(onClose).not.toHaveBeenCalled();
  await user.keyboard("{Escape}");
  await user.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Leave" }));
  expect(onClose).toHaveBeenCalledTimes(1);
  unmount();
  // Cancel discards without asking
  const cancelled = vi.fn();
  render(sheet(cancelled));
  await edit(user);
  await user.click(screen.getByRole("button", { name: "Cancel" }));
  expect(cancelled).toHaveBeenCalledTimes(1);
  expect(screen.queryByRole("alertdialog")).toBeNull();
});
