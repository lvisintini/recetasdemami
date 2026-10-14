"""Tests locking in the behaviors settled for build_docs.py."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from build_docs import build_docs  # noqa: E402

INSTALL_UUID = "14179fca-0bc7-4181-ba7d-7cdb16fd296c"
CONFIG_UUID = "00000000-0000-0000-0000-000000000004"
EDIT_BASE = "https://github.com/org/repo/edit/main/docs"


def write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def page(slug, uuid, title, body, extra=""):
    return f"---\nslug: {slug}\nuuid: {uuid}\npage_title: {title}\n{extra}---\n{body}\n"


@pytest.fixture
def src(tmp_path):
    s = tmp_path / "src"
    write(s, "index.md", page("index", "00000000-0000-0000-0000-000000000001", "Home",
                              "{nav}\n\n{toc}"))
    write(s, "getting-started/index.md",
          page("index", "00000000-0000-0000-0000-000000000002", "Getting Started",
               "{nav}\n\n{toc}"))
    write(s, "getting-started/installation.md",
          page("installation", INSTALL_UUID, "Installation", "{nav}\n\nInstall."))
    write(s, "getting-started/config.md",
          page("config", CONFIG_UUID, "Configuration",
               f"{{nav}}\n\nSee [page_link]({INSTALL_UUID}) first.",
               extra="custom_edit_url: https://example.com/stale\n"))
    write(s, "advanced/index.md",
          page("index", "00000000-0000-0000-0000-000000000005", "Advanced Topics",
               "{nav}\n\n{toc}"))
    write(s, "advanced/tuning.md",
          page("tuning", "00000000-0000-0000-0000-000000000006", "Performance Tuning",
               f"{{nav}}\n\nSee [page_link]({CONFIG_UUID}).\n"
               "Unknown: [page_link](aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee)"))
    write(s, "getting-started/diagram.png", "not-really-a-png")
    return s


@pytest.fixture
def built(src, tmp_path):
    out = tmp_path / "out"
    _, _, warnings = build_docs(str(src), str(out), edit_base_url=EDIT_BASE)
    return out, warnings


def read(out, rel):
    return (out / rel).read_text(encoding="utf-8")


def test_uuid_link_resolved_to_title_and_url(built):
    out, _ = built
    assert f"[Installation]({{{{ site.baseurl }}}}/getting-started/installation)" in \
        read(out, "getting-started/config.md")


def test_unresolved_uuid_left_untouched_and_warned(built):
    out, warnings = built
    assert "(aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee)" in read(out, "advanced/tuning.md")
    assert any("unresolved uuid" in w for w in warnings)


def test_page_urls_have_no_trailing_slash(built):
    out, _ = built
    assert "{{ site.baseurl }}/getting-started/installation)" in read(out, "index.md")


def test_directory_urls_keep_trailing_slash_and_never_contain_index(built):
    out, _ = built
    toc = read(out, "index.md")
    assert "{{ site.baseurl }}/getting-started/)" in toc
    assert "{{ site.baseurl }}/advanced/)" in toc
    for f in out.rglob("*.md"):
        assert "/index" not in f.read_text(encoding="utf-8").split("---", 2)[2]


def test_every_link_uses_baseurl_prefix(built):
    out, _ = built
    for f in out.rglob("*.md"):
        body = f.read_text(encoding="utf-8").split("---", 2)[2]
        assert "](../" not in body and ".md)" not in body


def test_nav_breadcrumb(built):
    out, _ = built
    nav = read(out, "getting-started/config.md").split("---\n")[2].splitlines()[0]
    assert nav == ("[Home]({{ site.baseurl }}/) / "
                   "[Getting Started]({{ site.baseurl }}/getting-started/) / Configuration")


def test_root_index_nav_is_plain_title(built):
    out, _ = built
    assert read(out, "index.md").split("---\n")[2].splitlines()[0] == "Home"


def test_toc_is_hierarchical_dirs_after_pages_sorted_by_title(built):
    out, _ = built
    toc = read(out, "index.md").split("---\n")[2]
    lines = [l for l in toc.splitlines() if l.startswith("-") or l.startswith("  -")]
    titles = [l.split("[")[1].split("]")[0] for l in lines]
    assert titles == ["Advanced Topics", "Performance Tuning",
                      "Getting Started", "Configuration", "Installation"]
    assert lines[1].startswith("  - ")  # nested under its directory


def test_edit_url_always_injected_and_overrides_existing(built):
    out, _ = built
    cfg = read(out, "getting-started/config.md")
    assert f"custom_edit_url: {EDIT_BASE}/getting-started/config.md" in cfg
    assert "stale" not in cfg
    assert f"custom_edit_url: {EDIT_BASE}/index.md" in read(out, "index.md")


def test_title_copied_from_page_title_unless_already_set(built, tmp_path):
    out, _ = built
    assert "\ntitle: Installation\n" in read(out, "getting-started/installation.md")
    s = tmp_path / "src_title"
    write(s, "index.md", page("index", "00000000-0000-0000-0000-000000000001", "Home",
                              "{nav}", extra="title: Custom\n"))
    build_docs(str(s), str(tmp_path / "out_title"), edit_base_url=EDIT_BASE)
    fm = read(tmp_path / "out_title", "index.md")
    assert "\ntitle: Custom\n" in fm and "title: Home" not in fm.replace("page_title", "")


def test_front_matter_untouched_without_edit_base_url(src, tmp_path):
    out = tmp_path / "out2"
    build_docs(str(src), str(out))
    assert "custom_edit_url: https://example.com/stale" in read(out, "getting-started/config.md")
    assert "custom_edit_url" not in read(out, "getting-started/installation.md")
    assert "\ntitle:" not in read(out, "getting-started/installation.md")


def test_non_markdown_files_copied(built):
    out, _ = built
    assert (out / "getting-started/diagram.png").read_text() == "not-really-a-png"


def test_target_is_wiped_each_run(src, tmp_path):
    out = tmp_path / "out3"
    out.mkdir()
    (out / "stale.txt").write_text("old")
    build_docs(str(src), str(out), edit_base_url=EDIT_BASE)
    assert not (out / "stale.txt").exists()


def test_non_index_page_with_slug_index_warns(tmp_path):
    s = tmp_path / "s"
    write(s, "index.md", page("index", "00000000-0000-0000-0000-0000000000a1", "Home", "{nav}"))
    write(s, "oops.md", page("index", "00000000-0000-0000-0000-0000000000a2", "Oops", "{nav}"))
    _, _, warnings = build_docs(str(s), str(tmp_path / "o"), edit_base_url=EDIT_BASE)
    assert any("collide" in w for w in warnings)


def test_toc_sorts_numbered_titles_naturally(tmp_path):
    s = tmp_path / "s"
    write(s, "index.md", page("index", "00000000-0000-0000-0000-0000000000b1", "Home", "{toc}"))
    write(s, "ten/index.md", page("index", "00000000-0000-0000-0000-0000000000b2", "10. Postres", ""))
    write(s, "two/index.md", page("index", "00000000-0000-0000-0000-0000000000b3", "2. Entradas", ""))
    out = tmp_path / "o"
    build_docs(str(s), str(out), edit_base_url=EDIT_BASE)
    toc = read(out, "index.md")
    assert toc.index("2. Entradas") < toc.index("10. Postres")


def test_curation_markers_stripped_from_output_only(tmp_path):
    s = tmp_path / "s"
    write(s, "index.md", page("index", "00000000-0000-0000-0000-0000000000c1", "Home",
                              "{toc}\n\n<TODO>"))
    write(s, "dish.md", page("dish", "00000000-0000-0000-0000-0000000000c2", "Dish",
                             "Intro\n\n{summary}\n\n### Ingredientes"))
    out = tmp_path / "o"
    _, _, warnings = build_docs(str(s), str(out), edit_base_url=EDIT_BASE)
    assert "Intro\n\n### Ingredientes" in read(out, "dish.md")
    assert "<TODO>" not in read(out, "index.md")
    assert "{summary}" in read(s, "dish.md")
    assert [w for w in warnings if "curation markers" in w] == [
        "2 page(s) still have curation markers ({summary}, <TODO>); stripped from output"
    ]


def test_sidebar_include_two_levels_collapsible(tmp_path):
    s = tmp_path / "s"
    write(s, "index.md", page("index", "00000000-0000-0000-0000-0000000000d1", "Home", "{nav}"))
    write(s, "ten/index.md", page("index", "00000000-0000-0000-0000-0000000000d2", "10. Postres", ""))
    write(s, "two/index.md", page("index", "00000000-0000-0000-0000-0000000000d3",
                                  "2. Salsas & Dips", ""))
    write(s, "two/sub/index.md", page("index", "00000000-0000-0000-0000-0000000000d4", "Dips", ""))
    write(s, "two/sub/deep.md", page("deep", "00000000-0000-0000-0000-0000000000d5", "Deep", ""))
    write(s, "two/flan.md", page("flan", "00000000-0000-0000-0000-0000000000d6", "Flan", ""))
    out = tmp_path / "o"
    build_docs(str(s), str(out), edit_base_url=EDIT_BASE)
    toc = read(out, "_includes/site-toc.html")
    assert toc.startswith("{% assign here = page.url | remove: '.html' %}\n<ul class=\"site-toc\">")
    assert toc.index("2. Salsas &amp; Dips") < toc.index("10. Postres")  # natural, escaped
    assert "Deep" not in toc  # level 3 left out
    # directory with children folds; open + current by URL prefix
    assert "{% assign p = here | slice: 0, 5 %}" in toc
    assert ("<li><details{% if p == '/two/' %} open{% endif %}><summary>"
            "<a href=\"{{ site.baseurl }}/two/\"{% if p == '/two/' %} class=\"current\"{% endif %}>"
            in toc)
    # level-2 page: exact match; level-2 dir without shown children: plain link
    assert "<li><a href=\"{{ site.baseurl }}/two/flan\"{% if here == '/two/flan' %}" in toc
    assert "<li><a href=\"{{ site.baseurl }}/two/sub/\"" in toc
    assert "<li><a href=\"{{ site.baseurl }}/ten/\"" in toc  # empty dir: no <details>


def test_site_dir_copied_into_output(src, tmp_path):
    site = tmp_path / "site"
    write(site, "_config.yml", "baseurl: /x\n")
    write(site, "_layouts/default.html", "{{ content }}")
    out = tmp_path / "o"
    build_docs(str(src), str(out), edit_base_url=EDIT_BASE, site_dir=str(site))
    assert read(out, "_config.yml") == "baseurl: /x\n"
    assert read(out, "_layouts/default.html") == "{{ content }}"


@pytest.mark.parametrize("target", [".", "..", "sub/out"])
def test_target_overlapping_source_is_refused(src, target):
    with pytest.raises(ValueError):
        build_docs(str(src), str(src / target), edit_base_url=EDIT_BASE)
    assert (src / "index.md").exists()
