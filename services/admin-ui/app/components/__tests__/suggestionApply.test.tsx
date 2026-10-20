import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import SuggestionApply from "~/features/sessions/SuggestionApply";
import * as api from "~/features/agents/reviews";
import { ApiError } from "../../lib/api";
vi.mock("~/features/agents/reviews");
vi.mock("../Markdown", () => ({ default: ({ content }: { content: string }) => <div>{content}</div> }));

const review = { id: "review-1", comment: "Be clearer", suggestion: "Be clearer.", created_at: "2026-10-08T10:00:00Z" };
const newNote = {
  name: "payments/deploy",
  content: "Payments deploys from the release branch.",
  metadata: { service: "payments" },
  exists: false,
  memory_id: null,
  current_content: null,
};
const existingNote = {
  name: "payments/owner",
  content: "Payments is owned by team A.",
  metadata: {},
  exists: true,
  memory_id: "m-9",
  current_content: "Payments is owned by team B.",
};
const source: api.ReviewSource = {
  suggestion: "Be clearer.",
  revision: 3,
  fingerprint: "f".repeat(64),
  changes: { instructions: "State uncertainty." },
  memory_notes: [newNote, existingNote],
  instructions: "Original instructions",
};
const sentNew = { name: newNote.name, content: newNote.content, metadata: newNote.metadata };
const applied = vi.fn();
const closed = vi.fn();
function page() {
  return render(<SuggestionApply agentId="custom" review={review} onClose={closed} onApplied={applied} />);
}
beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(api.getReviewSource).mockResolvedValue(structuredClone(source));
  vi.mocked(api.applyReview).mockResolvedValue({ agent_id: "custom", saved: [], refused: [] });
});

it("has no system prompt section and applies the instructions and the ticked notes to this agent", async () => {
  vi.mocked(api.applyReview).mockResolvedValue({
    agent_id: "custom",
    saved: [{ id: "m-1", name: "payments/deploy" }],
    refused: [],
  });
  page();
  expect(await screen.findByLabelText("Instructions")).toHaveValue("State uncertainty.");
  expect(api.getReviewSource).toHaveBeenCalledWith("custom", "review-1");
  expect(screen.queryByLabelText("System prompt")).toBeNull();
  expect(screen.queryByText(/system prompt/i)).toBeNull();
  expect(screen.getByText("Original instructions")).toBeInTheDocument();
  expect(screen.getByLabelText("Apply instructions")).toBeChecked();
  fireEvent.change(screen.getByLabelText("Instructions"), { target: { value: "Separate facts from guesses." } });
  fireEvent.click(screen.getByRole("button", { name: "Apply to this agent" }));
  await waitFor(() =>
    expect(api.applyReview).toHaveBeenCalledWith(
      "custom",
      "review-1",
      source,
      "agent",
      "Separate facts from guesses.",
      [sentNew],
    ),
  );
  expect(await screen.findByRole("link", { name: "payments/deploy" })).toHaveAttribute("href", "/memory?entry=m-1");
  expect(applied).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Done" }));
  expect(applied).toHaveBeenCalledWith("agent", "custom");
});

it("an existing note is unticked and links to its entry in Memory", async () => {
  page();
  expect(await screen.findByLabelText("Save note 1")).toBeChecked();
  expect(screen.getByLabelText("Save note 2")).not.toBeChecked();
  expect(screen.getByRole("link", { name: "Already exists — open in Memory" })).toHaveAttribute(
    "href",
    "/memory?entry=m-9",
  );
});

it("applies only the instructions when no note is ticked", async () => {
  vi.mocked(api.applyReview).mockResolvedValue({ agent_id: "clone-1", saved: [], refused: [] });
  page();
  fireEvent.click(await screen.findByLabelText("Save note 1"));
  fireEvent.click(screen.getByRole("button", { name: "Apply to a new clone" }));
  await waitFor(() =>
    expect(api.applyReview).toHaveBeenCalledWith("custom", "review-1", source, "clone", "State uncertainty.", []),
  );
  await waitFor(() => expect(applied).toHaveBeenCalledWith("clone", "clone-1"));
});

it("saves only edited notes without a target when the instructions are not applied", async () => {
  page();
  fireEvent.click(await screen.findByLabelText("Apply instructions"));
  expect(screen.queryByRole("button", { name: "Apply to this agent" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Apply to a new clone" })).toBeNull();
  fireEvent.change(screen.getByLabelText("Note 1 name"), { target: { value: "payments/release" } });
  fireEvent.change(screen.getByLabelText("Note 1 content"), { target: { value: "Release branch only." } });
  fireEvent.click(screen.getByLabelText("Save note 2"));
  fireEvent.click(screen.getByRole("button", { name: "Save memory notes" }));
  await waitFor(() =>
    expect(api.applyReview).toHaveBeenCalledWith("custom", "review-1", source, "agent", null, [
      { name: "payments/release", content: "Release branch only.", metadata: { service: "payments" } },
      { name: existingNote.name, content: existingNote.content, metadata: {} },
    ]),
  );
  fireEvent.click(await screen.findByRole("button", { name: "Done" }));
  expect(applied).not.toHaveBeenCalled();
  expect(closed).toHaveBeenCalled();
});

it("shows the refused names with their message", async () => {
  vi.mocked(api.applyReview).mockResolvedValue({
    agent_id: "custom",
    saved: [],
    refused: [{ name: "payments/owner", message: "An entry named payments/owner already exists." }],
  });
  page();
  fireEvent.click(await screen.findByLabelText("Save note 2"));
  fireEvent.click(screen.getByRole("button", { name: "Apply to this agent" }));
  expect(await screen.findByText("An entry named payments/owner already exists.")).toBeVisible();
});

it("needs something to apply", async () => {
  page();
  fireEvent.click(await screen.findByLabelText("Apply instructions"));
  fireEvent.click(screen.getByLabelText("Save note 1"));
  expect(screen.getByRole("button", { name: "Save memory notes" })).toBeDisabled();
});

it("does not tick Apply instructions when no change is proposed", async () => {
  vi.mocked(api.getReviewSource).mockResolvedValue({ ...structuredClone(source), changes: { instructions: null } });
  page();
  expect(await screen.findByLabelText("Apply instructions")).not.toBeChecked();
  expect(screen.getByLabelText("Instructions")).toHaveValue("Original instructions");
});

it("reloads the latest agent after a conflict, keeps the edits and applies with the new revision", async () => {
  const latest: api.ReviewSource = {
    ...structuredClone(source),
    revision: 4,
    fingerprint: "e".repeat(64),
    instructions: "Newer instructions",
  };
  vi.mocked(api.getReviewSource)
    .mockResolvedValueOnce(structuredClone(source))
    .mockResolvedValueOnce(structuredClone(latest));
  vi.mocked(api.applyReview)
    .mockRejectedValueOnce(new ApiError("The agent changed since you opened the suggestion. Review it again.", 409))
    .mockResolvedValueOnce({ agent_id: "custom", saved: [], refused: [] });
  page();
  fireEvent.change(await screen.findByLabelText("Instructions"), { target: { value: "Answer from evidence." } });
  fireEvent.click(screen.getByLabelText("Save note 1"));
  fireEvent.click(screen.getByRole("button", { name: "Apply to this agent" }));
  fireEvent.click(await screen.findByRole("button", { name: "Reload latest" }));
  await waitFor(() => expect(api.getReviewSource).toHaveBeenCalledTimes(2));
  await waitFor(() => expect(screen.queryByRole("button", { name: "Reload latest" })).not.toBeInTheDocument());
  expect(screen.getByLabelText("Instructions")).toHaveValue("Answer from evidence.");
  expect(screen.getByText("Newer instructions")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Apply to this agent" }));
  await waitFor(() => expect(applied).toHaveBeenCalledWith("agent", "custom"));
  const [, , sent, , instructions] = vi.mocked(api.applyReview).mock.calls[1];
  expect([sent.revision, sent.fingerprint, instructions]).toEqual([4, "e".repeat(64), "Answer from evidence."]);
});

it("offers Reload latest when the suggestion fails to load", async () => {
  vi.mocked(api.getReviewSource)
    .mockRejectedValueOnce(new ApiError("request failed (500)", 500))
    .mockResolvedValueOnce(structuredClone(source));
  page();
  expect(await screen.findByText("request failed (500)")).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Reload latest" }));
  expect(await screen.findByLabelText("Instructions")).toHaveValue("State uncertainty.");
  expect(screen.queryByText("request failed (500)")).not.toBeInTheDocument();
});

it("does not offer Reload latest after other apply failures", async () => {
  vi.mocked(api.applyReview).mockRejectedValueOnce(new ApiError("Nothing to apply.", 400));
  page();
  fireEvent.click(await screen.findByRole("button", { name: "Apply to this agent" }));
  expect(await screen.findByText("Nothing to apply.")).toBeVisible();
  expect(screen.queryByRole("button", { name: "Reload latest" })).not.toBeInTheDocument();
});
