"""Front matter, quadrant rules, HTML rendering, and heading slugs.

Moved verbatim off the old single-module indexing pipeline (store-package
refactor, see `docs/superpowers/plans/2026-08-04-rag-store-package.md`):
these are pure text-in/text-out helpers with no Store/SQL/IO dependency of
their own, shared by `rag.ingest` (front matter + rendering, on the
per-doc indexing path) and `rag.anchors` (`_dedupe_slug`/`unique_slugs`,
so the anchor id sequence and `render_html`'s own `id="..."` slugs can
never drift apart -- one dedupe rule, two callers).
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml
from markdown_it import MarkdownIt
from markdown_it.common.utils import escapeHtml
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import get_lexer_by_name
from pygments.util import ClassNotFound

# terminology used by diataxis and docs-wiki: "quadrant" is the top-level category of a doc, i.e.
# the first path component under the docs root. The four diataxis quadrants are how to, reference, explanation, and tutorials; docs-wiki adds index and other for top-level files and any unrecognized first path component.
QUADRANTS = ("how-to", "reference", "explanation", "tutorials")

_FRONT_MATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def parse_front_matter(text: str) -> tuple[dict, str]:
    match = _FRONT_MATTER_RE.match(text)
    if not match:
        return {}, text
    try:
        meta = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError:
        meta = {}
    if not isinstance(meta, dict):
        meta = {}
    return meta, text[match.end():]


def quadrant_for(rel_path: Path) -> str:
    parts = rel_path.parts
    if len(parts) == 1:
        return "index"
    return parts[0] if parts[0] in QUADRANTS else "other"


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "section"


def _dedupe_slug(used: dict[str, int], text: str) -> str:
    """Slugify `text` and disambiguate against slugs already seen in `used`
    (mutated in place). Shared by render_html's heading ids and the TOC
    builder in webapp.py so the two can never drift apart."""
    slug = slugify(text)
    count = used.get(slug, 0) + 1
    used[slug] = count
    return slug if count == 1 else f"{slug}-{count}"


def unique_slugs(labels: list[str]) -> list[str]:
    """Batch form of `_dedupe_slug`: dedupe a whole ordered heading
    sequence in one call, first-seen order, same -2/-3 suffix rule."""
    used: dict[str, int] = {}
    return [_dedupe_slug(used, label) for label in labels]


def render_html(body: str) -> str:
    md = MarkdownIt("gfm-like")
    used_slugs: dict[str, int] = {}

    def unique_slug(text: str) -> str:
        return _dedupe_slug(used_slugs, text)

    # NOTE (library deviation): MarkdownIt.add_render_rule binds the given
    # callable to the renderer instance via `function.__get__(self.renderer)`
    # (markdown_it/main.py), i.e. it becomes a *bound method* of the
    # renderer. That means the callable must accept a leading `self`
    # parameter -- with only (tokens, idx, options, env) as in the brief,
    # markdown-it-py 4.2.0 raises
    # "TypeError: fence() takes 4 positional arguments but 5 were given".
    # See task-2-report.md Library deviations section.
    def fence(self, tokens, idx, options, env):
        token = tokens[idx]
        info = (token.info or "").strip()
        lang = info.split()[0] if info else ""
        lexer = None
        if lang:
            try:
                lexer = get_lexer_by_name(lang)
            except ClassNotFound:
                lexer = None
        if lexer is None:
            return f"<pre><code>{escapeHtml(token.content)}</code></pre>\n"
        return highlight(token.content, lexer, HtmlFormatter())

    def heading_open(self, tokens, idx, options, env):
        token = tokens[idx]
        inline = tokens[idx + 1]
        return f'<{token.tag} id="{unique_slug(inline.content)}">'

    md.add_render_rule("fence", fence)
    md.add_render_rule("heading_open", heading_open)
    return md.render(body)
