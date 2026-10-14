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
- Every `.md` has YAML front matter with `slug`, `uuid`, `page_title`.
- Every directory has an `index.md`; all index files have `slug: index`.
- Placeholders in bodies: `{nav}` (breadcrumb, all pages) and `{toc}` (index pages only).
- Cross-references: `[page_link](<uuid>)` -> link text becomes the target's `page_title`,
  the uuid becomes the target's URL.
- Curation markers: `{summary}` (recipes) and `<TODO>` (index pages) are manual to-dos for
  curating each recipe. They stay in the sources and are stripped from the output (one
  summary warning with the count of pages still pending).

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
- **{toc}**: per directory, regular pages first (natural sort by `page_title`,
  case-insensitive, digit runs compared as numbers so `2.` < `10.`), then subdirectories
  (by their index `page_title`), each followed by its own nested list.
- **`custom_edit_url`**: when `--edit-base-url` is given, ALWAYS injected into every output
  page's front matter as `edit_base_url + source-relative path`, overriding any existing
  value. Templates never contain it. Front matter is only re-serialized (PyYAML, key
  order kept, comments lost) when the flag is set; otherwise it passes through untouched.
- **Warnings, not failures**: missing/duplicate uuid, missing index.md, unresolved uuid
  references (left untouched in output), non-index page with `slug: index`.
- **Target is wiped and rebuilt every run.** Non-markdown files are copied unchanged.
  A target that is, contains, or sits inside the source or site dir is refused (ValueError).
- **Site files live in `site/`** and are copied over the output (`--site-dir`); a site file
  that overwrites a generated one is a warning. They must come from the source branch
  because the publish uses `force_orphan: true`, which wipes anything added to gh-pages by hand.

## Gotchas
- The site uses GitHub's default no-theme setup, which knows nothing about
  `custom_edit_url`. `site/_layouts/default.html` renders it. GitHub Pages enables
  `jekyll-default-layout`, which should apply `default` to pages without `layout:`
  (unverified on the live site).
- `scan_dir` recurses into every subdirectory of the source dir (including dot/underscore
  dirs), so keep non-doc folders outside `docs/`.
- `legacy/` holds the original source text (`Recetas.md`, `add.txt`) that `docs/` was
  migrated from. The migration scripts were removed; `add.txt` still has recipes to add.

## Open items
- Decide whether the script should also inject `layout: default` into front matter (likely
  unnecessary if `jekyll-default-layout` applies it; check the live site first).
- Ordering is by title only (natural sort); a `weight`/`order` front matter field is not implemented.
- Confirm the edit link renders on the live site now that `site/` is published.
- Curation workflow: make the manual pass over recipes more agile, e.g. a report/checklist
  of pages still containing curation markers or empty Ingredientes, or a `curated: true`
  front-matter flag shown on the site.

## Style
Single-file script, stdlib + PyYAML only. Keep functions small and recursive where walking
the tree. Add a test in `tests/` for every behavior change.
