import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import type { DocumentProposal, Proposal, ServiceProposal } from "~/features/servers/discovery";
import ProposalSheet from "./ProposalSheet";

vi.mock("~/components/Markdown", () => ({ default: ({ content }: { content: string }) => <div>{content}</div> }));

const base = {
  session_id: "r1",
  server_id: "s1",
  status: "pending" as const,
  revision: 1,
  result_id: null,
  decided_at: null,
  service_id: null,
  document_id: null,
  service_proposal_id: null,
};
const redis: ServiceProposal = {
  ...base,
  id: "p1",
  kind: "service",
  number: 1,
  payload: {
    name: "redis",
    service_type: "builtin",
    service_role: "cache",
    description: "Session cache",
    owner: "",
    deployment: {
      environment_id: "e1",
      environment: "prod",
      runtime: "systemd",
      runtime_name: "redis-server",
      port: 6379,
      log_location: "",
      deployed_version: "",
    },
    depends_on: [{ id: "n1", name: "nginx" }],
    built_from: [{ id: "c1", name: "Shop" }],
    part_of: [{ id: "b1", name: "billing" }],
    connections: [
      {
        to_deployment_id: "d1",
        to_service_id: null,
        to_host: null,
        label: "postgres @ db-1 (prod)",
        port: 5432,
        protocol: "postgres",
        description: "Sessions store",
      },
      {
        to_deployment_id: null,
        to_service_id: null,
        to_host: "api.example.com",
        label: "api.example.com",
        port: 443,
        protocol: "https",
        description: "",
      },
    ],
    matched_revision: null,
  },
  present: { connections: [true, false], relations: ["depends_on:n1"] },
};
const runbook: DocumentProposal = {
  ...base,
  id: "p2",
  kind: "document",
  number: 2,
  service_proposal_id: "p1",
  service_name: "redis",
  service_proposal_status: "pending",
  payload: { title: "Redis runbook", description: "", markdown: "# Runbook", about_service: "redis" },
};
const other: DocumentProposal = {
  ...runbook,
  id: "p3",
  number: 3,
  service_proposal_id: null,
  service_name: undefined,
  payload: { ...runbook.payload, title: "Server overview", about_service: null },
};

function show(proposal: Proposal, onOpen = vi.fn()) {
  render(
    <MemoryRouter>
      <ProposalSheet
        proposal={proposal}
        related={[redis, runbook, other]}
        message={null}
        onClose={() => {}}
        onMessage={() => {}}
        onReload={() => {}}
        onOpen={onOpen}
      />
    </MemoryRouter>,
  );
  return onOpen;
}

test("a service proposal shows its connections, relations and the run's documents about it", async () => {
  const user = userEvent.setup();
  const onOpen = show(redis);
  const sheet = await screen.findByRole("dialog");
  const connections = within(sheet).getByRole("region", { name: "Connections" });
  const rows = within(connections).getAllByRole("row").slice(1);
  expect(rows.map((row) => row.textContent)).toEqual([
    "postgres @ db-1 (prod)5432postgresSessions storeRecorded",
    "api.example.com443httpsNew",
  ]);
  const relations = within(sheet).getByRole("region", { name: "Relations" });
  expect(
    within(relations)
      .getAllByRole("listitem")
      .map((item) => item.textContent),
  ).toEqual(["Depends on nginx (recorded)", "Part of billing", "Built from Shop"]);
  const documents = within(sheet).getByRole("region", { name: "Documents" });
  expect(
    within(documents)
      .getAllByRole("button")
      .map((button) => button.textContent),
  ).toEqual(["2 · Redis runbook"]);
  await user.click(within(documents).getByRole("button", { name: "2 · Redis runbook" }));
  expect(onOpen).toHaveBeenCalledWith("p2");
});

test("a document about a proposed service links to that service proposal", async () => {
  const user = userEvent.setup();
  const onOpen = show(runbook);
  const sheet = await screen.findByRole("dialog");
  await user.click(within(sheet).getByRole("button", { name: "1 · New service redis" }));
  expect(onOpen).toHaveBeenCalledWith("p1");
});

test("an older service proposal without connections or part_of still shows", async () => {
  const { connections: _c, part_of: _p, ...payload } = redis.payload;
  show({ ...redis, payload, present: undefined });
  const sheet = await screen.findByRole("dialog");
  expect(within(sheet).queryByRole("region", { name: "Connections" })).toBeNull();
  expect(within(within(sheet).getByRole("region", { name: "Relations" })).getAllByRole("listitem")).toHaveLength(2);
});
