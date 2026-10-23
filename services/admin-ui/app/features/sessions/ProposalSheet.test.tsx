import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import type { DocumentProposal } from "~/features/servers/discovery";
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
};
const runbook: DocumentProposal = {
  ...base,
  id: "p2",
  kind: "document",
  number: 2,
  service_name: "redis",
  payload: { title: "Redis runbook", description: "", markdown: "# Runbook", about_service: "redis" },
};

function show(proposal: DocumentProposal, resourceType: "server" | "service" | "environment" = "server") {
  render(
    <MemoryRouter>
      <ProposalSheet
        proposal={proposal}
        resourceType={resourceType}
        message={null}
        onClose={() => {}}
        onMessage={() => {}}
        onReload={() => {}}
      />
    </MemoryRouter>,
  );
}

test("a document proposal about a recorded service says so and shows the document", async () => {
  show(runbook);
  const sheet = await screen.findByRole("dialog");
  expect(within(sheet).getByText("About service redis")).toBeInTheDocument();
  expect(within(sheet).getByText("# Runbook")).toBeInTheDocument();
  expect(within(sheet).getByRole("button", { name: "Accept" })).toBeEnabled();
});

test("a document proposal about the run's resource names that resource, and a service proposal view is gone", async () => {
  show({ ...runbook, service_name: undefined, payload: { ...runbook.payload, about_service: null } }, "environment");
  const sheet = await screen.findByRole("dialog");
  expect(within(sheet).getByText("About this environment")).toBeInTheDocument();
  expect(within(sheet).queryByRole("region")).toBeNull();
});
