# CLAUDE.md

## What this project is
A build step for a Jekyll / GitHub Pages documentation site. Markdown "template"
files live in `docs/` on the source branch. `scripts/build_docs.py` walks that
tree depth-first, fills in placeholders, and writes a fresh copy to an output
directory, then overlays the Jekyll site files from `site/` (`_config.yml`,
`_layouts/`). A GitHub Action publishes that output to the Pages branch.

## Commands
- Install: `pip install -r requirements-dev.txt` (locally: `python3 -m venv --without-pip .venv`
  then `python3 -m pip --python .venv/bin/python install -r requirements-dev.txt`; the
  system Python has no ensurepip)
- Test: `python -m pytest tests -q`
- Build locally:
  `python scripts/build_docs.py docs build_output --site-dir site --edit-base-url "https://github.com/<owner>/<repo>/edit/main/docs"`
- CI: `.github/workflows/build-and-publish-docs.yml` (tests, build, publish to `gh-pages`
  with `force_orphan: true`). Pages must be set to serve from the publish branch.

## Source file conventions
- Every `.md` has YAML front matter with `slug`, `uuid`, `page_title`, `curated` (see
  below), and optionally `weight` (a number) to fix its position: weighted entries come first, ascending, before
  unweighted ones; a directory's weight is its `index.md`'s. Titles carry no numbering
  (the top-level sections use `weight: 1`..`11`). A non-numeric weight is warned and ignored.
- Every directory has an `index.md`; all index files have `slug: index`.
- Placeholders in bodies: `{nav}` (breadcrumb, all pages) and `{toc}` (index pages only).
- Cross-references: `[page_link](<uuid>)` -> link text becomes the target's `page_title`,
  the uuid becomes the target's URL.
- Curation: `curated: true|false` in the front matter says whether a page has been reviewed
  by hand. Anything but `true` (missing, or non-boolean, which is warned) is "pending": the
  page gets a `Pendiente de revisión` note under its `{nav}` (on top if it has none), every
  generated link to it in `{toc}`, the sidebar and uuid references gets a `.pending-mark`
  icon (CSS in `site/assets/css/style.scss`), and the build warns once with the count.
  Breadcrumbs are left unmarked. `<SUMMARY>` in recipes is the placeholder for the summary
  still to write; it stays in the sources, is stripped from the output, and is warned if
  left on a curated page.

## Settled design decisions (do not re-litigate without asking)
- **Slugs match file/dir names** (ignoring extension). Directory URL segments come from the
  **directory name**, never from the index page's slug (it is always `index`; using it
  produced `/index/index/`). Regular pages use their own `slug`.
- **URL shape**: directory/index pages end with a trailing slash (`/getting-started/`);
  regular pages have NO trailing slash (`/getting-started/installation`), otherwise 404.
  The word `index` and `.md` never appear in a generated link.
- **All generated links** are prefixed `{{ site.baseurl }}` and are site-root-relative.
  No relative links.
- **{nav}**: ancestors linked, current page plain text, separator `" / "` (CLI `--nav-sep`).
- **{toc}**: per directory, regular pages first, then subdirectories (in **bold**), each
  followed by its own nested list. Within each group: by `weight` if present, then natural
  sort by `page_title` (a subdirectory by its index's; case-insensitive, digit runs
  compared as numbers so `2.` < `10.`).
- **Sidebar index**: bulleted lists (same ordering as `{toc}`, subdirectories in bold;
  top level flush left, each nested level indented by the hierarchy) under a heading
  linking the root index, nested under the trail of ancestor sections from the root down
  to the listed directory (the ancestors only, not their siblings). Never collapsible,
  mobile included. Every build writes two per directory to `_includes/site-toc/<dir>/`,
  and the layout picks one by `page.dir` / `page.name`:
  - `toc.html` (regular pages): the directory's direct children, i.e. the page's siblings;
    Liquid marks the current page with `class="current"` (exact URL).
  - `index-toc.html` (the directory's index page): the parent's list with this directory
    marked current and its own direct children listed right under it. The root has no
    parent, so it gets its own list.
- **`custom_edit_url`**: when `--edit-base-url` is given, ALWAYS injected into every output
  page's front matter as `edit_base_url + source-relative path`, overriding any existing
  value. Templates never contain it. The same step sets `title` from `page_title` (unless
  `title` exists) because the theme's `<title>` reads `title`. Front matter is only re-serialized (PyYAML, key
  order kept, comments lost) when the flag is set; otherwise it passes through untouched.
- **Warnings, not failures**: missing/duplicate uuid, missing index.md, unresolved uuid
  references (left untouched in output), non-index page with `slug: index`.
- **Target is wiped and rebuilt every run.** Non-markdown files are copied unchanged.
  A target that is, contains, or sits inside the source or site dir is refused (ValueError).
- **Site files live in `site/`** and are copied over the output (`--site-dir`); a site file
  that overwrites a generated one is a warning. They must come from the source branch
  because the publish uses `force_orphan: true`, which wipes anything added to gh-pages by hand.

## Gotchas
- The site uses the `jekyll-theme-minimal` theme (`site/_config.yml`).
  `site/_layouts/default.html` is a copy of the theme's v0.2.0 layout with the sidebar
  index in `<header>` (the sidebar) and the project link, edit link and credits in a
  `.sidebar-footer` pinned at its bottom; keep it in step with the theme if the theme
  changes. `site/assets/css/style.scss` imports the theme's CSS, widens the sidebar and
  makes it a flex column where only the index scrolls. The theme positions `header ul`
  absolutely at <=960px, hence the `position:static` override on `.site-toc`. `_config.yml` `defaults:` applies `layout: default`, so the
  script does not inject `layout:`. `jekyll-titles-from-headings` can't supply page titles
  because `{nav}` comes before the first heading, hence the injected `title`.
- `peaceiris/actions-gh-pages` writes a `.nojekyll` by default, which disables Jekyll and
  makes the site 404. The workflow sets `enable_jekyll: true`; keep it. Pages serves
  `gh-pages` at `/ (root)`.
- `scan_dir` recurses into every subdirectory of the source dir (including dot/underscore
  dirs), so keep non-doc folders outside `docs/`.
- `legacy/` holds the original source text (`Recetas.md`, `add.txt`) that `docs/` was
  migrated from. The migration scripts were removed; `add.txt` still has recipes to add.

## Open items
- Curation workflow: a local report/checklist of pending pages (`curated: false`, leftover
  `<SUMMARY>`, empty Ingredientes or metadata table) could complement the on-site marks.

## Style
Single-file script, stdlib + PyYAML only. Keep functions small and recursive where walking
the tree. Add a test in `tests/` for every behavior change.
