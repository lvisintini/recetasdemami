#!/usr/bin/env python3
"""
build_docs.py
=============

Walks a source tree of Jekyll/GitHub-Pages markdown docs (depth-first,
recursive) and builds a fresh copy into a target directory:

  * every directory's index.md gets a {toc} block replaced with a
    hierarchical (nested) list of links to every page under it
  * every file's {nav} placeholder is replaced with a breadcrumb trail
    ("Home / Section / Current Page", last item not linked)
  * in-body references like  [page_link](14179fca-0bc7-4181-ba7d-7cdb16fd296c)
    are resolved by uuid: the link text becomes the target page's
    page_title and the uuid becomes a relative link to that page
  * non-markdown files (images, etc.) are copied through unchanged
  * the target directory is wiped and rebuilt each run, so it's safe to
    run this repeatedly as the source content changes

Front matter is expected at the top of every .md file, e.g.:

    ---
    slug: installation
    uuid: 14179fca-0bc7-4181-ba7d-7cdb16fd296c
    page_title: Installation
    ---

Usage:
    python build_docs.py SOURCE_DIR TARGET_DIR [--nav-sep " / "]
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

import yaml

UUID_PATTERN = (
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
UUID_RE = re.compile(UUID_PATTERN)
UUID_LINK_RE = re.compile(r"\[([^\]]*)\]\(\s*(" + UUID_PATTERN + r")\s*\)")

# Front matter block: from the leading "---" line to the closing "---" line,
# inclusive. Captured as a whole so we can re-emit it byte-for-byte.
FRONTMATTER_RE = re.compile(r"^---[ \t]*\n.*?\n---[ \t]*\n?", re.DOTALL)


# --------------------------------------------------------------------------
# Data model
# --------------------------------------------------------------------------

class Page:
    """A single markdown page (front matter + its location in the tree)."""

    def __init__(self, src_path: Path, rel_path: Path, frontmatter: dict):
        self.src_path = src_path
        self.rel_path = rel_path  # path relative to the source/target root
        self.frontmatter = frontmatter or {}
        self.uuid = str(self.frontmatter.get("uuid") or "").strip()
        self.slug = self.frontmatter.get("slug") or rel_path.stem
        self.title = self.frontmatter.get("page_title") or self.slug
        self.dir_node: "Dir" | None = None  # set by the scanner


class Dir:
    """A directory: its own index page, its regular pages, and subdirs."""

    def __init__(self, src_path: Path, rel_path: Path):
        self.src_path = src_path
        self.rel_path = rel_path
        self.index_page: Page | None = None
        self.pages: list[Page] = []
        self.subdirs: list["Dir"] = []
        self.parent: "Dir" | None = None


# --------------------------------------------------------------------------
# Front matter helpers
# --------------------------------------------------------------------------

def split_frontmatter(text: str) -> tuple[str, dict, str]:
    """Return (raw_frontmatter_block, parsed_dict, body)."""
    m = FRONTMATTER_RE.match(text)
    if not m:
        return "", {}, text
    raw = m.group(0)
    inner = raw.strip()
    inner = inner[3:-3] if inner.endswith("---") else inner[3:]
    try:
        data = yaml.safe_load(inner) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"Bad YAML front matter: {exc}") from exc
    return raw, data, text[m.end():]


# --------------------------------------------------------------------------
# Pass 1 (recursive, depth-first): scan the source tree
# --------------------------------------------------------------------------

def scan_dir(root: Path, dir_path: Path, uuid_map: dict, warnings: list) -> Dir:
    node = Dir(dir_path, dir_path.relative_to(root))

    for entry in sorted(dir_path.iterdir(), key=lambda p: p.name.lower()):
        if entry.is_dir():
            child = scan_dir(root, entry, uuid_map, warnings)  # <-- recursion
            child.parent = node
            node.subdirs.append(child)
        elif entry.suffix.lower() == ".md":
            text = entry.read_text(encoding="utf-8")
            _, fm, _ = split_frontmatter(text)
            page = Page(entry, entry.relative_to(root), fm)
            page.dir_node = node

            if entry.name == "index.md":
                node.index_page = page
            else:
                node.pages.append(page)
                if page.slug == "index":
                    warnings.append(
                        f"non-index page has slug 'index', which will collide "
                        f"with its directory's URL: {page.rel_path}"
                    )

            if page.uuid:
                if page.uuid in uuid_map:
                    warnings.append(
                        f"duplicate uuid {page.uuid}: "
                        f"{uuid_map[page.uuid].rel_path} and {page.rel_path}"
                    )
                uuid_map[page.uuid] = page
            else:
                warnings.append(f"missing uuid in front matter: {page.rel_path}")

    if node.index_page is None:
        warnings.append(f"missing index.md in directory: {node.rel_path or '.'}")

    node.pages.sort(key=lambda p: p.title.lower())
    node.subdirs.sort(
        key=lambda d: (d.index_page.title if d.index_page else d.rel_path.name).lower()
    )
    return node


# --------------------------------------------------------------------------
# Link helpers
#
# Links are built from front-matter `slug` values (not relative file
# paths), on the assumption that slug == directory/file name (minus
# extension). A directory's index.md IS that directory's page: it never
# gets its own path segment, and "index"/".md" never appear in a link.
# Every generated link is site-root-relative, prefixed with
# "{{ site.baseurl }}/", so Jekyll can rewrite it correctly.
# --------------------------------------------------------------------------

def ancestor_chain(dir_node: Dir) -> list[Dir]:
    """Root-first list of directories from the tree root down to dir_node."""
    chain = []
    node = dir_node
    while node is not None:
        chain.append(node)
        node = node.parent
    chain.reverse()
    return chain


def page_url(page: Page) -> str:
    """Site-root-relative URL for a page, e.g. '/getting-started/config/'.

    Built from directory names for the nesting, plus the page's own slug
    for non-index pages. A directory's index.md never contributes its own
    slug (index.md files all have slug: index in front matter, which is
    not a real path segment) -- the directory name IS its segment.
    """
    dir_node = page.dir_node
    is_index = dir_node.index_page is page

    segments = []
    for d in ancestor_chain(dir_node):
        if d.parent is None:
            continue  # tree root contributes no segment
        segments.append(d.rel_path.name)  # directory name, never index.md's slug

    if not is_index:
        segments.append(page.slug)

    if not segments:
        return "/"

    url = "/" + "/".join(segments)
    return url + "/" if is_index else url


def site_link(page: Page) -> str:
    """Full Jekyll-ready link: '{{ site.baseurl }}/getting-started/config/'."""
    return "{{ site.baseurl }}" + page_url(page)


# --------------------------------------------------------------------------
# {nav} breadcrumb
# --------------------------------------------------------------------------

def build_nav(page: Page, dir_node: Dir, is_index: bool, sep: str) -> str:
    chain = ancestor_chain(dir_node)
    # if this page IS the directory's own index, don't list that dir twice
    ancestor_dirs = chain[:-1] if is_index else chain

    parts = []
    for d in ancestor_dirs:
        if d.index_page is None:
            continue
        parts.append(f"[{d.index_page.title}]({site_link(d.index_page)})")

    parts.append(page.title)  # current page: plain text, not a link
    return sep.join(parts)


# --------------------------------------------------------------------------
# {toc} hierarchical listing (recursive, depth-first)
# --------------------------------------------------------------------------

def build_toc(dir_node: Dir, depth: int = 0) -> list[str]:
    lines = []
    pad = "  " * depth

    for page in dir_node.pages:
        lines.append(f"{pad}- [{page.title}]({site_link(page)})")

    for sub in dir_node.subdirs:
        if sub.index_page:
            lines.append(f"{pad}- [{sub.index_page.title}]({site_link(sub.index_page)})")
        else:
            lines.append(f"{pad}- {sub.rel_path.name}")
        lines.extend(build_toc(sub, depth + 1))  # <-- recursion

    return lines


# --------------------------------------------------------------------------
# uuid reference resolution in page bodies
# --------------------------------------------------------------------------

def resolve_uuid_links(body: str, page: Page, uuid_map: dict, warnings: list) -> str:
    def _sub(m: re.Match) -> str:
        target_uuid = m.group(2)
        target = uuid_map.get(target_uuid)
        if target is None:
            warnings.append(
                f"unresolved uuid reference {target_uuid} in {page.rel_path}"
            )
            return m.group(0)  # leave as-is so it's easy to spot in the build
        return f"[{target.title}]({site_link(target)})"

    return UUID_LINK_RE.sub(_sub, body)


# --------------------------------------------------------------------------
# Pass 2 (recursive, depth-first): render + copy into target
# --------------------------------------------------------------------------

def render_page(page: Page, dir_node: Dir, is_index: bool, target_root: Path,
                 uuid_map: dict, warnings: list, nav_sep: str) -> None:
    text = page.src_path.read_text(encoding="utf-8")
    raw_fm, _, body = split_frontmatter(text)

    body = resolve_uuid_links(body, page, uuid_map, warnings)
    body = body.replace("{nav}", build_nav(page, dir_node, is_index, nav_sep))

    if is_index:
        toc_lines = build_toc(dir_node)
        toc_text = "\n".join(toc_lines) if toc_lines else "_No pages yet._"
        body = body.replace("{toc}", toc_text)

    out_path = target_root / page.rel_path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(raw_fm + body, encoding="utf-8")


def render_dir(dir_node: Dir, target_root: Path, uuid_map: dict,
                warnings: list, nav_sep: str) -> None:
    out_dir = target_root / dir_node.rel_path
    out_dir.mkdir(parents=True, exist_ok=True)

    if dir_node.index_page:
        render_page(dir_node.index_page, dir_node, True, target_root, uuid_map,
                    warnings, nav_sep)
    for page in dir_node.pages:
        render_page(page, dir_node, False, target_root, uuid_map, warnings, nav_sep)

    # copy non-markdown files (images, etc.) through unchanged
    for entry in dir_node.src_path.iterdir():
        if entry.is_file() and entry.suffix.lower() != ".md":
            shutil.copy2(entry, out_dir / entry.name)

    for sub in dir_node.subdirs:
        render_dir(sub, target_root, uuid_map, warnings, nav_sep)  # <-- recursion


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

def build_docs(source_dir: str, target_dir: str, nav_sep: str = " / "):
    source_root = Path(source_dir).resolve()
    target_root = Path(target_dir).resolve()

    if not source_root.is_dir():
        raise NotADirectoryError(f"source directory not found: {source_root}")

    warnings: list[str] = []
    uuid_map: dict[str, Page] = {}

    tree = scan_dir(source_root, source_root, uuid_map, warnings)

    # clean slate for the target, every run
    if target_root.exists():
        shutil.rmtree(target_root)
    target_root.mkdir(parents=True, exist_ok=True)

    render_dir(tree, target_root, uuid_map, warnings, nav_sep)

    if warnings:
        print(f"Build finished with {len(warnings)} warning(s):", file=sys.stderr)
        for w in warnings:
            print(f"  - {w}", file=sys.stderr)
    else:
        print("Build finished with no warnings.")

    return tree, uuid_map, warnings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", help="source directory containing the docs")
    parser.add_argument("target", help="target directory to (re)build into")
    parser.add_argument(
        "--nav-sep", default=" / ",
        help='separator for breadcrumb nav, e.g. " / " or " > " (default: " / ")'
    )
    args = parser.parse_args()
    build_docs(args.source, args.target, args.nav_sep)


if __name__ == "__main__":
    main()
