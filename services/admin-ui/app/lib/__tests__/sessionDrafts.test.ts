import { insertMention, mentionable, mentionText, sessionDraftPath, stateLabel } from "~/features/documents/drafts";
import { documentWrites, type SessionEvent } from "~/features/sessions/api";

const event = (id: number, kind: string, payload: Record<string, unknown>): SessionEvent => ({
  id,
  turn: 1,
  seq: id,
  kind,
  payload,
  created_at: "2026-10-27T10:00:00Z",
});

test("a mention names the draft by its title and its full id", () => {
  expect(mentionText({ id: "0b5e-41", title: "Disk layout" })).toBe('[Draft "Disk layout" · 0b5e-41]');
  expect(mentionText({ id: "0b5e-41", title: null })).toBe('[Draft "Untitled" · 0b5e-41]');
});

test("a mention goes in at the cursor, set apart by spaces, or at the end", () => {
  const text = "[Draft]";
  expect(insertMention("Fix  please", text, { start: 4, end: 4 })).toEqual({ value: "Fix [Draft] please", cursor: 11 });
  expect(insertMention("Fix it", text, { start: 3, end: 3 })).toEqual({ value: "Fix [Draft] it", cursor: 11 });
  expect(insertMention("Fix this one", text, { start: 4, end: 8 })).toEqual({ value: "Fix [Draft] one", cursor: 11 });
  expect(insertMention("Fix", text, null)).toEqual({ value: "Fix [Draft]", cursor: 11 });
  expect(insertMention("", text, null)).toEqual({ value: "[Draft]", cursor: 7 });
  expect(insertMention("Fix\n", text, null)).toEqual({ value: "Fix\n[Draft]", cursor: 11 });
});

test("the state of a conversation's draft in words", () => {
  expect(stateLabel("open")).toBe("Draft");
  expect(stateLabel("published")).toBe("Published");
  expect(stateLabel("discarded")).toBe("Discarded");
  expect(stateLabel("replaced")).toBe("Replaced");
  expect(stateLabel(null)).toBe("Deleted");
});

test("document writes count the results of new_document, update_document and edit_document calls", () => {
  const events = [
    event(1, "tool_call", { name: "get_document", call_id: "a" }),
    event(2, "tool_result", { call_id: "a", preview: "{}" }),
    event(3, "tool_call", { name: "new_document", call_id: "b" }),
    event(4, "tool_call", { name: "update_document", call_id: "c" }),
    event(5, "tool_result", { call_id: "c", preview: "Saved draft" }),
    event(6, "tool_result", { call_id: "b", preview: "Created" }),
    event(7, "tool_call", { name: "edit_document" }), // no call id: its result follows it
    event(8, "tool_result", { preview: "Saved draft" }),
    event(9, "message", { text: "Done." }),
  ];
  expect(documentWrites(events.slice(0, 4))).toBe(0);
  expect(documentWrites(events.slice(0, 5))).toBe(1);
  expect(documentWrites(events)).toBe(3);
});

test("a conversation's draft opens its draft page while open, its document once published, nothing once gone", () => {
  const draft = { id: "d1", title: "T", draft_of: null, draft_of_title: null, updated_at: "x" };
  expect(sessionDraftPath({ ...draft, state: "open" })).toBe("/documents/drafts/d1");
  expect(sessionDraftPath({ ...draft, state: "published" })).toBe("/documents/d1");
  expect(sessionDraftPath({ ...draft, draft_of: "doc-1", state: "published" })).toBe("/documents/doc-1");
  expect(sessionDraftPath({ ...draft, state: "discarded" })).toBeNull();
  expect(sessionDraftPath({ ...draft, state: null })).toBeNull();
  expect(mentionable({ ...draft, state: "open" })).toBe(true);
  expect(mentionable({ ...draft, state: "published" })).toBe(true); // a new document keeps the draft's id
  expect(mentionable({ ...draft, draft_of: "doc-1", state: "published" })).toBe(false);
  expect(mentionable({ ...draft, state: "replaced" })).toBe(false);
});
