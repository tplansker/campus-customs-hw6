"""CODE defense layer: turn a vendor document into the text a PERSON would see, before any model sees it.

The attacks in Problem 5 all hid text that a browser, PDF viewer, or mail app never shows but a plain
text extractor happily returns. These readers keep only visible text and report what they dropped:

  HTML  - drops elements hidden by CSS (display:none, visibility:hidden, opacity:0, font-size <= 2px,
          white text, the `hidden` attribute, aria-hidden, and classes that a <style> block hides).
  PDF   - walks the page's drawing instructions and drops text that is white, invisible (render mode 3),
          smaller than 4 pt, or drawn outside the page.
  .eml  - reads the HTML part when there is one (that is what a mail app shows) through the HTML
          cleaner above; otherwise the plain-text part.
  all   - removes zero-width and other invisible Unicode characters.

Each function returns (visible_text, removed) where `removed` lists the hidden snippets that were dropped.
"""

from __future__ import annotations

import email
import re
from email import policy
from html.parser import HTMLParser
from pathlib import Path

from pypdf import PdfReader
from pypdf.generic import ContentStream

# Zero-width spaces/joiners, word joiner, BOM, soft hyphen, bidi controls, invisible operators.
INVISIBLE_CHARS = re.compile("[­᠎​-‏‪-‮⁠-⁤⁦-⁩﻿]")

MIN_FONT_PT = 4.0          # text smaller than this cannot be read on screen or in print
WHITE = 0.95               # fill colour components at/above this count as white


def strip_invisible(text: str) -> tuple[str, int]:
    cleaned, n = INVISIBLE_CHARS.subn("", text)
    return cleaned, n


# ---------- HTML ----------

_HIDING_CSS = [
    re.compile(r"display\s*:\s*none", re.I),
    re.compile(r"visibility\s*:\s*hidden", re.I),
    re.compile(r"(?<![-\w])opacity\s*:\s*0*(?:\.0+)?\s*(?:;|$|!)", re.I),
    re.compile(r"font-size\s*:\s*(?:0*\.?\d*|[0-2](?:\.\d+)?)\s*(?:px|pt)?\s*(?:;|$|!)", re.I),
    re.compile(r"(?<![-\w])color\s*:\s*(?:white|#fff(?:fff)?|rgba?\(\s*255\s*,\s*255\s*,\s*255)", re.I),
    re.compile(r"(?:height|width|max-height)\s*:\s*0(?:px)?\s*;[^\"']*overflow\s*:\s*hidden", re.I),
    re.compile(r"(?:left|top|text-indent)\s*:\s*-\d{3,}", re.I),
]
_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
_BLOCK = {"p", "tr", "br", "h1", "h2", "h3", "h4", "li", "div", "header", "footer", "title", "table", "section"}


def _css_hides(style: str) -> bool:
    return any(rx.search(style) for rx in _HIDING_CSS)


def _hidden_classes(html: str) -> set[str]:
    """Class names that a <style> block hides, e.g. `.fine { display:none }`."""
    hidden = set()
    for block in re.findall(r"<style[^>]*>(.*?)</style>", html, re.I | re.S):
        for selectors, body in re.findall(r"([^{}]+)\{([^}]*)\}", block):
            if _css_hides(body):
                hidden.update(re.findall(r"\.([\w-]+)", selectors))
    return hidden


class _VisibleHTML(HTMLParser):
    def __init__(self, hidden_classes: set[str]) -> None:
        super().__init__(convert_charrefs=True)
        self.hidden_classes = hidden_classes
        self.stack: list[tuple[str, bool]] = []   # (tag, hidden-by-this-element)
        self.parts: list[str] = []
        self.removed: list[str] = []
        self._removed_buf: list[str] = []

    def _hidden(self) -> bool:
        return any(h for _, h in self.stack)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k.lower(): (v or "") for k, v in attrs}
        hides = (
            tag in ("style", "script", "template", "noscript", "head")
            or "hidden" in a
            or a.get("aria-hidden", "").lower() == "true"
            or _css_hides(a.get("style", ""))
            or bool(self.hidden_classes & set(a.get("class", "").split()))
        )
        if tag in _BLOCK and not self._hidden():
            self.parts.append("\n")
        if tag not in _VOID:
            if hides and not self._hidden() and tag not in ("style", "script", "head"):
                self._removed_buf = []
            self.stack.append((tag, hides))

    def handle_endtag(self, tag: str) -> None:
        if not any(t == tag for t, _ in self.stack):
            return
        while self.stack:
            t, hides = self.stack.pop()
            if hides and not self._hidden() and t not in ("style", "script", "head"):
                text = " ".join("".join(self._removed_buf).split())
                if text:
                    self.removed.append(f"<{t}> hidden by markup: {text}")
                self._removed_buf = []
            if t == tag:
                break
        if tag in ("td", "th") and not self._hidden():
            self.parts.append(" | ")

    def handle_data(self, data: str) -> None:
        if self._hidden():
            if not any(t in ("style", "script", "head") for t, _ in self.stack):
                self._removed_buf.append(data)
        else:
            self.parts.append(data)


def visible_html(html: str) -> tuple[str, list[str]]:
    # <title> lives in <head>, which a reader never sees in the page body; keep it out.
    parser = _VisibleHTML(_hidden_classes(html))
    parser.feed(html)
    parser.close()
    lines = (" ".join(line.split()) for line in "".join(parser.parts).splitlines())
    return "\n".join(line for line in lines if line), parser.removed


# ---------- PDF ----------

def _mul(a: list[float], b: list[float]) -> list[float]:
    """Multiply two PDF 3x3 matrices given as [a b c d e f]."""
    return [
        a[0] * b[0] + a[1] * b[2], a[0] * b[1] + a[1] * b[3],
        a[2] * b[0] + a[3] * b[2], a[2] * b[1] + a[3] * b[3],
        a[4] * b[0] + a[5] * b[2] + b[4], a[4] * b[1] + a[5] * b[3] + b[5],
    ]


def _text_of(operand: object) -> str:
    if isinstance(operand, bytes):
        return operand.decode("latin-1")
    return str(operand)


def visible_pdf_page(page) -> tuple[list[str], list[str]]:
    """Interpret one page's text operators; keep text a viewer would actually show."""
    box = page.mediabox
    x0, y0, x1, y1 = float(box.left), float(box.bottom), float(box.right), float(box.top)
    content = ContentStream(page.get_contents(), page.pdf)

    ctm = [1.0, 0, 0, 1.0, 0, 0]
    fill = (0.0, 0.0, 0.0)          # rgb, 1 = white
    render_mode = 0
    stack: list[tuple] = []
    tm = lm = [1.0, 0, 0, 1.0, 0, 0]
    size, leading = 12.0, 0.0
    kept: list[str] = []
    removed: list[str] = []

    def show(text: str) -> None:
        m = _mul(tm, ctm)
        eff = abs(size) * (m[0] ** 2 + m[1] ** 2) ** 0.5
        x, y = m[4], m[5]
        why = None
        if render_mode in (3, 7):
            why = "invisible render mode"
        elif min(fill) >= WHITE:
            why = "white text"
        elif eff < MIN_FONT_PT:
            why = f"{eff:.1f} pt font"
        elif not (x0 - 1 <= x <= x1 and y0 - 1 <= y <= y1):
            why = f"drawn off the page at ({x:.0f}, {y:.0f})"
        if text.strip():
            (removed.append(f"PDF text hidden ({why}): {text.strip()}") if why else kept.append(text))

    for operands, op in content.operations:
        op = op.decode() if isinstance(op, bytes) else op
        if op == "q":
            stack.append((ctm, fill, render_mode))
        elif op == "Q" and stack:
            ctm, fill, render_mode = stack.pop()
        elif op == "cm":
            ctm = _mul([float(v) for v in operands], ctm)
        elif op == "rg":
            fill = tuple(float(v) for v in operands)
        elif op == "g":
            fill = (float(operands[0]),) * 3
        elif op == "k":
            c, mg, yl, k = (float(v) for v in operands)
            fill = ((1 - c) * (1 - k), (1 - mg) * (1 - k), (1 - yl) * (1 - k))
        elif op == "Tr":
            render_mode = int(operands[0])
        elif op == "BT":
            tm = lm = [1.0, 0, 0, 1.0, 0, 0]
        elif op == "Tf":
            size = float(operands[1])
        elif op == "TL":
            leading = float(operands[0])
        elif op == "Tm":
            tm = lm = [float(v) for v in operands]
        elif op in ("Td", "TD"):
            tx, ty = float(operands[0]), float(operands[1])
            if op == "TD":
                leading = -ty
            tm = lm = _mul([1, 0, 0, 1, tx, ty], lm)
        elif op == "T*":
            tm = lm = _mul([1, 0, 0, 1, 0, -leading], lm)
        elif op == "Tj":
            show(_text_of(operands[0]))
        elif op == "TJ":
            show("".join(_text_of(x) for x in operands[0] if not isinstance(x, (int, float))))
        elif op in ("'", '"'):
            tm = lm = _mul([1, 0, 0, 1, 0, -leading], lm)
            show(_text_of(operands[-1]))
    return kept, removed


def visible_pdf(path: Path) -> tuple[str, list[str]]:
    reader = PdfReader(str(path))
    kept_all, removed_all = [], []
    for page in reader.pages:
        try:
            kept, removed = visible_pdf_page(page)
        except Exception as exc:  # odd PDF: fall back to plain extraction, but say so
            kept, removed = [page.extract_text() or ""], [f"(visibility check failed: {type(exc).__name__}; used plain extraction)"]
        kept_all += kept
        removed_all += removed
    return "\n".join(kept_all).strip(), removed_all


# ---------- Email ----------

def visible_eml(path: Path) -> tuple[str, list[str]]:
    msg = email.message_from_bytes(path.read_bytes(), policy=policy.default)
    headers = "\n".join(f"{h}: {msg[h]}" for h in ("From", "To", "Subject", "Date") if msg[h])
    html_part = msg.get_body(preferencelist=("html",))
    removed: list[str] = []
    if html_part is not None:                     # what a mail app displays
        body, removed = visible_html(html_part.get_content())
    else:
        plain = msg.get_body(preferencelist=("plain",))
        body = plain.get_content() if plain else ""
    for att in msg.iter_attachments():
        removed.append(f"attachment not read: {att.get_filename() or att.get_content_type()}")
    return f"{headers}\n\n{body.strip()}", removed


# ---------- Entry point ----------

def visible_text(path: Path) -> tuple[str, list[str]]:
    """Visible text of an HTML, PDF, or .eml document, plus a list of what was hidden and dropped."""
    suffix = path.suffix.lower()
    if suffix in (".html", ".htm"):
        text, removed = visible_html(path.read_text(encoding="utf-8", errors="replace"))
    elif suffix == ".pdf":
        text, removed = visible_pdf(path)
    elif suffix == ".eml":
        text, removed = visible_eml(path)
    else:
        raise ValueError(f"Unsupported file type '{suffix}'")
    text, n = strip_invisible(text)
    if n:
        removed.append(f"{n} zero-width / invisible characters removed")
    return text, removed
