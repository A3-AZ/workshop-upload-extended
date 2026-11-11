"""Convert GitHub-flavoured Markdown into Steam BBCode for Workshop descriptions.

Supported: headings, bold / italic / strikethrough, inline code, links, images
(also images inside links, like badges), nested and numbered lists, fenced code
blocks, quotes and GitHub alerts (> [!NOTE]), tables, horizontal rules and a
small amount of inline HTML (<img>, <br>).

Relative links and images are resolved against a GitHub repository when one is
given. Images Steam can't show (SVG, shields.io / badgen badges) are replaced by
their alt text.

Markers in the Markdown source:
    <!-- workshop-skip --> ... <!-- /workshop-skip -->   left out of the output
    <!-- workshop-only Text only on the Workshop -->     only shown in the output

Can also be used from the command line:
    python md2bbcode.py README.md --repo owner/repo --ref main -o description.txt
"""

from __future__ import annotations

import argparse
import html
import re
import sys
from dataclasses import dataclass, field

STEAM_DESCRIPTION_LIMIT = 8000

_OPEN = "\x01"
_CLOSE = "\x02"
_TOKEN = re.compile(_OPEN + r"(\d+)" + _CLOSE)

# Link target: (url), (<url>), (url "title")
_TARGET = r'\(\s*<?([^)\s>]+)>?(?:\s+"[^"]*")?\s*\)'

_FENCE = re.compile(r"^(\s*)(```|~~~)")
_HEADING = re.compile(r"^\s{0,3}(#{1,6})(?:\s+(.*?))?(?:\s+#+)?\s*$")
_SETEXT = re.compile(r"^\s{0,3}(=+|-+)\s*$")
_RULE = re.compile(r"^\s{0,3}([-*_])(\s*\1){2,}\s*$")
_QUOTE = re.compile(r"^\s{0,3}>\s?(.*)$")
_ALERT = re.compile(r"^\s*\[!(NOTE|TIP|IMPORTANT|WARNING|CAUTION)\]\s*$")
_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$")
_LIST_ITEM = re.compile(r"^(\s*)([-*+]|\d{1,9}[.)])\s+(.*)$")


@dataclass
class _Block:
    kind: str
    text: str


@dataclass
class Converter:
    """Markdown to Steam BBCode converter.

    repo:     "owner/name" of the GitHub repository relative links point to.
              Without it, relative links are left as plain text.
    ref:      branch, tag or commit used for relative links.
    base_dir: folder of the Markdown file inside the repository, relative
              links start there ("" = repository root).
    keep_line_breaks: keep single line breaks inside paragraphs instead of
              joining the lines like GitHub does.
    """

    repo: str | None = None
    ref: str = "main"
    base_dir: str = ""
    keep_line_breaks: bool = False
    warnings: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------ urls

    def _warn(self, message: str) -> None:
        if message not in self.warnings:
            self.warnings.append(message)

    def resolve_url(self, url: str, image: bool) -> str | None:
        if url.startswith("#"):
            return None  # Steam has no anchors
        if re.match(r"^[a-zA-Z][a-zA-Z0-9+.\-]*:", url):
            return url
        if url.startswith("//"):
            return "https:" + url
        if not self.repo:
            self._warn("No GitHub repository known - relative links are left as text.")
            return None

        suffix = ""
        cut = min((i for i in (url.find("?"), url.find("#")) if i >= 0), default=-1)
        if cut >= 0:
            suffix = url[cut:]
            url = url[:cut]

        full = url if url.startswith("/") else f"{self.base_dir}/{url}"
        parts: list[str] = []
        for part in full.split("/"):
            if part in ("", "."):
                continue
            if part == "..":
                if parts:
                    parts.pop()
                continue
            parts.append(part)
        relative = "/".join(parts).replace(" ", "%20")

        if image:
            return f"https://raw.githubusercontent.com/{self.repo}/{self.ref}/{relative}"
        return f"https://github.com/{self.repo}/blob/{self.ref}/{relative}{suffix}"

    def _image(self, src: str) -> str | None:
        """BBCode for an image, or None when Steam can't show it."""
        url = self.resolve_url(src, True)
        if not url:
            return None
        if re.search(r"\.svg($|[?#])", url, re.IGNORECASE) or re.match(
            r"^https?://(img\.shields\.io|badgen\.net)/", url, re.IGNORECASE
        ):
            return None
        return f"[img]{url}[/img]"

    # ---------------------------------------------------------------- inline

    def inline(self, text: str) -> str:
        stash: list[str] = []

        # Finished parts are stashed behind a token, so later steps can't change them
        def save(value: str) -> str:
            stash.append(value)
            return f"{_OPEN}{len(stash) - 1}{_CLOSE}"

        def code(m: re.Match) -> str:
            value = m.group(2)
            if re.search(r"\[/?[A-Za-z*]", value):
                value = f"[noparse]{value}[/noparse]"
            return save(value)

        text = re.sub(r"(`+)\s?(.+?)\s?\1", code, text)
        text = re.sub(r"\\([\\`*_{}\[\]()#+\-.!|<>~])", lambda m: save(m.group(1)), text)
        text = re.sub(r"(?<!\()<(https?://[^>\s]+)>", lambda m: save(m.group(1)), text)

        # Image inside a link, like a badge
        def linked_image(m: re.Match) -> str:
            inner = self._image(m.group(2)) or m.group(1)
            href = self.resolve_url(m.group(3), False)
            if not inner:
                inner = href or ""
            return save(f"[url={href}]{inner}[/url]" if href else inner)

        text = re.sub(r"\[!\[([^\]]*)\]" + _TARGET + r"\]" + _TARGET, linked_image, text)

        def image(m: re.Match) -> str:
            img = self._image(m.group(2))
            return save(img) if img else m.group(1)

        text = re.sub(r"!\[([^\]]*)\]" + _TARGET, image, text)

        def html_image(m: re.Match) -> str:
            src = re.search(r"""\bsrc\s*=\s*["']([^"']+)["']""", m.group(0))
            alt = re.search(r"""\balt\s*=\s*["']([^"']*)["']""", m.group(0))
            img = self._image(src.group(1)) if src else None
            return save(img) if img else (alt.group(1) if alt else "")

        text = re.sub(r"<img\b[^>]*>", html_image, text, flags=re.IGNORECASE)

        def link(m: re.Match) -> str:
            href = self.resolve_url(m.group(2), False)
            if href:
                return f"[url={save(href)}]{m.group(1)}[/url]"
            return m.group(1)

        text = re.sub(r"\[([^\]]+)\]" + _TARGET, link, text)
        text = re.sub(r"(?<![=\w])https?://[^\s\[\]<>\"]+", lambda m: save(m.group(0)), text)

        # Remaining HTML
        text = re.sub(r"[ \t]*<br\s*/?>[ \t]*", "\n", text, flags=re.IGNORECASE)
        text = re.sub(r"</?[A-Za-z][^>]*>", "", text)

        text = re.sub(r"\*\*(?=\S)(.+?)(?<=\S)\*\*", r"[b]\1[/b]", text)
        text = re.sub(r"(?<!\w)__(?=\S)(.+?)(?<=\S)__(?!\w)", r"[b]\1[/b]", text)
        text = re.sub(r"(?<!\w)\*(?=\S)(.+?)(?<=\S)\*(?!\w)", r"[i]\1[/i]", text)
        text = re.sub(r"(?<!\w)_(?=\S)(.+?)(?<=\S)_(?!\w)", r"[i]\1[/i]", text)
        text = re.sub(r"~~(?=\S)(.+?)(?<=\S)~~", r"[strike]\1[/strike]", text)

        text = html.unescape(text)

        # Stashed parts can contain tokens themselves
        while _TOKEN.search(text):
            text = _TOKEN.sub(lambda m: stash[int(m.group(1))], text)
        return text

    def _paragraph(self, lines: list[str]) -> str:
        result = ""
        for n, line in enumerate(lines):
            hard_break = self.keep_line_breaks or re.search(r"(\s{2,}|\\)$", line) is not None
            line = line.strip()
            if line.endswith("\\") and not line.endswith("\\\\"):
                line = line[:-1].rstrip()
            result += line
            if n < len(lines) - 1:
                result += "\n" if hard_break else " "
        return self.inline(result)

    def _table_row(self, row: str) -> list[str]:
        row = row.strip()
        if row.startswith("|"):
            row = row[1:]
        if row.endswith("|") and not row.endswith("\\|"):
            row = row[:-1]
        return [self.inline(cell.strip().replace("\\|", "|")) for cell in re.split(r"(?<!\\)\|", row)]

    # ---------------------------------------------------------------- blocks

    @staticmethod
    def _join(blocks: list[_Block]) -> str:
        result = ""
        for n, block in enumerate(blocks):
            if n > 0:
                result += "\n" if blocks[n - 1].kind == "heading" else "\n\n"
            result += block.text
        return result

    def _blocks(self, lines: list[str]) -> list[_Block]:
        blocks: list[_Block] = []
        para: list[str] = []
        lists: list[tuple[int, str]] = []  # open lists (indent, tag), innermost last
        list_text: list[str] = []
        item: str | None = None  # current list item, written when it ends

        def flush_para() -> None:
            if para:
                text = self._paragraph(para)
                if text.strip():
                    blocks.append(_Block("text", text.strip()))
                para.clear()

        def flush_item() -> None:
            nonlocal item
            if item is not None:
                list_text.append("[*]" + self.inline(item))
                item = None

        def close_list() -> None:
            flush_item()
            list_text.append(f"[/{lists[-1][1]}]")
            lists.pop()
            if not lists:
                blocks.append(_Block("list", "\n".join(list_text).rstrip()))
                list_text.clear()

        def close_all() -> None:
            flush_para()
            while lists:
                close_list()

        i = 0
        while i < len(lines):
            line = lines[i]
            prev_blank = i == 0 or not lines[i - 1].strip()

            if not line.strip():
                flush_para()
                i += 1
                continue

            # Fenced code block
            m = _FENCE.match(line)
            if m:
                fence_indent = len(m.group(1))
                fence = m.group(2)
                close_all()
                code: list[str] = []
                i += 1
                end = re.compile(r"^\s*" + re.escape(fence))
                while i < len(lines) and not end.match(lines[i]):
                    code_line = lines[i]
                    lead = len(code_line) - len(code_line.lstrip())
                    code.append(code_line[min(lead, fence_indent):])
                    i += 1
                i += 1
                blocks.append(_Block("code", "[code]\n" + "\n".join(code) + "\n[/code]"))
                continue

            # Heading - an empty one ("##") is used as a divider
            m = _HEADING.match(line)
            if m:
                level = min(len(m.group(1)), 3)
                heading = m.group(2) or ""
                close_all()
                if heading.strip():
                    blocks.append(_Block("heading", f"[h{level}]{self.inline(heading.strip())}[/h{level}]"))
                else:
                    blocks.append(_Block("rule", "[hr][/hr]"))
                i += 1
                continue

            # Underlined heading
            m = _SETEXT.match(line)
            if para and m:
                level = 1 if m.group(1).startswith("=") else 2
                heading = self._paragraph(para)
                para.clear()
                blocks.append(_Block("heading", f"[h{level}]{heading}[/h{level}]"))
                i += 1
                continue

            if _RULE.match(line):
                close_all()
                blocks.append(_Block("rule", "[hr][/hr]"))
                i += 1
                continue

            # Quote, GitHub alerts get their type as a title
            if _QUOTE.match(line):
                close_all()
                quote: list[str] = []
                while i < len(lines):
                    qm = _QUOTE.match(lines[i])
                    if not qm:
                        break
                    quote.append(qm.group(1))
                    i += 1
                title = ""
                am = _ALERT.match(quote[0])
                if am:
                    title = f"[b]{am.group(1).capitalize()}[/b]\n"
                    quote.pop(0)
                inner = self._join(self._blocks(quote))
                blocks.append(_Block("quote", f"[quote]\n{title}{inner}\n[/quote]"))
                continue

            # Table
            if "|" in line and i + 1 < len(lines) and "|" in lines[i + 1] and _TABLE_SEP.match(lines[i + 1]):
                close_all()
                rows = ["[tr]" + "".join(f"[th]{c}[/th]" for c in self._table_row(line)) + "[/tr]"]
                i += 2
                while i < len(lines) and lines[i].strip() and "|" in lines[i]:
                    rows.append("[tr]" + "".join(f"[td]{c}[/td]" for c in self._table_row(lines[i])) + "[/tr]")
                    i += 1
                blocks.append(_Block("table", "[table]\n" + "\n".join(rows) + "\n[/table]"))
                continue

            # List item
            m = _LIST_ITEM.match(line)
            if m:
                indent = len(m.group(1).replace("\t", "    "))
                tag = "olist" if m.group(2)[0].isdigit() else "list"
                flush_para()
                flush_item()
                while lists and lists[-1][0] > indent:
                    close_list()
                if lists and lists[-1][0] == indent and lists[-1][1] != tag:
                    close_list()
                if not lists or lists[-1][0] < indent:
                    list_text.append(f"[{tag}]")
                    lists.append((indent, tag))
                item = m.group(3)
                i += 1
                continue

            # Continued list item - indented, or directly below it
            if lists:
                lead = len(re.match(r"^\s*", line).group(0).replace("\t", "    "))
                if item is not None and (lead >= 2 or not prev_blank):
                    item += ("\n" if prev_blank else " ") + line.strip()
                    i += 1
                    continue
                close_all()

            para.append(line)
            i += 1

        close_all()
        return blocks

    # ------------------------------------------------------------------ main

    def _preprocess(self, source: str) -> list[str]:
        """Apply the workshop markers and drop HTML comments, outside of code blocks."""
        kept: list[str] = []
        chunk: list[str] = []
        skip = False
        fence: str | None = None

        def flush_chunk() -> None:
            if chunk:
                text = "\n".join(chunk)
                text = re.sub(r"<!--\s*workshop-only\s?(.*?)\s*-->", r"\1", text, flags=re.DOTALL)
                text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
                kept.extend(text.split("\n"))
                chunk.clear()

        for line in source.split("\n"):
            if fence:
                if re.match(r"^\s*" + re.escape(fence), line):
                    fence = None
                if not skip:
                    kept.append(line)
                continue
            m = _FENCE.match(line)
            if m:
                fence = m.group(2)
                flush_chunk()
                if not skip:
                    kept.append(line)
                continue
            if re.search(r"<!--\s*workshop-skip\s*-->", line):
                skip = True
                continue
            if re.search(r"<!--\s*/workshop-skip\s*-->", line):
                skip = False
                continue
            if not skip:
                chunk.append(line)
        flush_chunk()
        if skip:
            self._warn("<!-- workshop-skip --> without <!-- /workshop-skip --> - skipped to the end of the file.")
        return kept

    def convert(self, source: str) -> str:
        source = source.replace("\r\n", "\n").replace("\r", "\n")
        if source.startswith("﻿"):
            source = source[1:]
        result = self._join(self._blocks(self._preprocess(source)))
        return re.sub(r"\n{3,}", "\n\n", result).strip()


def convert(source: str, repo: str | None = None, ref: str = "main", base_dir: str = "",
            keep_line_breaks: bool = False) -> str:
    """Convert Markdown to Steam BBCode. See Converter for the options."""
    return Converter(repo=repo, ref=ref, base_dir=base_dir, keep_line_breaks=keep_line_breaks).convert(source)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Convert Markdown to Steam Workshop BBCode.")
    parser.add_argument("file", help="Markdown file, '-' for stdin")
    parser.add_argument("-o", "--output", help="output file (default: stdout)")
    parser.add_argument("--repo", help="GitHub repository (owner/name) for relative links")
    parser.add_argument("--ref", default="main", help="branch, tag or commit for relative links (default: main)")
    parser.add_argument("--base-dir", default="", help="folder of the file inside the repository")
    parser.add_argument("--keep-line-breaks", action="store_true", help="keep line breaks inside paragraphs")
    args = parser.parse_args(argv)

    if args.file == "-":
        source = sys.stdin.read()
    else:
        with open(args.file, encoding="utf-8") as handle:
            source = handle.read()

    converter = Converter(repo=args.repo, ref=args.ref, base_dir=args.base_dir.strip("/"),
                          keep_line_breaks=args.keep_line_breaks)
    result = converter.convert(source)
    for warning in converter.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    if len(result) > STEAM_DESCRIPTION_LIMIT:
        print(f"warning: {len(result)} characters, Steam allows {STEAM_DESCRIPTION_LIMIT}.", file=sys.stderr)

    if args.output:
        with open(args.output, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(result + "\n")
    else:
        sys.stdout.write(result + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
