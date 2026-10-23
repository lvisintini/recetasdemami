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
    _includes/recomendaciones/<name>.md, and every directory gets
    _includes/page-recs/<dir>/recs.html: an end-of-page callout with the
    tips that apply to the page, grouped by where they come from (most
    general first), for the site layout to include by page.dir
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
import unicodedata
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


def fold_accents(text: str) -> str:
    """Lowercase without accents, Spanish order: 'Brócoli' ~ 'brocoli', ñ between n and o."""
    text = text.lower().replace("ñ", "n\x7f")
    return "".join(c for c in unicodedata.normalize("NFD", text) if not unicodedata.combining(c))


def natural_key(text: str) -> list:
    """Sort key that compares digit runs as numbers: '2. B' < '10. A'."""
    return [int(t) if t.isdigit() else fold_accents(t) for t in re.split(r"(\d+)", text)]


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


def write_sidebar(dir_node: Dir, target_root: Path) -> None:
    out_dir = target_root / SIDEBAR_DIR / dir_node.rel_path
    out_dir.mkdir(parents=True, exist_ok=True)
    own = build_sidebar(dir_node)
    in_parent = build_sidebar(dir_node.parent, dir_node) if dir_node.parent else own
    (out_dir / "toc.html").write_text("\n".join(own) + "\n", encoding="utf-8")
    (out_dir / "index-toc.html").write_text("\n".join(in_parent) + "\n", encoding="utf-8")
    for sub in dir_node.subdirs:
        write_sidebar(sub, target_root)  # <-- recursion


# --------------------------------------------------------------------------
# Recomendaciones: tips in SOURCE/_recomendaciones/<name>.md, attached by name
# in a page's `recomendaciones` front matter. A tip on a directory's index.md
# applies to everything under it. Each directory gets
# _includes/page-recs/<dir>/recs.html, the callout the layout puts after the
# page content: one group per source (root first, then each section on the
# path, then the page itself), each tip included (see
# site/_includes/site-recs.html) from _includes/recomendaciones/<name>.md.
# Liquid picks a page's own tips by exact URL, like the sidebar's "current".
# --------------------------------------------------------------------------

REC_DIR = "_recomendaciones"
REC_INCLUDES = Path("_includes") / "recomendaciones"
PAGE_RECS_DIR = Path("_includes") / "page-recs"
ROOT_RECS_LABEL = "Para todas las recetas"
SECTION_RECS_LABEL = "Para toda la sección {}"
PAGE_RECS_LABEL = "Para esta receta"


def load_recs(source_root: Path) -> dict[str, str]:
    rec_dir = source_root / REC_DIR
    if not rec_dir.is_dir():
        return {}
    return {p.stem: p.read_text(encoding="utf-8") for p in sorted(rec_dir.glob("*.md"))}


def new_recs(names: list[str], seen: list[str]) -> list[str]:
    """Tip names not in `seen`, without repeats, in first-seen order."""
    return list(dict.fromkeys(name for name in names if name not in seen))


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


def rec_groups(dir_node: Dir) -> list[tuple[str, list[str]]]:
    """(label, tip names) per directory on the path that adds tips, root first."""
    groups, seen = [], []
    for d in ancestor_chain(dir_node):
        new = new_recs(d.index_page.recs if d.index_page else [], seen)
        if new:
            label = (SECTION_RECS_LABEL.format(link_html(d.index_page)) if d.parent
                     else ROOT_RECS_LABEL)
            groups.append((label, new))
            seen += new
    return groups


def recs_block(groups: list[tuple[str, list[str]]]) -> list[str]:
    if not groups:
        return []
    lines = ['<aside class="page-recs">', '<p class="page-recs-title">Recomendaciones</p>']
    for label, names in groups:
        lines += ['<div class="page-recs-group">', f'<p class="page-recs-for">{label}</p>',
                  f'{{% include site-recs.html names="{",".join(names)}" %}}', "</div>"]
    return lines + ["</aside>"]


def write_page_recs(dir_node: Dir, target_root: Path) -> None:
    base = rec_groups(dir_node)
    seen = [name for _, names in base for name in names]
    own = [(p, new_recs(p.recs, seen)) for p in dir_node.pages]
    own = [(p, extra) for p, extra in own if extra]
    lines = recs_block(base)
    if own:
        branches = [f"{{% {'if' if i == 0 else 'elsif'} here == '{page_url(p)}' %}}\n"
                    + "\n".join(recs_block(base + [(PAGE_RECS_LABEL, extra)]))
                    for i, (p, extra) in enumerate(own)]
        lines = ["{% assign here = page.url | remove: '.html' %}", *branches,
                 *(["{% else %}", *lines] if lines else []), "{% endif %}"]
    out_dir = target_root / PAGE_RECS_DIR / dir_node.rel_path
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "recs.html").write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")
    for sub in dir_node.subdirs:
        write_page_recs(sub, target_root)  # <-- recursion


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
# Curation report: curacion.md at the output root (served as /curacion)
#
# Progress, fix-up suggestions and every page's `[page_link](<uuid>)` text,
# for whoever is curating. Generated, so it is not part of the tree: no
# {toc}/sidebar entry, no custom_edit_url. The layout links it from the
# sidebar footer.
# --------------------------------------------------------------------------

REPORT_FILE = "curacion.md"
REPORT_TITLE = "Curación"
PARENS_RE = re.compile(r"\([^)]*\)")
H2_RE = re.compile(r"^## +(.*?)\s*$", re.MULTILINE)
H3_RE = re.compile(r"^### +(.*?)\s*$", re.MULTILINE)
TEMPLATE_LINE_RE = re.compile(r"^(?:#+ *)?(?:Ingredientes|Preparaci[oó]n)\s*:?\s*$",
                              re.MULTILINE | re.IGNORECASE)
TABLE_ROW_RE = re.compile(r"^\|\*\*[^|]*\*\*\|(.*)\|\s*$", re.MULTILINE)
MISSING_LABELS = {"summary": "Resumen", "ingredients": "Ingredientes", "data": "Datos"}


def walk(dir_node: Dir):
    """Every page under dir_node in {toc} order: index, pages, then subdirs."""
    if dir_node.index_page:
        yield dir_node.index_page
    yield from dir_node.pages
    for sub in dir_node.subdirs:
        yield from walk(sub)  # <-- recursion


def expected_slug(title: str) -> str:
    """Slug rule: title minus (notes), no accents, lowercase, other runs as '-'."""
    text = unicodedata.normalize("NFD", PARENS_RE.sub("", str(title)).lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


def recipe_sections(body: str) -> dict[str, str]:
    """{heading: text} for each `### ` section of a page body."""
    parts = H3_RE.split(body)
    return {parts[i]: parts[i + 1] for i in range(1, len(parts) - 1, 2)}


def name_issues(page: Page, h2s: list[str]) -> list[str]:
    issues = []
    if h2s and h2s[0] != str(page.title):
        issues.append(f"el título `## {h2s[0]}` no coincide con page_title")
    is_index = page.dir_node.index_page is page
    if is_index:
        name = page.dir_node.rel_path.name
        if page.dir_node.parent and name != expected_slug(page.title):
            issues.append(f"directorio `{name}`, esperado `{expected_slug(page.title)}`")
        return issues
    if page.slug != expected_slug(page.title):
        issues.append(f"slug `{page.slug}`, esperado `{expected_slug(page.title)}`")
    if page.rel_path.stem != page.slug:
        issues.append(f"archivo `{page.rel_path.name}` no coincide con el slug")
    return issues


def page_issues(page: Page) -> tuple[list[str], list[tuple[str, str]]]:
    """(missing checklist items, [(kind, detail)] suggestions) for one page."""
    _, _, body = split_frontmatter(page.src_path.read_text(encoding="utf-8"))
    h2s = H2_RE.findall(body)
    suggestions = [("Nombres", issue) for issue in name_issues(page, h2s)]
    if len(h2s) > 1:
        extra = ", ".join(f"`## {h}`" for h in h2s[1:])
        suggestions.append(("Encabezados `##` extra", f"{extra}: usar `###` o `####`"))
    if re.search(r"linkear", body, re.IGNORECASE):
        suggestions.append(("Enlaces pendientes", "texto «Linkear…» sin enlace"))

    sections = recipe_sections(body)
    if "Preparación" not in sections:
        return [], suggestions
    steps = sections["Preparación"]
    missing = []
    if any(m in body for m in CURATION_MARKERS):
        missing.append("summary")
    if not re.search(r"[^\s-]", sections.get("Ingredientes", "")):
        missing.append("ingredients")
    if not any(cell.strip(" |") for cell in TABLE_ROW_RE.findall(body)):
        missing.append("data")
    if TEMPLATE_LINE_RE.search(steps):
        suggestions.append(("Plantilla dentro de Preparación",
                            "«Ingredientes»/«Preparación» como títulos en los pasos: "
                            "pasar a las secciones de la plantilla"))
    if not steps.strip():
        suggestions.append(("Preparación vacía", "no tiene pasos"))
    if missing and not page.pending:
        labels = ", ".join(MISSING_LABELS[m] for m in missing)
        suggestions.append(("Revisadas incompletas", f"falta: {labels}"))
    return missing, suggestions


def report_link(page: Page) -> str:
    return f"[{page.title}]({site_link(page)}){pending_mark(page)}"


def edit_link(page: Page, edit_base_url: str | None) -> str:
    return f"[editar]({compute_edit_url(page, edit_base_url)})" if edit_base_url else ""


def percent(done: int, total: int) -> str:
    return f"{round(100 * done / total)}%" if total else "—"


def report_sections(tree: Dir) -> list[tuple[str, list[Page]]]:
    """(name, pages) per top-level section; root-level pages under "Inicio"."""
    root = ([tree.index_page] if tree.index_page else []) + tree.pages
    groups = [("Inicio", root)] if root else []
    for sub in tree.subdirs:
        name = sub.index_page.title if sub.index_page else sub.rel_path.name
        groups.append((str(name), list(walk(sub))))
    return groups


def curation_report(tree: Dir, edit_base_url: str | None) -> str:
    groups = report_sections(tree)
    pages = [p for _, group in groups for p in group]
    issues = {id(p): page_issues(p) for p in pages}
    done = sum(not p.pending for p in pages)

    lines = [f"---\ntitle: {REPORT_TITLE}\npage_title: {REPORT_TITLE}\n---\n",
             f"## {REPORT_TITLE}", "",
             "Generada en cada publicación: progreso de la revisión, sugerencias y el "
             "texto `[page_link](<uuid>)` de cada página para enlazarla desde otra.", "",
             "### Progreso", "",
             f"**{done} de {len(pages)} páginas revisadas ({percent(done, len(pages))})**", "",
             f'<progress value="{done}" max="{len(pages)}"></progress>', "",
             "| Sección | Revisadas | Total | % |", "|---|---:|---:|---:|"]
    for name, group in groups:
        ok = sum(not p.pending for p in group)
        lines.append(f"| {name} | {ok} | {len(group)} | {percent(ok, len(group))} |")

    lines += ["", "Recetas a las que todavía les falta:", ""]
    for key, label in MISSING_LABELS.items():
        count = sum(key in issues[id(p)][0] for p in pages)
        lines.append(f"- {label}: {count}")

    lines += ["", "### Sugerencias", ""]
    by_kind: dict[str, list[str]] = {}
    for p in pages:
        for kind, detail in issues[id(p)][1]:
            item = f"- {report_link(p)}: {detail} {edit_link(p, edit_base_url)}".rstrip()
            by_kind.setdefault(kind, []).append(item)
    if not by_kind:
        lines.append("_Nada que sugerir._")
    for kind, items in by_kind.items():
        lines += [f"#### {kind} ({len(items)})", "", *items, ""]

    lines += ["", "### Páginas", ""]
    for name, group in groups:
        lines += [f"#### {name}", "", "| Página | Falta | Enlace | |", "|---|---|---|---|"]
        for p in group:
            missing = ", ".join(MISSING_LABELS[m] for m in issues[id(p)][0])
            # entities, so kramdown doesn't turn the snippet inside the HTML into a link
            code = (f'<code class="copy">&#91;page_link&#93;({p.uuid})</code>'
                    if p.uuid else "")
            lines.append(f"| {report_link(p)} | {missing} | {code} | "
                         f"{edit_link(p, edit_base_url)} |")
        lines.append("")
    return "\n".join(lines)


def write_report(tree: Dir, target_root: Path, edit_base_url: str | None,
                 warnings: list) -> None:
    out_path = target_root / REPORT_FILE
    if out_path.exists():
        warnings.append(f"curation report overrides a generated file: {REPORT_FILE}")
    out_path.write_text(curation_report(tree, edit_base_url), encoding="utf-8")


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
    write_page_recs(tree, target_root)
    write_report(tree, target_root, edit_base_url, warnings)
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

