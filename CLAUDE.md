# CLAUDE.md

## What this project is
A build step for a Jekyll / GitHub Pages documentation site. Markdown "template"
files live in `docs/` on the source branch. `scripts/build_docs.py` walks that
tree depth-first, fills in placeholders, and writes a fresh copy to an output
directory. A GitHub Action publishes that output to the Pages branch.

## Commands
- Install: `pip install -r requirements-dev.txt`
- Test: `python -m pytest tests -q`
- Build locally:
  `python scripts/build_docs.py docs build_output --edit-base-url "https://github.com/<owner>/<repo>/edit/main/docs"`
- CI: `.github/workflows/build-and-publish-docs.yml` (tests, build, publish to `gh-pages`
  with `force_orphan: true`). Pages must be set to serve from the publish branch.

## Source file conventions
- Every `.md` has YAML front matter with `slug`, `uuid`, `page_title`.
- Every directory has an `index.md`; all index files have `slug: index`.
- Placeholders in bodies: `{nav}` (breadcrumb, all pages) and `{toc}` (index pages only).
- Cross-references: `[page_link](<uuid>)` -> link text becomes the target's `page_title`,
  the uuid becomes the target's URL.

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
- **{toc}**: per directory, regular pages first (alphabetical by `page_title`,
  case-insensitive), then subdirectories (by their index `page_title`), each followed by
  its own nested list.
- **`custom_edit_url`**: when `--edit-base-url` is given, ALWAYS injected into every output
  page's front matter as `edit_base_url + source-relative path`, overriding any existing
  value. Templates never contain it. Front matter is only re-serialized (PyYAML, key
  order kept, comments lost) when the flag is set; otherwise it passes through untouched.
- **Warnings, not failures**: missing/duplicate uuid, missing index.md, unresolved uuid
  references (left untouched in output), non-index page with `slug: index`.
- **Target is wiped and rebuilt every run.** Non-markdown files are copied unchanged.

## Gotchas
- The site uses GitHub's default no-theme setup, which knows nothing about
  `custom_edit_url`. A layout must render it (see `examples/_layouts_default.html`), and
  pages need `layout:` in front matter for that layout to apply. This lives in the
  publish branch / Pages repo, not in this build script.
- `scan_dir` recurses into every subdirectory of the source dir (including dot/underscore
  dirs), so keep non-doc folders outside `docs/`.

## Open items
- Decide whether the script should also inject `layout: default` into front matter.
- Ordering is alphabetical only; a `weight`/`order` front matter field is not implemented.
- Confirm the edit link renders on the live site once the layout is in place.

## Style
Single-file script, stdlib + PyYAML only. Keep functions small and recursive where walking
the tree. Add a test in `tests/` for every behavior change.
