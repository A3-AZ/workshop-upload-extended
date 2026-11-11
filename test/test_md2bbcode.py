import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import md2bbcode  # noqa: E402
from md2bbcode import Converter, convert  # noqa: E402

FIXTURES = ROOT / "test" / "fixtures"


def repo_convert(source: str, base_dir: str = "") -> str:
    return convert(source, repo="owner/repo", ref="main", base_dir=base_dir)


class InlineTests(unittest.TestCase):
    def test_emphasis(self):
        self.assertEqual(convert("**b** __b__ *i* _i_ ~~s~~"), "[b]b[/b] [b]b[/b] [i]i[/i] [i]i[/i] [strike]s[/strike]")

    def test_underscores_inside_words_are_kept(self):
        self.assertEqual(convert("snake_case_name and __init__x"), "snake_case_name and __init__x")

    def test_inline_code_is_not_formatted(self):
        self.assertEqual(convert("`**not bold**`"), "**not bold**")

    def test_inline_code_with_bbcode_is_noparsed(self):
        self.assertEqual(convert("`[b]x[/b]`"), "[noparse][b]x[/b][/noparse]")

    def test_escapes(self):
        self.assertEqual(convert(r"\*a\* back\\slash"), "*a* back\\slash")

    def test_html_entities(self):
        self.assertEqual(convert("&amp; &lt;3"), "& <3")

    def test_absolute_link(self):
        self.assertEqual(convert("[x](https://e.com/a_b_c)"), "[url=https://e.com/a_b_c]x[/url]")

    def test_link_with_title_and_angle_brackets(self):
        self.assertEqual(convert('[x](<https://e.com> "Title")'), "[url=https://e.com]x[/url]")

    def test_bare_and_autolink_urls_are_not_formatted(self):
        self.assertEqual(convert("<https://e.com/_x_> https://e.com/*y*"), "https://e.com/_x_ https://e.com/*y*")

    def test_anchor_link_becomes_text(self):
        self.assertEqual(convert("[top](#top)"), "top")

    def test_relative_link_without_repo_becomes_text(self):
        converter = Converter()
        self.assertEqual(converter.convert("[guide](docs/guide.md)"), "guide")
        self.assertTrue(converter.warnings)

    def test_relative_link_with_repo(self):
        self.assertEqual(
            repo_convert("[g](../docs/./a.md?plain=1#x)", base_dir=".steam"),
            "[url=https://github.com/owner/repo/blob/main/docs/a.md?plain=1#x]g[/url]",
        )

    def test_root_relative_link(self):
        self.assertEqual(
            repo_convert("[g](/LICENSE)", base_dir="sub/dir"),
            "[url=https://github.com/owner/repo/blob/main/LICENSE]g[/url]",
        )

    def test_relative_image_uses_raw_url(self):
        self.assertEqual(
            repo_convert("![a](img/x.png)", base_dir=".steam"),
            "[img]https://raw.githubusercontent.com/owner/repo/main/.steam/img/x.png[/img]",
        )

    def test_protocol_relative_url(self):
        self.assertEqual(convert("![a](//e.com/x.png)"), "[img]https://e.com/x.png[/img]")

    def test_svg_image_becomes_alt_text(self):
        self.assertEqual(convert("![Alt](https://e.com/x.SVG)"), "Alt")

    def test_badge_link_keeps_link_with_alt_text(self):
        self.assertEqual(
            convert("[![CI](https://img.shields.io/badge/x)](https://e.com)"),
            "[url=https://e.com]CI[/url]",
        )

    def test_image_inside_link(self):
        self.assertEqual(
            convert("[![s](https://e.com/s.png)](https://e.com)"),
            "[url=https://e.com][img]https://e.com/s.png[/img][/url]",
        )

    def test_html_img_and_br(self):
        self.assertEqual(
            convert('<img src="https://e.com/a.jpg" alt="A"> <IMG alt="B" src="b.svg"><br>next'),
            "[img]https://e.com/a.jpg[/img] B\nnext",
        )

    def test_other_html_is_removed(self):
        self.assertEqual(convert('<p align="center">text</p>'), "text")


class BlockTests(unittest.TestCase):
    def test_headings(self):
        self.assertEqual(convert("# A\n## B\n### C\n#### D ####"), "[h1]A[/h1]\n[h2]B[/h2]\n[h3]C[/h3]\n[h3]D[/h3]")

    def test_empty_heading_is_rule(self):
        self.assertEqual(convert("##"), "[hr][/hr]")

    def test_setext_headings(self):
        self.assertEqual(convert("A\n===\nB\n---"), "[h1]A[/h1]\n[h2]B[/h2]")

    def test_rules(self):
        self.assertEqual(convert("a\n\n---\n\n* * *\n\n___"), "a\n\n[hr][/hr]\n\n[hr][/hr]\n\n[hr][/hr]")

    def test_paragraph_lines_are_joined(self):
        self.assertEqual(convert("one\ntwo\n\nthree"), "one two\n\nthree")

    def test_hard_breaks(self):
        self.assertEqual(convert("one  \ntwo\\\nthree"), "one\ntwo\nthree")

    def test_keep_line_breaks(self):
        self.assertEqual(Converter(keep_line_breaks=True).convert("one\ntwo"), "one\ntwo")

    def test_nested_lists(self):
        source = "- a\n  - b\n    1. c\n    2. d\n- e"
        expected = "[list]\n[*]a\n[list]\n[*]b\n[olist]\n[*]c\n[*]d\n[/olist]\n[/list]\n[*]e\n[/list]"
        self.assertEqual(convert(source), expected)

    def test_list_type_switch_on_same_level(self):
        self.assertEqual(convert("- a\n1. b"), "[list]\n[*]a\n[/list]\n\n[olist]\n[*]b\n[/olist]")

    def test_list_item_continuation(self):
        self.assertEqual(convert("- a\ncontinued\n\n  more"), "[list]\n[*]a continued\nmore\n[/list]")

    def test_list_ends_at_unindented_paragraph(self):
        self.assertEqual(convert("- a\n\ntext"), "[list]\n[*]a\n[/list]\n\ntext")

    def test_tab_indented_list(self):
        self.assertEqual(convert("- a\n\t- b"), "[list]\n[*]a\n[list]\n[*]b\n[/list]\n[/list]")

    def test_code_block(self):
        source = "```sqf\n  hint \"**x**\";\n```"
        self.assertEqual(convert(source), '[code]\n  hint "**x**";\n[/code]')

    def test_indented_code_block_is_dedented(self):
        self.assertEqual(convert("  ~~~\n    x\n  ~~~"), "[code]\n  x\n[/code]")

    def test_quote(self):
        self.assertEqual(convert("> a\n> b"), "[quote]\na b\n[/quote]")

    def test_github_alert(self):
        self.assertEqual(convert("> [!IMPORTANT]\n> Read this."), "[quote]\n[b]Important[/b]\nRead this.\n[/quote]")

    def test_table(self):
        source = "| A | B \\| C |\n|:--|--:|\n| `1` | **2** |"
        expected = "[table]\n[tr][th]A[/th][th]B | C[/th][/tr]\n[tr][td]1[/td][td][b]2[/b][/td][/tr]\n[/table]"
        self.assertEqual(convert(source), expected)

    def test_crlf_and_bom(self):
        self.assertEqual(convert("﻿# A\r\ntext\r\n"), "[h1]A[/h1]\ntext")


class MarkerTests(unittest.TestCase):
    def test_workshop_skip(self):
        self.assertEqual(convert("a\n<!-- workshop-skip -->\nb\n<!-- /workshop-skip -->\nc"), "a c")

    def test_unclosed_skip_warns(self):
        converter = Converter()
        self.assertEqual(converter.convert("a\n\n<!-- workshop-skip -->\nb"), "a")
        self.assertTrue(any("workshop-skip" in w for w in converter.warnings))

    def test_workshop_only(self):
        self.assertEqual(convert("<!-- workshop-only **Hi** -->"), "[b]Hi[/b]")

    def test_multiline_workshop_only(self):
        self.assertEqual(convert("<!-- workshop-only\n# Title\n-->"), "[h1]Title[/h1]")

    def test_comments_are_removed(self):
        self.assertEqual(convert("a <!-- note --> b"), "a  b")

    def test_markers_inside_code_are_kept(self):
        source = "```\n<!-- workshop-skip -->\n<!-- x -->\n```"
        self.assertEqual(convert(source), "[code]\n<!-- workshop-skip -->\n<!-- x -->\n[/code]")


class FixtureTests(unittest.TestCase):
    def test_full_fixture(self):
        source = (FIXTURES / "description.md").read_text(encoding="utf-8")
        expected = (FIXTURES / "description.bbcode").read_text(encoding="utf-8").strip()
        self.assertEqual(repo_convert(source, base_dir="test/fixtures"), expected)


class CliTests(unittest.TestCase):
    def test_cli_writes_file(self):
        import tempfile

        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp) / "out.txt"
            md2bbcode.main([str(FIXTURES / "description.md"), "--repo", "owner/repo",
                            "--base-dir", "test/fixtures", "-o", str(out)])
            expected = (FIXTURES / "description.bbcode").read_text(encoding="utf-8")
            self.assertEqual(out.read_text(encoding="utf-8"), expected)


if __name__ == "__main__":
    unittest.main()
