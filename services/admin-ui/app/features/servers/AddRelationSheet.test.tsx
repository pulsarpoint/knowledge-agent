import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { serviceAsset as asset, stubApi as api } from "~/lib/__tests__/serviceFixture";
import type { RelationKind } from "./inventory";
import AddRelationSheet from "./AddRelationSheet";

const services = [asset("v1", "payments-api"), asset("v5", "billing"), asset("v2", "postgres")];
const repositories = [
  { id: "r2", name: "shop-repo" },
  { id: "r1", name: "payments-repo" },
];
afterEach(() => vi.unstubAllGlobals());

function dialog(kind: RelationKind = "depends_on") {
  const handlers = { onSaved: vi.fn(), onClose: vi.fn() };
  render(<AddRelationSheet serviceId="v1" services={services} kind={kind} {...handlers} />);
  return handlers;
}
const routes = {
  "GET /api/repositories": () => [200, { repositories }] as [number, unknown],
  "POST /api/relations": (body: any) => [201, { id: "rel9", revision: 1, ...body }] as [number, unknown],
};

test("depends on another service: the picker leaves this service out", async () => {
  const user = userEvent.setup();
  const calls = api(routes);
  const { onSaved } = dialog();
  await user.click(screen.getByRole("combobox", { name: "Service" }));
  expect(screen.getAllByRole("option").map((option) => option.textContent)).toEqual(["billing", "postgres"]);
  await user.click(screen.getByRole("option", { name: "postgres" }));
  await user.type(screen.getByLabelText("Description"), "stores orders");
  await user.click(screen.getByRole("button", { name: "Add relation" }));
  await vi.waitFor(() => expect(onSaved).toHaveBeenCalled());
  expect(calls.find((call) => call.key === "POST /api/relations")!.body).toEqual({
    from_service_id: "v1",
    kind: "depends_on",
    to_service_id: "v2",
    to_code_project_id: null,
    description: "stores orders",
  });
});

test("part of another service", async () => {
  const user = userEvent.setup();
  const calls = api(routes);
  const { onSaved } = dialog();
  await user.click(screen.getByRole("combobox", { name: "Kind" }));
  await user.click(screen.getByRole("option", { name: "Part of" }));
  await user.click(screen.getByRole("combobox", { name: "Service" }));
  await user.click(screen.getByRole("option", { name: "billing" }));
  await user.click(screen.getByRole("button", { name: "Add relation" }));
  await vi.waitFor(() => expect(onSaved).toHaveBeenCalled());
  expect(calls.find((call) => call.key === "POST /api/relations")!.body).toMatchObject({
    kind: "part_of",
    to_service_id: "v5",
    to_code_project_id: null,
  });
});

test("built from a code project", async () => {
  const user = userEvent.setup();
  const calls = api(routes);
  const { onSaved } = dialog("built_from");
  expect(screen.getByRole("combobox", { name: "Kind" })).toHaveTextContent("Built from");
  await user.click(await screen.findByRole("combobox", { name: "Code project" }));
  expect(screen.getAllByRole("option").map((option) => option.textContent)).toEqual(["payments-repo", "shop-repo"]);
  await user.click(screen.getByRole("option", { name: "payments-repo" }));
  await user.click(screen.getByRole("button", { name: "Add relation" }));
  await vi.waitFor(() => expect(onSaved).toHaveBeenCalled());
  expect(calls.find((call) => call.key === "POST /api/relations")!.body).toEqual({
    from_service_id: "v1",
    kind: "built_from",
    to_service_id: null,
    to_code_project_id: "r1",
    description: "",
  });
});

test("a refusal stays in the dialog", async () => {
  const user = userEvent.setup();
  api({ ...routes, "POST /api/relations": () => [409, { error: { message: "This relation already exists" } }] });
  const { onSaved } = dialog();
  await user.click(screen.getByRole("combobox", { name: "Service" }));
  await user.click(screen.getByRole("option", { name: "billing" }));
  await user.click(screen.getByRole("button", { name: "Add relation" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("This relation already exists");
  expect(onSaved).not.toHaveBeenCalled();
});

test("a relation to a draft service says saving confirms it", async () => {
  const user = userEvent.setup();
  api(routes);
  render(
    <AddRelationSheet
      serviceId="v1"
      services={[services[0], services[1], { ...services[2], status: "draft" }]}
      kind="depends_on"
      onSaved={vi.fn()}
      onClose={vi.fn()}
    />,
  );
  await user.click(screen.getByRole("combobox", { name: "Service" }));
  await user.click(screen.getByRole("option", { name: "postgres" }));
  expect(screen.getByText("Saving confirms the drafts it uses: postgres.")).toBeInTheDocument();
});
