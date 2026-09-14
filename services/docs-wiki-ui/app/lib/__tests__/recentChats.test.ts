import { beforeEach, expect, test } from "vitest";
import { addRecent, listRecent, removeRecent } from "../recentChats";

beforeEach(() => localStorage.clear());

test("adds newest-first, dedupes by key, caps at 50", () => {
  for (let i = 0; i < 55; i++) addRecent("shop", { key: `k${i}`, firstQuestion: `q${i}`, ts: i });
  const list = listRecent("shop");
  expect(list).toHaveLength(50);
  expect(list[0].key).toBe("k54");
  addRecent("shop", { key: "k54", firstQuestion: "changed", ts: 99 });
  expect(listRecent("shop")).toHaveLength(50);
  expect(listRecent("shop")[0].firstQuestion).toBe("changed");
});

test("remove and per-app isolation", () => {
  addRecent("shop", { key: "a", firstQuestion: "q", ts: 1 });
  addRecent("billing", { key: "b", firstQuestion: "q", ts: 1 });
  removeRecent("shop", "a");
  expect(listRecent("shop")).toEqual([]);
  expect(listRecent("billing")).toHaveLength(1);
});

test("corrupt storage returns empty, does not throw", () => {
  localStorage.setItem("docs-wiki.recent-chats.shop", "{not json");
  expect(listRecent("shop")).toEqual([]);
});
