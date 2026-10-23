import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { stubApi } from "~/lib/__tests__/serviceFixture";
import ConfirmDraftSheet, { DraftActions, DraftsUsed } from "./ConfirmDraftSheet";
import { RowActions } from "./RowAction";

afterEach(() => vi.unstubAllGlobals());

const reached = [
  { kind: "deployment", id: "d1", name: "cache on app-1 in prod" },
  { kind: "service", id: "v1", name: "cache" },
  { kind: "server", id: "s1", name: "app-1" },
];

test("Confirm lists what the dry run reaches, then confirms with the revision", async () => {
  const user = userEvent.setup();
  const calls = stubApi({
    "POST /api/inventory/deployment/d1/confirm?dry_run=1": () => [200, { confirmed: reached }],
    "POST /api/inventory/deployment/d1/confirm": () => [200, { confirmed: reached }],
  });
  const onDone = vi.fn();
  render(
    <ConfirmDraftSheet
      action="confirm"
      target={{ kind: "deployment", id: "d1", revision: 2 }}
      name="cache on app-1"
      onDone={onDone}
      onClose={() => {}}
    />,
  );
  const sheet = within(await screen.findByRole("dialog", { name: "Confirm cache on app-1?" }));
  expect(await sheet.findByText("Confirming it also confirms these drafts:")).toBeInTheDocument();
  expect(sheet.getAllByRole("listitem").map((item) => item.textContent)).toEqual(["cache", "app-1"]);
  expect(calls).toHaveLength(1);
  await user.click(sheet.getByRole("button", { name: "Confirm" }));
  expect(calls.map((call) => [call.key, call.body])).toEqual([
    ["POST /api/inventory/deployment/d1/confirm?dry_run=1", { revision: 2 }],
    ["POST /api/inventory/deployment/d1/confirm", { revision: 2 }],
  ]);
  expect(onDone).toHaveBeenCalled();
});

test("Discard lists what it deletes; nothing else says so", async () => {
  const user = userEvent.setup();
  stubApi({
    "POST /api/inventory/membership/e1%3As1/discard?dry_run=1": () => [
      200,
      { discarded: [{ kind: "membership", id: "e1:s1", name: "app-1 in prod" }] },
    ],
    "POST /api/inventory/membership/e1%3As1/discard": () => [200, { discarded: [] }],
  });
  const onDone = vi.fn();
  render(
    <ConfirmDraftSheet
      action="discard"
      target={{ kind: "membership", id: "e1:s1", revision: null }}
      name="app-1"
      onDone={onDone}
      onClose={() => {}}
    />,
  );
  const sheet = within(await screen.findByRole("dialog", { name: "Discard app-1?" }));
  expect(await sheet.findByText("Nothing else is deleted with it.")).toBeInTheDocument();
  await user.click(sheet.getByRole("button", { name: "Discard" }));
  expect(onDone).toHaveBeenCalled();
});

test("a stale revision says it changed and does not confirm", async () => {
  stubApi({
    "POST /api/inventory/service/v1/confirm?dry_run=1": () => [
      409,
      { error: { message: "stale", code: "STALE_REVISION" } },
    ],
  });
  render(
    <ConfirmDraftSheet
      action="confirm"
      target={{ kind: "service", id: "v1", revision: 1 }}
      name="cache"
      onDone={() => {}}
      onClose={() => {}}
    />,
  );
  const sheet = within(await screen.findByRole("dialog"));
  expect(await sheet.findByText("This was changed meanwhile. Reload to see the latest.")).toBeInTheDocument();
  expect(sheet.getByRole("button", { name: "Confirm" })).toBeDisabled();
});

test("a draft changed between the dry run and Confirm says so and stays open", async () => {
  const user = userEvent.setup();
  stubApi({
    "POST /api/inventory/service/v1/confirm?dry_run=1": () => [200, { confirmed: [] }],
    "POST /api/inventory/service/v1/confirm": () => [409, { error: { message: "stale", code: "STALE_REVISION" } }],
  });
  const onDone = vi.fn();
  render(
    <ConfirmDraftSheet
      action="confirm"
      target={{ kind: "service", id: "v1", revision: 1 }}
      name="cache"
      onDone={onDone}
      onClose={() => {}}
    />,
  );
  const sheet = within(await screen.findByRole("dialog"));
  await sheet.findByText("Nothing else is confirmed with it.");
  await user.click(sheet.getByRole("button", { name: "Confirm" }));
  expect(await sheet.findByText("This was changed meanwhile. Reload to see the latest.")).toBeInTheDocument();
  expect(sheet.getByRole("button", { name: "Confirm" })).toBeDisabled();
  expect(onDone).not.toHaveBeenCalled();
});

test("the row buttons open the sheet for their own target", async () => {
  const user = userEvent.setup();
  stubApi({
    "POST /api/inventory/server/s1/discard?dry_run=1": () => [
      200,
      { discarded: [{ kind: "server", id: "s1", name: "x" }] },
    ],
  });
  render(
    <RowActions>
      <DraftActions
        name="app-1"
        confirm={{ kind: "membership", id: "e1:s1", revision: null }}
        discard={{ kind: "server", id: "s1", revision: 3 }}
        onDone={() => {}}
      />
    </RowActions>,
  );
  expect(screen.getByRole("button", { name: "Confirm app-1" })).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Discard app-1" }));
  expect(await screen.findByRole("dialog", { name: "Discard app-1?" })).toBeInTheDocument();
});

test("a form that uses drafts says saving confirms them", () => {
  const { rerender } = render(<DraftsUsed names={["cache", "app-1"]} />);
  expect(screen.getByText("Saving confirms the drafts it uses: cache, app-1.")).toBeInTheDocument();
  rerender(<DraftsUsed names={[]} />);
  expect(screen.queryByText(/Saving confirms/)).not.toBeInTheDocument();
});
