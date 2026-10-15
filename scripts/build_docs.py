#!/usr/bin/env python3
"""
build_docs.py
=============

Walks a source tree of Jekyll/GitHub-Pages markdown docs (depth-first,
recursive) and builds a fresh copy into a target directory:

  * every directory's index.md gets a {toc} block replaced with a
    hierarchical (nested) list of links to every page under it, with
    directories in bold
  * every file's {nav} placeholder is replaced with a breadcrumb trail
    ("Home / Section / Current Page", last item not linked)
  * in-body references like  [page_link](14179fca-0bc7-4181-ba7d-7cdb16fd296c)
    are resolved by uuid: the link text becomes the target page's
    page_title and the uuid becomes a relative link to that page
  * non-markdown files (images, etc.) are copied through unchanged
  * when --edit-base-url is given, every page's front matter gets a
    `custom_edit_url` property injected/overridden with
    edit_base_url + the file's source-relative path -- no template
    placeholder needed, and any pre-existing value is replaced
  * the <SUMMARY> curation placeholder left in the sources is stripped
    from the output
  * a page without `curated: true` in its front matter is pending review:
    it gets a "pending review" note under its {nav}, every generated link
    to it (toc, sidebar, uuid references) a small mark, and the build one
    summary warning with the count
  * tips ("recomendaciones") live as plain markdown files in
    SOURCE/_recomendaciones/<name>.md; a page attaches them by name in its
    `recomendaciones` front matter, and a tip on an index.md applies to the
    whole directory below it. Each tip is written (uuid links resolved) to
    _includes/recomendaciones/<name>.md and the sidebar lists every tip that
    applies to the page, most general first, under the index
  * every directory gets sidebar lists written to _includes/site-toc/<dir>/
    for the site layout to include by page.dir: toc.html (its pages and
    subdirectories, for its regular pages) and index-toc.html (its parent's
    list with it expanded, for its index page)
  * when --site-dir is given, that tree (_config.yml, _layouts/, ...) is
    copied over the output, so Jekyll site files reach the publish branch
  * the target directory is wiped and rebuilt each run, so it's safe to
    run this repeatedly as the source content changes; a target that is,
    contains, or sits inside the source (or site) directory is refused

Front matter is expected at the top of every .md file, e.g.:

    ---
    slug: installation
    uuid: 14179fca-0bc7-4181-ba7d-7cdb16fd296c
    page_title: Installation
    weight: 1          # optional: orders entries before unweighted ones
    curated: false     # true once the page has been reviewed
    recomendaciones: [hinojo]   # optional: tips from _recomendaciones/
    ---

Usage:
    python build_docs.py SOURCE_DIR TARGET_DIR [--nav-sep " / "]
                         [--edit-base-url URL] [--site-dir DIR]
"""

from __future__ import annotations

import argparse
import html
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

# Placeholders left in the sources as manual curation to-dos. Kept in the
# templates, removed from the output (with the blank line that follows).
CURATION_MARKERS = ("<SUMMARY>",)
CURATION_RE = re.compile(
    "(?:" + "|".join(re.escape(m) for m in CURATION_MARKERS) + r")[ \t]*\n?(?:[ \t]*\n)?"
)
# Shown for pages not marked `curated: true`; the icon itself is CSS.
PENDING_LABEL = "Pendiente de revisión"
PENDING_MARK = (f'<span class="pending-mark" role="img" title="{PENDING_LABEL}" '
                f'aria-label="{PENDING_LABEL}"></span>')
PENDING_NOTE = f'<p class="pending-note">{PENDING_LABEL}</p>'


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
        weight = self.frontmatter.get("weight")  # optional explicit sort order
        valid = isinstance(weight, (int, float)) and not isinstance(weight, bool)
        self.weight = weight if valid else None
        self.bad_weight = weight is not None and not valid
        curated = self.frontmatter.get("curated")  # reviewed by hand
        self.pending = curated is not True
        self.bad_curated = curated is not None and not isinstance(curated, bool)
        recs = self.frontmatter.get("recomendaciones")  # tip names, see load_recs
        recs = [recs] if isinstance(recs, str) else recs
        ok = recs is None or (isinstance(recs, list) and all(isinstance(r, str) for r in recs))
        self.recs = list(recs) if recs and ok else []
        self.bad_recs = not ok
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


def natural_key(text: str) -> list:
    """Sort key that compares digit runs as numbers: '2. B' < '10. A'."""
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", text)]


def order_key(page: "Page | None", fallback: str) -> tuple:
    """Weighted entries first (ascending), then the rest by natural title."""
    weight = page.weight if page else None
    title = str(page.title) if page else fallback
    return (weight is None, weight or 0, natural_key(title))


# --------------------------------------------------------------------------
# Pass 1 (recursive, depth-first): scan the source tree
# --------------------------------------------------------------------------

def scan_dir(root: Path, dir_path: Path, uuid_map: dict, warnings: list) -> Dir:
    node = Dir(dir_path, dir_path.relative_to(root))

    for entry in sorted(dir_path.iterdir(), key=lambda p: p.name.lower()):
        if entry.is_dir():
            if entry.name.startswith(("_", ".")):
                continue  # not pages: _recomendaciones/, .git/, ...
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

            if page.bad_weight:
                warnings.append(
                    f"non-numeric weight {page.frontmatter['weight']!r} ignored: "
                    f"{page.rel_path}"
                )
            if page.bad_curated:
                warnings.append(
                    f"non-boolean curated {page.frontmatter['curated']!r} treated as "
                    f"false: {page.rel_path}"
                )
            if page.bad_recs:
                warnings.append(
                    f"recomendaciones must be a list of names, ignored: {page.rel_path}"
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

    node.pages.sort(key=lambda p: order_key(p, p.rel_path.stem))
    node.subdirs.sort(key=lambda d: order_key(d.index_page, d.rel_path.name))
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


def pending_mark(page: Page) -> str:
    """Mark placed after every generated link to a page not curated yet."""
    return PENDING_MARK if page.pending else ""


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
        lines.append(f"{pad}- [{page.title}]({site_link(page)}){pending_mark(page)}")

    for sub in dir_node.subdirs:
        if sub.index_page:  # directories (non-leaf entries) in bold
            lines.append(f"{pad}- **[{sub.index_page.title}]({site_link(sub.index_page)})**"
                         f"{pending_mark(sub.index_page)}")
        else:
            lines.append(f"{pad}- **{sub.rel_path.name}**")
        lines.extend(build_toc(sub, depth + 1))  # <-- recursion

    return lines


# --------------------------------------------------------------------------
# Sidebar index: _includes/site-toc/<dir>/{toc,index-toc}.html (recursive)
#
# Each directory gets two lists (pages, then subdirectories in bold),
# under a heading linking the root index and the trail of sections from the
# root down to the listed directory. The layout picks one by page.dir:
#   * toc.html, for regular pages: the directory's own children, i.e. the
#     page's siblings. Liquid marks the page being rendered by exact URL;
#     `here` is page.url sans ".html".
#   * index-toc.html, for the directory's index page: the parent's list with
#     this directory marked current and its own children listed right under
#     it (the root has no parent, so it gets its own list).
# --------------------------------------------------------------------------

SIDEBAR_DIR = Path("_includes") / "site-toc"
CURRENT_PAGE = "{{% if here == '{}' %}} class=\"current\"{{% endif %}}"


def link_html(page: Page, attrs: str = "") -> str:
    return (f'<a href="{site_link(page)}"{attrs}>{html.escape(str(page.title))}</a>'
            + pending_mark(page))


def sidebar_list(dir_node: Dir, open_dir: Dir | None = None) -> list[str]:
    items = [f"<li>{link_html(p, CURRENT_PAGE.format(page_url(p)))}</li>"
             for p in dir_node.pages]
    for sub in dir_node.subdirs:
        if sub.index_page is None:
            continue
        if sub is open_dir:
            items += [f'<li><strong>{link_html(sub.index_page, " class=\"current\"")}</strong>',
                      *sidebar_list(sub), "</li>"]  # <-- recursion, one level
        else:
            items.append(f"<li><strong>{link_html(sub.index_page)}</strong></li>")
    return ['<ul class="site-toc">', *items, "</ul>"] if items else []


def sidebar_trail(trail: list[Dir], inner: list[str]) -> list[str]:
    """Nest `inner` under each section of `trail` (root-first), one entry per level."""
    for d in reversed(trail):
        if d.index_page:
            inner = ['<ul class="site-toc">', f"<li><strong>{link_html(d.index_page)}</strong>",
                     *inner, "</li>", "</ul>"]
    return inner


def build_sidebar(dir_node: Dir, open_dir: Dir | None = None) -> list[str]:
    root, *trail = ancestor_chain(dir_node)
    lines = ["{% assign here = page.url | remove: '.html' %}"]
    if root.index_page:
        lines.append(f'<p class="site-toc-title">{link_html(root.index_page)}</p>')
    return lines + sidebar_trail(trail, sidebar_list(dir_node, open_dir))


def sidebar_recs(base: list[str], pages: list[Page]) -> list[str]:
    """Liquid picking the rendered page's tips (by exact URL) for site-recs.html."""
    own = [(p, merge_recs(base, p.recs)) for p in pages if p.recs]
    if not base and not own:
        return []
    lines = [f'{{% assign recs = "{",".join(base)}" %}}']
    lines += [f"{{% if here == '{page_url(p)}' %}}"
              f'{{% assign recs = "{",".join(recs)}" %}}{{% endif %}}' for p, recs in own]
    return lines + ["{% include site-recs.html names=recs %}"]


def write_sidebar(dir_node: Dir, target_root: Path) -> None:
    out_dir = target_root / SIDEBAR_DIR / dir_node.rel_path
    out_dir.mkdir(parents=True, exist_ok=True)
    own = build_sidebar(dir_node)
    in_parent = build_sidebar(dir_node.parent, dir_node) if dir_node.parent else own
    recs = dir_recs(dir_node)
    own += sidebar_recs(recs, dir_node.pages)
    in_parent += sidebar_recs(recs, [])
    (out_dir / "toc.html").write_text("\n".join(own) + "\n", encoding="utf-8")
    (out_dir / "index-toc.html").write_text("\n".join(in_parent) + "\n", encoding="utf-8")
    for sub in dir_node.subdirs:
        write_sidebar(sub, target_root)  # <-- recursion


# --------------------------------------------------------------------------
# Recomendaciones: tips in SOURCE/_recomendaciones/<name>.md, attached by name
# in a page's `recomendaciones` front matter. A tip on a directory's index.md
# applies to everything under it. The sidebar includes them (see
# site/_includes/site-recs.html) from _includes/recomendaciones/<name>.md.
# --------------------------------------------------------------------------

REC_DIR = "_recomendaciones"
REC_INCLUDES = Path("_includes") / "recomendaciones"


def load_recs(source_root: Path) -> dict[str, str]:
    rec_dir = source_root / REC_DIR
    if not rec_dir.is_dir():
        return {}
    return {p.stem: p.read_text(encoding="utf-8") for p in sorted(rec_dir.glob("*.md"))}


def merge_recs(*lists: list[str]) -> list[str]:
    """Concatenate tip-name lists, dropping repeats but keeping first-seen order."""
    return list(dict.fromkeys(name for names in lists for name in names))


def dir_recs(dir_node: Dir) -> list[str]:
    """Tips for a directory: its ancestors' (root first), then its index.md's."""
    inherited = dir_recs(dir_node.parent) if dir_node.parent else []  # <-- recursion
    return merge_recs(inherited, dir_node.index_page.recs if dir_node.index_page else [])


def check_recs(dir_node: Dir, recs: dict, warnings: list, used: set) -> None:
    """Warn about (and drop) unknown tip names; collect the names in use."""
    pages = [dir_node.index_page] if dir_node.index_page else []
    for page in pages + dir_node.pages:
        for name in page.recs:
            if name not in recs:
                warnings.append(f"unknown recomendacion {name!r} in {page.rel_path}")
        page.recs = [name for name in page.recs if name in recs]
        used.update(page.recs)
    for sub in dir_node.subdirs:
        check_recs(sub, recs, warnings, used)  # <-- recursion


def write_recs(recs: dict, target_root: Path, uuid_map: dict, warnings: list) -> None:
    out_dir = target_root / REC_INCLUDES
    for name, text in recs.items():
        out_dir.mkdir(parents=True, exist_ok=True)
        text = resolve_uuid_links(text, f"{REC_DIR}/{name}.md", uuid_map, warnings)
        (out_dir / f"{name}.md").write_text(text, encoding="utf-8")


# --------------------------------------------------------------------------
# uuid reference resolution in page bodies
# --------------------------------------------------------------------------

def resolve_uuid_links(body: str, where: Path | str, uuid_map: dict, warnings: list) -> str:
    def _sub(m: re.Match) -> str:
        target_uuid = m.group(2)
        target = uuid_map.get(target_uuid)
        if target is None:
            warnings.append(f"unresolved uuid reference {target_uuid} in {where}")
            return m.group(0)  # leave as-is so it's easy to spot in the build
        return f"[{target.title}]({site_link(target)}){pending_mark(target)}"

    return UUID_LINK_RE.sub(_sub, body)


def strip_curation_markers(body: str) -> tuple[str, int]:
    """Remove manual to-do markers from the output body; return (body, count)."""
    return CURATION_RE.subn("", body)


# --------------------------------------------------------------------------
# custom_edit_url -- "Improve this page" front-matter property
#
# Always computed as edit_base_url + the file's source-relative path, and
# always overrides whatever (if anything) was already in a page's front
# matter. Templates never need to reference it directly; it's injected
# straight into the output front matter for downstream Jekyll/theme use.
# --------------------------------------------------------------------------

def compute_edit_url(page: Page, edit_base_url: str) -> str:
    return edit_base_url.rstrip("/") + "/" + page.rel_path.as_posix()


# --------------------------------------------------------------------------
# Pass 2 (recursive, depth-first): render + copy into target
# --------------------------------------------------------------------------

def render_page(page: Page, dir_node: Dir, is_index: bool, target_root: Path,
                 uuid_map: dict, warnings: list, nav_sep: str,
                 edit_base_url: str | None) -> bool:
    """Render one page; return True if it is still pending curation."""
    text = page.src_path.read_text(encoding="utf-8")
    raw_fm, fm_dict, body = split_frontmatter(text)

    body, marker_count = strip_curation_markers(body)
    if marker_count and not page.pending:
        warnings.append(f"curated page still has {', '.join(CURATION_MARKERS)}: "
                        f"{page.rel_path}")
    body = resolve_uuid_links(body, page.rel_path, uuid_map, warnings)
    nav = build_nav(page, dir_node, is_index, nav_sep)
    if page.pending:  # "pending review" note right under the breadcrumb, or on top
        if "{nav}" in body:
            nav += f"\n\n{PENDING_NOTE}"
        else:
            body = f"{PENDING_NOTE}\n\n{body}"
    body = body.replace("{nav}", nav)

    if is_index:
        toc_lines = build_toc(dir_node)
        toc_text = "\n".join(toc_lines) if toc_lines else "_No pages yet._"
        body = body.replace("{toc}", toc_text)

    if edit_base_url:
        # override in place (or append) -- computed value always wins
        fm_dict["custom_edit_url"] = compute_edit_url(page, edit_base_url)
        # the theme's <title> (jekyll-seo-tag) reads `title`, not `page_title`
        if "title" not in fm_dict and "page_title" in fm_dict:
            fm_dict["title"] = fm_dict["page_title"]
        new_yaml = yaml.safe_dump(
            fm_dict, sort_keys=False, default_flow_style=False, allow_unicode=True
        )
        raw_fm = f"---\n{new_yaml}---\n"
    elif "custom_edit_url" not in fm_dict:
        warnings.append(
            f"no --edit-base-url set and {page.rel_path} has no custom_edit_url "
            f"of its own; front matter left unchanged"
        )

    out_path = target_root / page.rel_path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(raw_fm + body, encoding="utf-8")
    return page.pending


def render_dir(dir_node: Dir, target_root: Path, uuid_map: dict,
                warnings: list, nav_sep: str, edit_base_url: str | None) -> int:
    """Render a directory tree; return how many pages are pending curation."""
    out_dir = target_root / dir_node.rel_path
    out_dir.mkdir(parents=True, exist_ok=True)

    pending = 0
    if dir_node.index_page:
        pending += render_page(dir_node.index_page, dir_node, True, target_root,
                               uuid_map, warnings, nav_sep, edit_base_url)
    for page in dir_node.pages:
        pending += render_page(page, dir_node, False, target_root, uuid_map,
                               warnings, nav_sep, edit_base_url)

    # copy non-markdown files (images, etc.) through unchanged
    for entry in dir_node.src_path.iterdir():
        if entry.is_file() and entry.suffix.lower() != ".md":
            shutil.copy2(entry, out_dir / entry.name)

    for sub in dir_node.subdirs:
        pending += render_dir(sub, target_root, uuid_map, warnings, nav_sep,
                              edit_base_url)  # <-- recursion
    return pending


# --------------------------------------------------------------------------
# Site files (_config.yml, _layouts/, ...) copied over the output
# --------------------------------------------------------------------------

def copy_site_dir(site_root: Path, target_root: Path, warnings: list) -> None:
    for entry in sorted(site_root.rglob("*")):
        if not entry.is_file():
            continue
        dest = target_root / entry.relative_to(site_root)
        if dest.exists():
            warnings.append(
                f"site file overrides a generated file: {dest.relative_to(target_root)}"
            )
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(entry, dest)


def check_target(protected: Path, target_root: Path) -> None:
    """Refuse a target that is, contains, or sits inside a protected dir."""
    if protected.is_relative_to(target_root) or target_root.is_relative_to(protected):
        raise ValueError(
            f"target {target_root} overlaps {protected}; refusing to wipe it"
        )


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

def build_docs(source_dir: str, target_dir: str, nav_sep: str = " / ",
               edit_base_url: str | None = None, site_dir: str | None = None):
    source_root = Path(source_dir).resolve()
    target_root = Path(target_dir).resolve()
    site_root = Path(site_dir).resolve() if site_dir else None

    if not source_root.is_dir():
        raise NotADirectoryError(f"source directory not found: {source_root}")
    if site_root and not site_root.is_dir():
        raise NotADirectoryError(f"site directory not found: {site_root}")
    for protected in filter(None, (source_root, site_root)):
        check_target(protected, target_root)

    warnings: list[str] = []
    uuid_map: dict[str, Page] = {}

    tree = scan_dir(source_root, source_root, uuid_map, warnings)
    recs = load_recs(source_root)
    used: set[str] = set()
    check_recs(tree, recs, warnings, used)
    warnings += [f"unused recomendacion: {name}" for name in recs if name not in used]

    # clean slate for the target, every run
    if target_root.exists():
        shutil.rmtree(target_root)
    target_root.mkdir(parents=True, exist_ok=True)

    pending = render_dir(tree, target_root, uuid_map, warnings, nav_sep, edit_base_url)
    write_recs(recs, target_root, uuid_map, warnings)
    write_sidebar(tree, target_root)
    if pending:
        warnings.append(f"{pending} page(s) not curated yet (curated: true missing)")

    if site_root:
        copy_site_dir(site_root, target_root, warnings)

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
    parser.add_argument(
        "--edit-base-url", default=None,
        help=(
            "Base URL used to compute each page's custom_edit_url front-matter "
            "property, e.g. 'https://github.com/org/repo/edit/main/docs' -- the "
            "page's source-relative path is appended to this and always "
            "overrides any custom_edit_url already present in the source file."
        ),
    )
    parser.add_argument(
        "--site-dir", default=None,
        help=(
            "Directory of Jekyll site files (_config.yml, _layouts/, ...) copied "
            "over the output after the build, e.g. 'site'."
        ),
    )
    args = parser.parse_args()
    build_docs(args.source, args.target, args.nav_sep, args.edit_base_url,
               args.site_dir)


if __name__ == "__main__":
    main()

