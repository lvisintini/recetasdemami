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


def page(slug, uuid, title, body, extra="", curated=True):
    flag = "" if curated is None else f"curated: {str(curated).lower()}\n"
    return (f"---\nslug: {slug}\nuuid: {uuid}\npage_title: {title}\n{flag}{extra}---\n"
            f"{body}\n")


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


def test_toc_bolds_directories_only(built):
    out, _ = built
    toc = read(out, "index.md")
    assert "- **[Getting Started]({{ site.baseurl }}/getting-started/)**" in toc
    assert "  - [Installation]({{ site.baseurl }}/getting-started/installation)\n" in toc


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


def test_toc_sorts_ignoring_accents_with_n_tilde_after_n(tmp_path):
    s = tmp_path / "s"
    write(s, "index.md", page("index", "00000000-0000-0000-0000-0000000000a1", "Home", "{toc}"))
    for i, title in enumerate(["Omelette", "Ñoquis", "Brusquetas", "Nicoise", "Brócoli"]):
        write(s, f"p{i}.md", page(f"p{i}", f"00000000-0000-0000-0000-0000000000a{i + 2}",
                                  title, ""))
    out = tmp_path / "o"
    build_docs(str(s), str(out), edit_base_url=EDIT_BASE)
    toc = read(out, "index.md")
    order = ["Brócoli", "Brusquetas", "Nicoise", "Ñoquis", "Omelette"]
    assert [toc.index(f"[{t}]") for t in order] == sorted(toc.index(f"[{t}]") for t in order)


def test_weight_orders_before_title(tmp_path):
    s = tmp_path / "s"
    write(s, "index.md", page("index", "00000000-0000-0000-0000-0000000000e1", "Home", "{toc}"))
    write(s, "zeta.md", page("zeta", "00000000-0000-0000-0000-0000000000e2", "Zeta", "",
                             extra="weight: 1\n"))
    write(s, "alfa.md", page("alfa", "00000000-0000-0000-0000-0000000000e3", "Alfa", ""))
    write(s, "beta/index.md", page("index", "00000000-0000-0000-0000-0000000000e4", "Beta", "",
                                   extra="weight: 2\n"))
    write(s, "gamma/index.md", page("index", "00000000-0000-0000-0000-0000000000e5", "Gamma", "",
                                    extra="weight: 1\n"))
    write(s, "delta/index.md", page("index", "00000000-0000-0000-0000-0000000000e6", "Delta", ""))
    out = tmp_path / "o"
    _, _, warnings = build_docs(str(s), str(out), edit_base_url=EDIT_BASE)
    toc = read(out, "index.md")
    titles = [l.split("[")[1].split("]")[0] for l in toc.splitlines() if l.startswith("- ")]
    assert titles == ["Zeta", "Alfa", "Gamma", "Beta", "Delta"]  # pages, then dirs
    sidebar = read(out, "_includes/site-toc/toc.html")
    assert sidebar.index(">Gamma<") < sidebar.index(">Beta<") < sidebar.index(">Delta<")
    assert not any("weight" in w for w in warnings)


def test_non_numeric_weight_warns_and_is_ignored(tmp_path):
    s = tmp_path / "s"
    write(s, "index.md", page("index", "00000000-0000-0000-0000-0000000000f1", "Home", "{toc}"))
    write(s, "b.md", page("b", "00000000-0000-0000-0000-0000000000f2", "B", "",
                          extra="weight: first\n"))
    write(s, "a.md", page("a", "00000000-0000-0000-0000-0000000000f3", "A", ""))
    out = tmp_path / "o"
    _, _, warnings = build_docs(str(s), str(out), edit_base_url=EDIT_BASE)
    toc = read(out, "index.md")
    assert toc.index("[A]") < toc.index("[B]")
    assert "non-numeric weight 'first' ignored: b.md" in warnings


def test_summary_marker_stripped_from_output_only(tmp_path):
    s = tmp_path / "s"
    write(s, "index.md", page("index", "00000000-0000-0000-0000-0000000000c1", "Home", "{toc}"))
    write(s, "dish.md", page("dish", "00000000-0000-0000-0000-0000000000c2", "Dish",
                             "Intro\n\n<SUMMARY>\n\n### Ingredientes", curated=False))
    write(s, "done.md", page("done", "00000000-0000-0000-0000-0000000000c3", "Done",
                             "Intro\n\n<SUMMARY>\n\nBody"))
    out = tmp_path / "o"
    _, _, warnings = build_docs(str(s), str(out), edit_base_url=EDIT_BASE)
    assert "Intro\n\n### Ingredientes" in read(out, "dish.md")
    assert "Intro\n\nBody" in read(out, "done.md")
    assert "<SUMMARY>" in read(s, "dish.md")
    assert "curated page still has <SUMMARY>: done.md" in warnings
    assert not any("dish.md" in w for w in warnings)  # pending: placeholder expected
    assert "1 page(s) not curated yet (curated: true missing)" in warnings


def test_pending_pages_get_a_note_and_their_links_a_mark(tmp_path):
    s = tmp_path / "s"
    write(s, "index.md", page("index", "00000000-0000-0000-0000-0000000000e1", "Home",
                              "{nav}\n\n{toc}"))
    write(s, "sec/index.md", page("index", "00000000-0000-0000-0000-0000000000e2", "Sec",
                                  "{nav}\n\n{toc}", curated=None))  # no flag: pending
    write(s, "sec/dish.md", page("dish", "00000000-0000-0000-0000-0000000000e3", "Dish",
                                 "{nav}\n\n## Dish\n\nBody", curated=False))
    write(s, "sec/done.md", page("done", "00000000-0000-0000-0000-0000000000e4", "Done",
                                 "{nav}\n\nSee [page_link](00000000-0000-0000-0000-0000000000e3)"))
    write(s, "sec/bare.md", page("bare", "00000000-0000-0000-0000-0000000000e5", "Bare",
                                 "No nav", curated=None, extra="curated: 'yes'\n"))
    out = tmp_path / "o"
    _, _, warnings = build_docs(str(s), str(out), edit_base_url=EDIT_BASE)
    mark = ('<span class="pending-mark" role="img" title="Pendiente de revisión" '
            'aria-label="Pendiente de revisión"></span>')
    note = '<p class="pending-note">Pendiente de revisión</p>'
    dish = read(out, "sec/dish.md")
    assert dish.split("---\n", 2)[2].startswith(
        "[Home]({{ site.baseurl }}/) / [Sec]({{ site.baseurl }}/sec/) / Dish"  # nav: no marks
        f"\n\n{note}\n\n## Dish")
    assert read(out, "sec/bare.md").split("---\n", 2)[2].startswith(f"{note}\n\nNo nav")
    assert "non-boolean curated 'yes' treated as false: sec/bare.md" in warnings
    assert "3 page(s) not curated yet (curated: true missing)" in warnings
    done = read(out, "sec/done.md")
    assert note not in done
    assert f"See [Dish]({{{{ site.baseurl }}}}/sec/dish){mark}" in done  # uuid reference
    toc = read(out, "index.md")
    assert f"- **[Sec]({{{{ site.baseurl }}}}/sec/)**{mark}" in toc
    assert f"  - [Dish]({{{{ site.baseurl }}}}/sec/dish){mark}" in toc
    assert "  - [Done]({{ site.baseurl }}/sec/done)\n" in toc  # curated: no mark
    sidebar = read(out, "_includes/site-toc/sec/toc.html")
    assert "Dish</a>" + mark + "</li>" in sidebar
    assert "Done</a></li>" in sidebar
    assert "Home</a></p>" in sidebar  # curated root heading


def build_sidebar_tree(tmp_path) -> Path:
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
    return out / "_includes/site-toc"


def test_sidebar_include_per_directory_flat_list(tmp_path):
    inc = build_sidebar_tree(tmp_path)
    assert sorted(f.relative_to(inc).as_posix() for f in inc.rglob("toc.html")) == [
        "ten/toc.html", "toc.html", "two/sub/toc.html", "two/toc.html"]
    root = read(inc, "toc.html")
    assert root.startswith("{% assign here = page.url | remove: '.html' %}\n"
                           '<p class="site-toc-title"><a href="{{ site.baseurl }}/">Home</a></p>\n'
                           '<ul class="site-toc">')
    assert root.index("2. Salsas &amp; Dips") < root.index("10. Postres")  # natural, escaped
    assert "/two/flan" not in root and "/two/sub/" not in root  # only direct children
    assert "<details" not in root
    assert '<li><strong><a href="{{ site.baseurl }}/ten/">10. Postres</a></strong></li>' in root
    two = read(inc, "two/toc.html")
    assert two.index("/two/flan") < two.index("/two/sub/")  # pages before subdirectories
    assert ('<li><a href="{{ site.baseurl }}/two/flan"'
            "{% if here == '/two/flan' %} class=\"current\"{% endif %}>Flan</a></li>") in two
    assert '<li><strong><a href="{{ site.baseurl }}/two/sub/">Dips</a></strong></li>' in two
    assert "Deep" not in two
    assert "/two/" not in read(inc, "ten/toc.html")  # empty directory: trail only


def test_sidebar_lists_the_trail_of_ancestor_sections(tmp_path):
    inc = build_sidebar_tree(tmp_path)
    deep = read(inc, "two/sub/toc.html")
    # root heading, then each section on the path with the next level nested in it
    assert ('<p class="site-toc-title"><a href="{{ site.baseurl }}/">Home</a></p>\n'
            '<ul class="site-toc">\n'
            '<li><strong><a href="{{ site.baseurl }}/two/">2. Salsas &amp; Dips</a></strong>\n'
            '<ul class="site-toc">\n'
            '<li><strong><a href="{{ site.baseurl }}/two/sub/">Dips</a></strong>\n'
            '<ul class="site-toc">\n'
            '<li><a href="{{ site.baseurl }}/two/sub/deep"') in deep
    assert "/two/flan" not in deep and "/ten/" not in deep  # ancestors only, no their siblings
    sub = read(inc, "two/sub/index-toc.html")
    assert sub.index("/two/") < sub.index("/two/flan") < sub.index("/two/sub/\" class")


def test_sidebar_index_page_lists_parent_with_own_section_expanded(tmp_path):
    inc = build_sidebar_tree(tmp_path)
    two = read(inc, "two/index-toc.html")
    assert '<p class="site-toc-title"><a href="{{ site.baseurl }}/">Home</a></p>' in two
    assert ('<li><strong><a href="{{ site.baseurl }}/two/" class="current">'
            "2. Salsas &amp; Dips</a></strong>\n<ul class=\"site-toc\">") in two
    # own children right under it, before the next sibling; no grandchildren
    assert two.index("/two/") < two.index("/two/flan") < two.index("/two/sub/") \
        < two.index("/ten/")
    assert "Deep" not in two
    sub = read(inc, "two/sub/index-toc.html")
    assert '<a href="{{ site.baseurl }}/two/sub/" class="current">Dips</a>' in sub
    assert "/two/sub/deep" in sub and "10. Postres" not in sub
    assert read(inc, "index-toc.html") == read(inc, "toc.html")  # root: no parent


def build_recs_tree(tmp_path):
    s = tmp_path / "s"
    recs = "recomendaciones: [general]\n"
    write(s, "index.md", page("index", "00000000-0000-0000-0000-0000000000f1", "Home",
                              "{nav}\n\n{toc}", extra=recs))
    write(s, "sec/index.md", page("index", "00000000-0000-0000-0000-0000000000f2", "Sec",
                                  "{nav}\n\n{toc}",
                                  extra="recomendaciones: [seccion, general]\n"))
    write(s, "sec/sub/index.md", page("index", "00000000-0000-0000-0000-0000000000f3", "Sub",
                                      "{toc}"))
    write(s, "sec/sub/dish.md", page("dish", "00000000-0000-0000-0000-0000000000f4", "Dish",
                                     "Body", extra="recomendaciones: plato\n"))
    write(s, "sec/sub/plain.md", page("plain", "00000000-0000-0000-0000-0000000000f5",
                                      "Plain", "Body"))
    write(s, "other/index.md", page("index", "00000000-0000-0000-0000-0000000000f6", "Other",
                                    "{toc}", extra="recomendaciones: [nope]\n"))
    write(s, "other/bad.md", page("bad", "00000000-0000-0000-0000-0000000000f7", "Bad",
                                  "Body", extra="recomendaciones: {a: 1}\n"))
    write(s, "_recomendaciones/general.md", "General tip.\n")
    write(s, "_recomendaciones/seccion.md",
          "Ver [page_link](00000000-0000-0000-0000-0000000000f4).\n")
    write(s, "_recomendaciones/plato.md", "Plato tip.\n")
    write(s, "_recomendaciones/suelto.md", "Unused tip.\n")
    out = tmp_path / "o"
    _, _, warnings = build_docs(str(s), str(out), edit_base_url=EDIT_BASE)
    return out, warnings


def test_recs_inherited_by_everything_under_a_section(tmp_path):
    out, _ = build_recs_tree(tmp_path)
    inc = out / "_includes/site-toc"
    # most general first, no repeats; the index page gets its own section's tips
    assert read(inc, "sec/index-toc.html").endswith(
        '{% assign recs = "general,seccion" %}\n{% include site-recs.html names=recs %}\n')
    assert '{% assign recs = "general,seccion" %}' in read(inc, "sec/sub/index-toc.html")
    assert '{% assign recs = "general" %}' in read(inc, "other/toc.html")  # sibling: root only
    assert "seccion" not in read(inc, "other/toc.html")


def test_page_recs_only_for_that_page(tmp_path):
    out, _ = build_recs_tree(tmp_path)
    sub = read(out, "_includes/site-toc/sec/sub/toc.html")
    assert sub.endswith(
        '{% assign recs = "general,seccion" %}\n'
        "{% if here == '/sec/sub/dish' %}{% assign recs = \"general,seccion,plato\" %}"
        "{% endif %}\n{% include site-recs.html names=recs %}\n")
    assert "plain' %}{% assign recs" not in sub
    assert "plato" not in read(out, "_includes/site-toc/sec/sub/index-toc.html")


def test_rec_files_written_as_includes_not_pages(tmp_path):
    out, warnings = build_recs_tree(tmp_path)
    assert read(out, "_includes/recomendaciones/seccion.md") == (
        "Ver [Dish]({{ site.baseurl }}/sec/sub/dish).\n")
    assert not (out / "_recomendaciones").exists()
    assert "_recomendaciones" not in read(out, "index.md")
    assert "unknown recomendacion 'nope' in other/index.md" in warnings
    assert "unused recomendacion: suelto" in warnings
    assert "recomendaciones must be a list of names, ignored: other/bad.md" in warnings


def test_no_recs_block_without_tips(tmp_path):
    inc = build_sidebar_tree(tmp_path)
    assert "site-recs" not in read(inc, "two/toc.html")
    assert not (inc.parent / "recomendaciones").exists()


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
