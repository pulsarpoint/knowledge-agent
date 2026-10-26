import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import ResourceTabs, { type ResourceGroup } from "~/features/agents/ResourceTabs";

const groups: ResourceGroup[] = [
  {
    key: "servers",
    label: "Servers",
    many: "servers",
    items: [
      { id: "gone", name: "Missing server (gone)", description: "It is dropped when you save.", missing: true },
      { id: "s1", name: "web-1", description: "Front end", note: "via environment Production" },
      { id: "s2", name: "db-1", description: "Database" },
      { id: "s3", name: "web-2", description: "Second front end" },
      { id: "s4", name: "old-1", disabled: true, note: "No SSH." },
    ],
  },
  { key: "services", label: "Services", many: "services", items: [{ id: "v1", name: "Payments" }] },
  { key: "environments", label: "Environments", many: "environments", items: [] },
];

function Harness({ variant }: { variant?: "tabs" | "accordion" }) {
  const [value, setValue] = useState<Record<string, string[]>>({ servers: ["gone", "s1"], services: [] });
  return (
    <>
      <ResourceTabs
        groups={groups}
        value={value}
        onChange={(key, ids) => setValue((current) => ({ ...current, [key]: ids }))}
        variant={variant}
      />
      <output aria-label="Value">{JSON.stringify(value)}</output>
    </>
  );
}
const value = () => JSON.parse(screen.getByLabelText("Value").textContent!);

test("a tab per type with its count; every entry with a switch; missing ones first with their badge", async () => {
  const user = userEvent.setup();
  render(<Harness />);
  expect(screen.getAllByRole("tab").map((tab) => tab.textContent)).toEqual([
    "Servers 2/5",
    "Services 0/1",
    "Environments 0/0",
  ]);
  const switches = screen.getAllByRole("switch").filter((item) => item.getAttribute("aria-label") !== "All servers");
  expect(switches.map((item) => item.getAttribute("aria-label"))).toEqual([
    "Missing server (gone)",
    "web-1",
    "db-1",
    "web-2",
    "old-1",
  ]);
  expect(screen.getByText("missing")).toBeVisible();
  expect(screen.getByText("via environment Production")).toBeVisible();
  expect(screen.getByText("Front end")).toBeVisible();
  expect(screen.getByRole("switch", { name: "web-1" })).toBeChecked();
  expect(screen.getByRole("switch", { name: "old-1" })).toBeDisabled();
  await user.click(screen.getByRole("switch", { name: "db-1" }));
  await user.click(screen.getByRole("switch", { name: "web-1" }));
  expect(value().servers).toEqual(["gone", "s2"]);
  expect(screen.getByRole("tab", { name: "Servers 2/5" })).toBeInTheDocument();
  await user.click(screen.getByRole("tab", { name: "Environments 0/0" }));
  expect(screen.getByText("No environments.")).toBeVisible();
  await user.click(screen.getByRole("tab", { name: "Services 0/1" }));
  await user.click(screen.getByRole("switch", { name: "Payments" }));
  expect(screen.getByRole("tab", { name: "Services 1/1" })).toBeInTheDocument();
  await user.click(screen.getByRole("tab", { name: "Servers 2/5" }));
  expect(screen.getByRole("switch", { name: "db-1" })).toBeChecked(); // switching tabs keeps the changes
});

test("All turns every entry of the tab on or off (not disabled ones); with a filter only the filtered ones", async () => {
  const user = userEvent.setup();
  render(<Harness />);
  const all = screen.getByRole("switch", { name: "All servers" });
  expect(all).not.toBeChecked();
  expect(screen.getByText("2 of 5")).toBeVisible();
  await user.click(all);
  expect(value().servers).toEqual(["gone", "s1", "s2", "s3"]);
  expect(screen.getByRole("switch", { name: "All servers" })).toBeChecked();
  await user.click(screen.getByRole("switch", { name: "All servers" }));
  expect(value().servers).toEqual([]);
  await user.type(screen.getByRole("textbox", { name: "Filter servers" }), "WEB");
  expect(screen.queryByRole("switch", { name: "db-1" })).toBeNull();
  expect(screen.getByRole("switch", { name: "web-2" })).toBeVisible();
  expect(screen.getByText("0 of 2")).toBeVisible();
  await user.click(screen.getByRole("switch", { name: "All servers" }));
  expect(value().servers).toEqual(["s1", "s3"]);
  await user.clear(screen.getByRole("textbox", { name: "Filter servers" }));
  await user.type(screen.getByRole("textbox", { name: "Filter servers" }), "database");
  expect(screen.getByRole("switch", { name: "db-1" })).toBeVisible(); // the description matches too
  await user.type(screen.getByRole("textbox", { name: "Filter servers" }), "zzz");
  expect(screen.getByText("No servers match.")).toBeVisible();
});

test("narrow screens: an accordion section per type with its count, the same list inside", async () => {
  const user = userEvent.setup();
  render(<Harness variant="accordion" />);
  expect(screen.queryByRole("tab")).toBeNull();
  const servers = screen.getByRole("button", { name: "Servers 2/5" });
  expect(screen.getByRole("button", { name: "Services 0/1" })).toBeInTheDocument();
  expect(screen.queryByRole("switch", { name: "db-1" })).toBeNull();
  await user.click(servers);
  await user.click(screen.getByRole("switch", { name: "db-1" }));
  expect(screen.getByRole("button", { name: "Servers 3/5" })).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Services 0/1" }));
  const region = screen.getByRole("region", { name: "Services" });
  expect(within(region).getByRole("switch", { name: "Payments" })).toBeVisible();
});

test("locked entries count as on and stay on", async () => {
  render(
    <ResourceTabs
      groups={[
        {
          key: "documents",
          label: "Documents",
          many: "documents",
          items: [{ id: "d1", name: "Runbook", locked: true }],
        },
      ]}
      value={{}}
      onChange={() => {}}
    />,
  );
  expect(screen.getByRole("tab", { name: "Documents 1/1" })).toBeInTheDocument();
  expect(screen.getByRole("switch", { name: "Runbook" })).toBeChecked();
  expect(screen.getByRole("switch", { name: "Runbook" })).toBeDisabled();
});
