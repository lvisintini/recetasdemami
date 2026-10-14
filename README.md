# docs-builder

Builds a Jekyll/GitHub Pages docs site from markdown templates: breadcrumb nav, hierarchical
table of contents, uuid cross-references, and per-page `custom_edit_url`.

See `CLAUDE.md` for conventions and design decisions.

```bash
pip install -r requirements-dev.txt
python -m pytest tests -q
python scripts/build_docs.py docs build_output --site-dir site --edit-base-url "https://github.com/<owner>/<repo>/edit/main/docs"
```

## Setup
1. Put your markdown source in `docs/` (or change `SOURCE_DIR` in the workflow), and Jekyll
   site files (`_config.yml` with the theme, `_layouts/default.html`,
   `assets/css/style.scss`) in `site/`.
2. Settings > Pages: serve from the `gh-pages` branch (or change `PUBLISH_BRANCH`).
3. Push to `main`; the Action tests, builds, and publishes.
