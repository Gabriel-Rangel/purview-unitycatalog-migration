"""Turn normalized Purview assets into the comments and tags we want in Unity Catalog."""

from __future__ import annotations

import fnmatch
import hashlib
import html
import re
import unicodedata
from dataclasses import dataclass
from html.parser import HTMLParser

from .config import CommentsConfig, TagsConfig

MAX_TAG_LEN = 255
_HTML_TAG = re.compile(r"</?[a-zA-Z][^>]*>")
_BLOCK_TAGS = {"p", "div", "br", "li", "ul", "ol", "tr", "h1", "h2", "h3", "h4", "h5", "h6"}


class _TextExtractor(HTMLParser):
    """Minimal HTML -> markdown-ish text (Purview's rich-text editor emits simple HTML)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "li":
            self.parts.append("\n- ")
        elif tag in ("b", "strong"):
            self.parts.append("**")
        elif tag in ("i", "em"):
            self.parts.append("_")
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("b", "strong"):
            self.parts.append("**")
        elif tag in ("i", "em"):
            self.parts.append("_")
        elif tag in _BLOCK_TAGS and tag != "li":
            self.parts.append("\n")

    def handle_data(self, data):
        self.parts.append(data)


def html_to_text(value: str) -> str:
    if not _HTML_TAG.search(value):
        return html.unescape(value)
    parser = _TextExtractor()
    parser.feed(value)
    parser.close()
    return "".join(parser.parts)


def normalize_comment(value: str | None) -> str:
    """Canonical form used both for what we write and for comparing with UC (stable diffs)."""
    if not value:
        return ""
    text = value.replace("\r\n", "\n").replace("\r", "\n").replace(" ", " ")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def pick_description(attributes: dict, cfg: CommentsConfig) -> tuple[str, str | None]:
    """Return (normalized text, source attribute). userDescription is the steward-curated one."""
    for attr in cfg.source_priority:
        raw = attributes.get(attr)
        if raw and str(raw).strip():
            text = normalize_comment(html_to_text(str(raw)))
            if text:
                return text, attr
    return "", None


def slugify(value: str) -> str:
    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9_]+", "_", ascii_value.lower())
    return re.sub(r"_+", "_", slug).strip("_")


def make_tag_key(prefix: str, kind: str, name: str) -> str:
    key = f"{prefix}{kind}_{slugify(name)}"
    if len(key) > MAX_TAG_LEN:
        digest = hashlib.sha1(key.encode()).hexdigest()[:8]
        key = f"{key[: MAX_TAG_LEN - 9]}_{digest}"
    return key


def clean_tag_value(value: str) -> str:
    return str(value).strip()[:MAX_TAG_LEN]


@dataclass(frozen=True)
class DesiredTag:
    key: str
    value: str
    source: str  # ex.: classification:MICROSOFT.PERSONAL.EMAIL


def _classification_allowed(name: str, cfg: TagsConfig) -> bool:
    included = any(fnmatch.fnmatchcase(name, p) for p in cfg.include)
    excluded = any(fnmatch.fnmatchcase(name, p) for p in cfg.exclude)
    return included and not excluded


def derive_tags(obj: dict, cfg: TagsConfig) -> tuple[list[DesiredTag], list[tuple[DesiredTag, str]]]:
    """Tags for one table or column. Returns (tags, collisions) — a collision is a second tag
    that landed on an existing key with a different value (e.g. two aliases -> same key)."""
    candidates: list[DesiredTag] = []

    if cfg.classifications:
        for c in obj.get("classifications", []):
            name = c["type_name"]
            if not _classification_allowed(name, cfg):
                continue
            aliases = cfg.aliases.get(name)
            if aliases:
                candidates.extend(DesiredTag(a["key"], clean_tag_value(a["value"]), f"classification:{name}") for a in aliases)
            else:
                candidates.append(DesiredTag(make_tag_key(cfg.prefix, "class", name), clean_tag_value(name), f"classification:{name}"))

    if cfg.terms:
        for t in obj.get("terms", []):
            candidates.append(
                DesiredTag(
                    make_tag_key(cfg.prefix, "term", t["name"]),
                    clean_tag_value(t.get("qualified_name") or t["name"]),
                    f"term:{t.get('qualified_name') or t['name']}",
                )
            )

    if cfg.labels:
        for label in obj.get("labels", []):
            candidates.append(DesiredTag(make_tag_key(cfg.prefix, "label", label), "", f"label:{label}"))

    if cfg.managed_attributes:
        for group, attrs in (obj.get("business_attributes") or {}).items():
            for attr, value in (attrs or {}).items():
                if value is None or value == "" or value == []:
                    continue
                text = ", ".join(map(str, value)) if isinstance(value, list) else str(value)
                candidates.append(
                    DesiredTag(make_tag_key(cfg.prefix, "ma", f"{group}_{attr}"), clean_tag_value(text), f"managed_attribute:{group}.{attr}")
                )

    tags: dict[str, DesiredTag] = {}
    collisions: list[tuple[DesiredTag, str]] = []
    for tag in candidates:
        current = tags.get(tag.key)
        if current is None:
            tags[tag.key] = tag
        elif current.value != tag.value:
            collisions.append((tag, f"KEY_COLLISION com {current.source}"))
    return list(tags.values()), collisions
