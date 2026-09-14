# Chunk contract: authoring rules and feedback loop

Documentation authored for the `rag` indexing service must follow a contract that enables deep-linking by heading, full-text search, and hybrid embedding-based retrieval. Every H2 or H3 section is indexed as a distinct unit (a "chunk") and becomes retrievable and link-target. This guide defines the authoring rules that keep chunks self-contained, searchable, and within the indexer's efficient processing bounds.

## Authoring rules

### Sections are self-contained

Each H2 or H3 section must stand alone — a reader encountering it in search results, via an anchor link, or in a navigation flow should understand its full intent and content without back-references to "as mentioned above" or "see the previous section." If your section relies on context established earlier, either repeat the essential context inline or restructure to place both topics under a common parent heading.

**Why:** When a search result or deep link lands the reader in one section, they need that section to be complete. Orphaned references leave readers confused.

### Sections stay under ~1800 characters

The `rag` indexer chunks sections that exceed 2000 characters and reports each split. Aim to keep sections under 1800 characters — this gives headroom for a section to grow slightly without triggering a split warning. A 2000-character cap means that oversized sections become two or more separate chunks during indexing, each retrievable independently; while this is not a failure, it signals that the topic might be better split into focused, distinct H3 subsections authored upfront.

**Why:** Smaller sections retrieve with higher precision, and authoring sections near the boundary forces focus. The ~1800-char target is aggressive — it requires choosing a specific topic per section rather than loose collections.

**How to measure:** Most editors can count characters; the `rag` warning will tell you the exact character count of any section that does split.

### Headings are phrased as questions or intents

Title each H2 or H3 with the question or intent that the section answers. Use direct phrasing: "How do I configure X?", "What does error Y mean?", "When should I use Z?". Avoid vague titles like "Configuration" or "Setup" without an object; instead, be specific: "How to configure authentication", "Setting up the webhook receiver".

**Why:** A reader scanning the table of contents or seeing a heading in search results should immediately know whether that section is what they need. Question-phrased headings are skimmable and precise.

### Key terms appear verbatim in the section

When a section explains an error code, endpoint path, configuration key, environment variable, or command-line flag, include the exact term verbatim — in code formatting — in the section text where it is first explained or used. The `rag` full-text indexer and embedding model use this exact text to match search queries.

**Why:** Search relies on term matching. A reader searching for "API_KEY" or "`/users/{id}`" needs to find the section that explains them by that exact name.

**Example:** If the section title is "How to set the API key", include in the section body: "Set the environment variable `API_KEY` to your secret key" — not "the environment variable" alone.

### One topic per section

Each H2 or H3 covers one topic, one question, one intent. Do not combine related but distinct topics in a single section; instead, create parallel H3 sections under a common H2 parent, or split into sequential H2 sections at the same level.

**Why:** A reader or search result targeting one specific question should not have to skip over unrelated information to find it. Parallel structure (multiple H3 sections under one H2) groups related topics while keeping each retrievable independently.

## Reading the warnings: feedback loop

When the `rag index` command completes, it may print one or more warning lines to stderr. Each warning is a fact already computed during indexing — not a second-pass heuristic — that signals a need for revision. Treat each warning as a review finding: revise the section accordingly, re-run `rag index`, and repeat until the warnings are clean. If a warning describes a violation you intentionally accept (e.g., a long reference table that cannot be split without losing meaning), record that choice in the document's YAML front matter (see "Accepted exceptions" below).

### Section split warning

**Message format:**
```
rag index: warning: <collection>: <path> > '<heading_path>': <chars> chars, split into <n> chunks
```

**Meaning:** The section titled `<heading_path>` contains `<chars>` characters and exceeds the 2000-character chunking boundary. The indexer split it into `<n>` chunks automatically. While the split is applied and indexing succeeds, the section is now retrieval-fragmented.

**Revision:** Either split the section into multiple focused H3 subsections authored upfront (each under 1800 characters), or — if the content truly belongs together — consult whether the section is trying to answer too many questions at once and refactor into smaller, single-intent sections.

### No-headings warning

**Message format:**
```
rag index: warning: <collection>: <path>: no headings -- content not deep-linkable
```

**Meaning:** The document contains no headings at all — neither a document title (H1) nor any H2/H3 subsections. All content indexes under a synthetic `"(intro)"` heading and cannot be anchor-linked directly.

**Revision:** Add a document title (an H1 at the top) and H2 or H3 section headings for major topics. A document with just an H1 title and no H2/H3 subsections does NOT trigger this warning.

### Duplicate heading warning

**Message format:**
```
rag index: warning: <collection>: <path>: duplicate heading '<text>' (x<count>) -- anchors suffixed
```

**Meaning:** The heading text `<text>` appears `<count>` times anywhere in the document — at any level, or even nested inside blockquotes and list items. The indexer suffixes their anchor IDs (`#foo`, `#foo-2`, `#foo-3`, ...) so links still work, but a link to "the heading named 'foo'" without a suffix reaches only the first occurrence.

**Revision:** Rename the duplicate headings to be distinct and specific. For example, if two sections are both titled "Overview" under different parent H2 headings, rename one to "Source overview" and the other to "Target overview". If the repetition is intentional (e.g., a list of similarly-structured items), rewrite the section structure to avoid duplicate heading text — use H3 subsections with distinct titles, or convert to a bulleted/numbered list with inline subheadings instead.

## Accepted exceptions

When you intentionally accept a warning (e.g., a large reference table cannot be restructured without losing value), record your choice in the document's YAML front matter using the `chunk_contract_accepted` key:

```yaml
---
analyzed_commits: ...
updated: ...
chunk_contract_accepted:
  - "API response reference"  # large table, split unavoidable
  - "Error codes by service"  # comprehensive table, essential to keep intact
---
```

Each exception is a string listing the section heading and (in a comment) the reason. This front-matter-native approach:
- Is machine-readable for any future tooling that may consume or validate exceptions.
- Follows the existing front-matter contract (parse_front_matter already tolerates extra keys).
- Respects the plugin's "Plain Markdown, no HTML" rule (guide.md:61).
- Keeps exceptions visible to the `update` workflow, which reads front matter on every run.

Reviewers will see these exceptions during code review and can ask for justification if needed.
