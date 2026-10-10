"""Help content: index, article files and the AI export (no Qt widgets here).

Layout of <version>/help:
    index.json              groups -> articles (title, one-line subtitle, icon, tint, keywords)
    en/<id>.md, zh/<id>.md  one article per language
    images/en/, images/zh/  screenshots in that language's interface (falls back to the other)

Article file:
    # Title
    > One sentence: what this is for.

    ## <first section = shortest working procedure>
    1. **Step title.** What to do and what you will see. ![](picture.png)
    2. ...

    ## <more sections>        options, what the result means, if it does not work, reference ...
    Markdown text.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

IMAGE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")


@dataclass
class Step:
    title: str
    text: str
    image: str | None = None


@dataclass
class Section:
    title: str
    body: str


@dataclass
class Article:
    id: str
    title: str
    summary: str
    steps_title: str = ""
    steps: list[Step] = field(default_factory=list)
    sections: list[Section] = field(default_factory=list)


def help_directory() -> Path:
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle and (Path(bundle) / "help").is_dir():
        return Path(bundle) / "help"
    return Path(__file__).resolve().parents[2] / "help"


def load_index() -> dict:
    return json.loads((help_directory() / "index.json").read_text(encoding="utf-8"))


def all_articles(index: dict | None = None) -> list[dict]:
    index = index or load_index()
    return [a for g in index["groups"] for a in g["articles"]]


def lang_folder(language: str) -> str:
    return "zh" if str(language).startswith("zh") else "en"


def article_text(article_id: str, language: str) -> str:
    folder = help_directory()
    path = folder / lang_folder(language) / f"{article_id}.md"
    if not path.is_file():
        path = folder / "en" / f"{article_id}.md"
    return path.read_text(encoding="utf-8") if path.is_file() else f"# {article_id}\n"


def image_path(name: str | None, language: str) -> Path | None:
    """Screenshot in the language's own interface, else the other language's."""
    if not name:
        return None
    folder = help_directory() / "images"
    first = lang_folder(language)
    for candidate in (folder / first / name, folder / ("en" if first == "zh" else "zh") / name, folder / name):
        if candidate.is_file():
            return candidate
    return None


def image_focus(name: str | None) -> tuple[float, float, float, float] | None:
    """The outlined area of a screenshot (fractions of the picture), for thumbnails."""
    if not name:
        return None
    path = help_directory() / "images" / "focus.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8")).get(name)
    except (OSError, ValueError):
        return None
    return tuple(float(v) for v in value) if isinstance(value, list) and len(value) == 4 else None


def parse_article(article_id: str, text: str) -> Article:
    lines = text.splitlines()
    title, summary = article_id, []
    index = 0
    while index < len(lines) and not lines[index].startswith("## "):
        line = lines[index]
        if line.startswith("# "):
            title = line[2:].strip()
        elif line.startswith(">"):
            summary.append(line.lstrip("> ").strip())
        index += 1
    article = Article(article_id, title, " ".join(s for s in summary if s))
    blocks: list[tuple[str, list[str]]] = []
    for line in lines[index:]:
        if line.startswith("## "):
            blocks.append((line[3:].strip(), []))
        elif blocks:
            blocks[-1][1].append(line)
    if blocks:
        first_title, first_lines = blocks[0]
        article.steps_title = first_title
        article.steps = _steps(first_lines)
        extra = [line for line in first_lines if not re.match(r"^\s*(\d+\.|\s{2,})", line) and line.strip()]
        if not article.steps:
            article.sections.append(Section(first_title, "\n".join(first_lines).strip()))
        elif extra:
            article.sections.append(Section("", "\n".join(extra).strip()))
    for title_, body in blocks[1:]:
        article.sections.append(Section(title_, "\n".join(body).strip()))
    return article


def _steps(lines: list[str]) -> list[Step]:
    items: list[list[str]] = []
    for line in lines:
        if re.match(r"^\d+\.\s", line):
            items.append([re.sub(r"^\d+\.\s+", "", line)])
        elif items and (line.startswith("   ") or line.startswith("\t")) and line.strip():
            items[-1].append(line.strip())
    steps = []
    for item in items:
        text = " ".join(item)
        image = None
        found = IMAGE.search(text)
        if found:
            image = found.group(1).strip()
            text = IMAGE.sub("", text).strip()
        bold = re.match(r"^\*\*(.+?)\*\*\s*(.*)$", text)
        if bold:
            steps.append(Step(bold.group(1).strip().rstrip("。.．"), bold.group(2).strip(), image))
        else:
            steps.append(Step(text, "", image))
    return steps


def load_article(article_id: str, language: str) -> Article:
    return parse_article(article_id, article_text(article_id, language))


# -- AI export -----------------------------------------------------------------------------------
def build_ai_export(include_state: bool = False, state: str = "") -> str:
    from app import __version__

    index = load_index()
    parts = [
        f"# LabLogViewer {__version__} — user guide for AI assistants",
        "",
        "Instructions for the AI: answer the user's question about LabLogViewer using this guide. Reply in the "
        "user's language. Say where things are (window, menu, button name) and give numbered steps. Explain what "
        "results mean and what to check when something fails, as the guide does. The interface may be in English "
        "or Traditional Chinese; Chinese names are given in brackets. If the guide does not cover the question, say so.",
        "",
    ]
    if include_state and state:
        parts += ["## The user's current state", state, ""]
    for group in index["groups"]:
        parts.append(f"## {group['title']['en']} ({group['title']['zh']})")
        for entry in group["articles"]:
            article = load_article(entry["id"], "en")
            parts.append(f"### {article.title} ({entry['title']['zh']})")
            if article.summary:
                parts.append(article.summary)
            if article.steps:
                parts.append(f"#### {article.steps_title}")
                parts += [f"{n}. {s.title} {s.text}".rstrip() for n, s in enumerate(article.steps, 1)]
            for section in article.sections:
                if section.title:
                    parts.append(f"#### {section.title}")
                parts.append(section.body)
            parts.append("")
    return "\n".join(parts).rstrip() + "\n"
