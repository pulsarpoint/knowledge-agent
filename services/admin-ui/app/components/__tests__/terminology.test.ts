import { expect, test } from "vitest";

/** Every app source file (tests excluded), as text. */
const sources = Object.entries(
  import.meta.glob(["../../**/*.{ts,tsx}", "!../../**/__tests__/**", "!../../**/*.test.{ts,tsx}"], {
    query: "?raw",
    import: "default",
    eager: true,
  }) as Record<string, string>,
);

/** The lines of `text` a person can read in the UI: quoted sentences or bare JSX text, never comments or code. */
function visibleLines(text: string, word: RegExp): string[] {
  return text.split("\n").filter((line) => {
    const trimmed = line.trim();
    if (/^(\/\/|\/\*|\*)/.test(trimmed)) return false;
    // Expressions inside text (`${…}`) and JSX comments are code.
    const text = trimmed.replace(/\$\{[^{}]*\}/g, "").replace(/\{\/\*.*?\*\/\}/g, "");
    // A quoted text with the word: a single word starting with a capital, or a sentence (it has a space).
    const quoted = [...text.matchAll(/(["'`])((?:(?!\1).)*)\1/g)].some(
      ([, , inner]) => word.test(inner) && (/\s/.test(inner) || /^[A-Z]/.test(inner)),
    );
    // Bare JSX text: no code punctuation, not a declaration or a list item.
    const jsxText = text.replace(/\{[^{}]*\}/g, " ");
    const bare =
      !/^(import|export|type|const|let|function|interface)\b/.test(jsxText) &&
      !/[,;]$/.test(jsxText) &&
      /^[^<>{}=;:()"'`]*$/.test(jsxText) &&
      word.test(jsxText);
    // JSX text between tags on one line: <p>Some text</p>.
    const between = [...jsxText.matchAll(/>([^<>]+)</g)].some(([, inner]) => word.test(inner));
    return quoted || bare || between;
  });
}

test("documents go live with Publish: no Promote is left in the UI", () => {
  const found = sources.flatMap(([path, text]) =>
    visibleLines(text, /\b[Pp]romot/).map((line) => `${path}: ${line.trim()}`),
  );
  expect(found).toEqual([]);
});

test("the chat with an agent is a chat: no session or conversation is left in the chat pages' words", () => {
  const chatPages = sources.filter(([path]) =>
    /\/(SessionView|AgentChat|ChatColumn|ResourceChats|RunWorkspace|AgentSettingsSheet|DebugPanel|NewDocumentAgent|ReviewSheet)\.tsx$/.test(
      path,
    ),
  );
  expect(chatPages.length).toBe(9);
  const found = chatPages.flatMap(([path, text]) =>
    visibleLines(text, /\b([Ss]ession|[Cc]onversation)s?\b/).map((line) => `${path}: ${line.trim()}`),
  );
  expect(found).toEqual([]);
});
