import base64
import contextlib
import mimetypes
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import requests
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route


VAULT_ROOT = Path(os.environ.get("VAULT_ROOT", "/vault")).resolve()
PUBLIC_HOST = os.environ.get("PUBLIC_HOST", "obsidian-mcp.lizhuchao.live").strip()
LOCAL_TZ = ZoneInfo("Asia/Shanghai")

INBOX_REL = "00_Inbox/ChatGPT_To_Process"
CAPTURE_REL = "00_Inbox/Capture"
KNOWLEDGE_REL = "Knowledge"
SOURCES_REL = "Sources"
TEMPLATES_REL = "Templates"
ASSETS_REL = "Assets/ChatGPT"
ARCHIVE_REL = "Archive"
VERSIONS_REL = "Archive/Versions"
DELETED_REL = "Archive/Deleted"

INBOX_DIR = (VAULT_ROOT / INBOX_REL).resolve()
CAPTURE_DIR = (VAULT_ROOT / CAPTURE_REL).resolve()
KNOWLEDGE_DIR = (VAULT_ROOT / KNOWLEDGE_REL).resolve()
SOURCES_DIR = (VAULT_ROOT / SOURCES_REL).resolve()
TEMPLATES_DIR = (VAULT_ROOT / TEMPLATES_REL).resolve()
ASSETS_DIR = (VAULT_ROOT / ASSETS_REL).resolve()
ARCHIVE_DIR = (VAULT_ROOT / ARCHIVE_REL).resolve()
VERSIONS_DIR = (VAULT_ROOT / VERSIONS_REL).resolve()
DELETED_DIR = (VAULT_ROOT / DELETED_REL).resolve()

MAX_CONTENT_BYTES = int(os.environ.get("MAX_CONTENT_BYTES", "300000"))
MAX_ATTACHMENT_BYTES = int(os.environ.get("MAX_ATTACHMENT_BYTES", "15000000"))

APP_VERSION = "2026-05-16-lifecycle-archive-rollback-asia-shanghai-inbox-archive-no-auth"


def now_local() -> str:
    return datetime.now(LOCAL_TZ).strftime("%Y-%m-%d %H-%M-%S")


def now_month() -> str:
    return datetime.now(LOCAL_TZ).strftime("%Y-%m")


def now_iso() -> str:
    return datetime.now(LOCAL_TZ).isoformat()


def archive_safe_timestamp() -> str:
    return datetime.now(LOCAL_TZ).strftime("%Y-%m-%dT%H-%M-%S%z")


def ensure_inside(path: Path, root: Path) -> Path:
    resolved = path.resolve()
    root_resolved = root.resolve()
    if resolved != root_resolved and root_resolved not in resolved.parents:
        raise ValueError(f"Path escapes allowed root: {path}")
    return resolved


def safe_filename(name: str) -> str:
    name = name.strip()
    name = re.sub(r"[\\/:*?\"<>|#\[\]\n\r\t]+", "-", name)
    name = re.sub(r"\s+", " ", name)
    name = name.strip(" .-")
    if not name:
        name = "untitled"
    return name[:160]


def safe_markdown_filename(name: str) -> str:
    name = safe_filename(name)
    if not name.endswith(".md"):
        name += ".md"
    return name[:180]


def safe_asset_filename(name: str, default_ext: str = ".png") -> str:
    name = safe_filename(name)
    suffix = Path(name).suffix.lower()
    if not suffix:
        name += default_ext
    return name[:180]


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path

    stem = path.stem
    suffix = path.suffix
    parent = path.parent

    for i in range(2, 1000):
        candidate = parent / f"{stem}-{i}{suffix}"
        if not candidate.exists():
            return candidate

    raise FileExistsError("Cannot create unique filename")


def read_text_file(path: Path) -> str:
    if path.stat().st_size > MAX_CONTENT_BYTES:
        raise ValueError("File too large")
    return path.read_text(encoding="utf-8")


def write_text_file(path: Path, content: str) -> None:
    if len(content.encode("utf-8")) > MAX_CONTENT_BYTES:
        raise ValueError(f"content too large; max {MAX_CONTENT_BYTES} bytes")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content if content.endswith("\n") else content + "\n", encoding="utf-8")


def normalize_inbox_path(path_or_filename: str) -> Path:
    raw = path_or_filename.strip()
    if raw.startswith(INBOX_REL + "/"):
        candidate = (VAULT_ROOT / raw).resolve()
    else:
        candidate = (INBOX_DIR / raw).resolve()

    ensure_inside(candidate, INBOX_DIR)

    if candidate.suffix != ".md":
        raise ValueError("Only Markdown files are allowed")

    return candidate


def normalize_knowledge_path(relative_path: str) -> Path:
    raw = relative_path.strip().lstrip("/")
    candidate = (VAULT_ROOT / raw).resolve()

    ensure_inside(candidate, KNOWLEDGE_DIR)

    if ".obsidian" in candidate.parts:
        raise ValueError("Access to .obsidian is forbidden")

    if candidate.suffix != ".md":
        raise ValueError("Only Markdown files are supported")

    return candidate


def normalize_archive_path(relative_path: str) -> Path:
    raw = relative_path.strip().lstrip("/")
    candidate = (VAULT_ROOT / raw).resolve()

    allowed_roots = [VERSIONS_DIR, DELETED_DIR]

    if not any(candidate == root or root in candidate.parents for root in allowed_roots):
        raise ValueError("Path is not in an allowed archive area")

    if candidate.suffix != ".md":
        raise ValueError("Only Markdown archive files are readable")

    return candidate


def normalize_read_path(relative_path: str) -> Path:
    raw = relative_path.strip().lstrip("/")
    candidate = (VAULT_ROOT / raw).resolve()

    allowed_roots = [
        INBOX_DIR,
        CAPTURE_DIR,
        KNOWLEDGE_DIR,
        SOURCES_DIR,
        TEMPLATES_DIR,
        ASSETS_DIR,
    ]

    if not any(candidate == root or root in candidate.parents for root in allowed_roots):
        raise ValueError("Path is not in an allowed read area")

    if ".obsidian" in candidate.parts:
        raise ValueError("Access to .obsidian is forbidden")

    if candidate.suffix != ".md":
        raise ValueError("Only Markdown files are readable through fetch_note")

    return candidate


def set_frontmatter(text: str, updates: dict[str, str]) -> str:
    if text.startswith("---\n"):
        end = text.find("\n---", 4)
        if end != -1:
            frontmatter = text[4:end].strip().splitlines()
            body = text[end + 4 :].lstrip("\n")

            data: dict[str, str] = {}
            order: list[str] = []

            for line in frontmatter:
                if ":" in line and not line.startswith(" "):
                    key, value = line.split(":", 1)
                    key = key.strip()
                    data[key] = value.strip()
                    order.append(key)
                else:
                    order.append(line)

            for key, value in updates.items():
                if key not in data:
                    order.append(key)
                data[key] = value

            rendered = []
            seen = set()
            for key in order:
                if key in data and key not in seen:
                    rendered.append(f"{key}: {data[key]}")
                    seen.add(key)

            return "---\n" + "\n".join(rendered) + "\n---\n\n" + body

    rendered = "\n".join(f"{k}: {v}" for k, v in updates.items())
    return "---\n" + rendered + "\n---\n\n" + text.lstrip()


def frontmatter_value(text: str, key: str) -> str | None:
    if not text.startswith("---\n"):
        return None

    end = text.find("\n---", 4)
    if end == -1:
        return None

    fm = text[4:end].splitlines()
    prefix = key + ":"

    for line in fm:
        if line.startswith(prefix):
            return line[len(prefix):].strip()

    return None


def next_version_number(text: str) -> int:
    raw = frontmatter_value(text, "version")
    if raw is None:
        return 1

    try:
        return int(raw) + 1
    except ValueError:
        return 1


def title_from_markdown(path: Path, text: str) -> str:
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return path.stem


def snippet(text: str, terms: list[str], max_len: int = 360) -> str:
    lower = text.lower()
    first = -1
    for term in terms:
        idx = lower.find(term.lower())
        if idx != -1:
            first = idx
            break

    if first == -1:
        first = 0

    start = max(0, first - 120)
    end = min(len(text), start + max_len)
    s = text[start:end].replace("\n", " ").strip()
    return re.sub(r"\s+", " ", s)


def all_markdown_files(root: Path) -> list[Path]:
    if not root.exists():
        return []

    files = []
    for p in root.rglob("*.md"):
        if ".obsidian" in p.parts:
            continue
        files.append(p.resolve())
    return files


def asset_month_dir() -> Path:
    target = (ASSETS_DIR / now_month()).resolve()
    ensure_inside(target, ASSETS_DIR)
    target.mkdir(parents=True, exist_ok=True)
    return target


def guess_ext_from_content_type(content_type: str | None) -> str:
    if not content_type:
        return ".bin"

    content_type = content_type.split(";")[0].strip().lower()

    mapping = {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/webp": ".webp",
        "image/gif": ".gif",
        "image/heic": ".heic",
        "image/heif": ".heif",
        "application/pdf": ".pdf",
    }

    if content_type in mapping:
        return mapping[content_type]

    guessed = mimetypes.guess_extension(content_type)
    return guessed or ".bin"


def is_allowed_asset_suffix(path: Path) -> bool:
    return path.suffix.lower() in {
        ".png", ".jpg", ".jpeg", ".webp", ".gif", ".heic", ".heif", ".pdf"
    }


def obsidian_embed(relative_path: str) -> str:
    return f"![[{relative_path}]]"


def note_archive_dir(base_archive_dir: Path, knowledge_relative_path: str) -> Path:
    clean = knowledge_relative_path.strip().lstrip("/")
    if not clean.startswith(KNOWLEDGE_REL + "/"):
        raise ValueError("Only Knowledge notes can be versioned or archived")

    no_suffix = str(Path(clean).with_suffix(""))
    target = (base_archive_dir / no_suffix).resolve()
    ensure_inside(target, base_archive_dir)
    target.mkdir(parents=True, exist_ok=True)
    return target


def inbox_archive_dir(path: Path) -> Path:
    ensure_inside(path, INBOX_DIR)
    target = (DELETED_DIR / "Inbox" / safe_filename(path.stem)).resolve()
    ensure_inside(target, DELETED_DIR)
    target.mkdir(parents=True, exist_ok=True)
    return target


def archive_current_knowledge_version(path: Path, archive_status: str, reason: str | None = None) -> dict[str, Any]:
    ensure_inside(path, KNOWLEDGE_DIR)

    if not path.exists():
        raise FileNotFoundError("Knowledge note not found")

    relative = str(path.relative_to(VAULT_ROOT))
    old_text = read_text_file(path)

    updates = {
        "status": archive_status,
        "archived_at": now_iso(),
        "archived_from": relative,
    }

    if reason:
        updates["archived_reason"] = reason

    archived_text = set_frontmatter(old_text, updates)

    target_dir = note_archive_dir(VERSIONS_DIR, relative)
    target = unique_path(target_dir / safe_markdown_filename(archive_safe_timestamp()))

    write_text_file(target, archived_text)

    return {
        "relative_path": str(target.relative_to(VAULT_ROOT)),
        "size_bytes": target.stat().st_size,
    }


mcp = FastMCP(
    "obsidian-kb-business",
    stateless_http=True,
    json_response=True,
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[
            PUBLIC_HOST,
            f"{PUBLIC_HOST}:*",
            "localhost:*",
            "127.0.0.1:*",
            "[::1]:*",
        ],
        allowed_origins=["*"],
    ),
)


@mcp.tool()
def health_check() -> dict[str, Any]:
    """Check the Obsidian KB MCP server status and allowed business operations."""
    for d in [
        INBOX_DIR, CAPTURE_DIR, KNOWLEDGE_DIR, SOURCES_DIR, TEMPLATES_DIR,
        ASSETS_DIR, ARCHIVE_DIR, VERSIONS_DIR, DELETED_DIR
    ]:
        d.mkdir(parents=True, exist_ok=True)

    return {
        "ok": True,
        "version": APP_VERSION,
        "timezone": "Asia/Shanghai",
        "vault_root": str(VAULT_ROOT),
        "business_flow": [
            "create_inbox_note",
            "create_inbox_note_with_attachments",
            "replace_inbox_note",
            "append_inbox_note",
            "archive_inbox_note",
            "save_attachment_from_url",
            "save_attachment_base64",
            "fetch_inbox_note",
            "promote_inbox_note",
            "search_notes",
            "fetch_note",
            "update_knowledge_note",
            "append_knowledge_note",
            "archive_knowledge_note",
            "list_versions",
            "fetch_version",
            "rollback_knowledge_note",
        ],
        "write_allowed": [
            INBOX_REL,
            CAPTURE_REL,
            ASSETS_REL,
            "replace/append existing Inbox drafts",
            "soft archive Inbox drafts into Archive/Deleted/Inbox",
            "promote from Inbox to Knowledge",
            "update Knowledge with automatic version archive",
            "soft archive Knowledge into Archive/Deleted",
            "rollback Knowledge from Archive/Versions",
        ],
        "read_allowed": [
            INBOX_REL,
            KNOWLEDGE_REL,
            SOURCES_REL,
            TEMPLATES_REL,
            ASSETS_REL,
            VERSIONS_REL,
            DELETED_REL,
        ],
        "forbidden": [
            "physical delete",
            "overwrite without archive",
            "write .obsidian",
            "access outside vault",
        ],
    }


@mcp.tool()
def save_attachment_from_url(url: str, filename: str | None = None) -> dict[str, Any]:
    """
    Download an image/PDF from a URL and save it under Assets/ChatGPT/YYYY-MM.
    Returns an Obsidian embed string for Markdown notes.
    """
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Only http/https URLs are allowed")

    with requests.get(url, stream=True, timeout=30) as response:
        response.raise_for_status()

        content_type = response.headers.get("content-type")
        default_ext = guess_ext_from_content_type(content_type)

        if filename and filename.strip():
            final_name = safe_asset_filename(filename, default_ext=default_ext)
        else:
            url_name = Path(parsed.path).name
            final_name = safe_asset_filename(
                url_name or f"{now_local()} attachment{default_ext}",
                default_ext=default_ext,
            )

        target = unique_path(ensure_inside(asset_month_dir() / final_name, ASSETS_DIR))

        if not is_allowed_asset_suffix(target):
            raise ValueError("Only image/PDF attachments are allowed")

        size = 0
        with target.open("wb") as f:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if not chunk:
                    continue
                size += len(chunk)
                if size > MAX_ATTACHMENT_BYTES:
                    raise ValueError(f"attachment too large; max {MAX_ATTACHMENT_BYTES} bytes")
                f.write(chunk)

    relative_path = str(target.relative_to(VAULT_ROOT))

    return {
        "ok": True,
        "action": "saved_attachment_from_url",
        "relative_path": relative_path,
        "size_bytes": target.stat().st_size,
        "embed": obsidian_embed(relative_path),
    }


@mcp.tool()
def save_attachment_base64(filename: str, data_base64: str) -> dict[str, Any]:
    """
    Save a base64-encoded image/PDF under Assets/ChatGPT/YYYY-MM.
    Returns an Obsidian embed string for Markdown notes.
    """
    if not filename.strip():
        raise ValueError("filename is required")

    target = unique_path(ensure_inside(asset_month_dir() / safe_asset_filename(filename), ASSETS_DIR))

    if not is_allowed_asset_suffix(target):
        raise ValueError("Only image/PDF attachments are allowed")

    try:
        raw = base64.b64decode(data_base64, validate=True)
    except Exception as exc:
        raise ValueError(f"Invalid base64 data: {exc}")

    if len(raw) > MAX_ATTACHMENT_BYTES:
        raise ValueError(f"attachment too large; max {MAX_ATTACHMENT_BYTES} bytes")

    target.write_bytes(raw)

    relative_path = str(target.relative_to(VAULT_ROOT))

    return {
        "ok": True,
        "action": "saved_attachment_base64",
        "relative_path": relative_path,
        "size_bytes": target.stat().st_size,
        "embed": obsidian_embed(relative_path),
    }


@mcp.tool()
def create_inbox_note(title: str, content: str) -> dict[str, Any]:
    """
    Create a Markdown draft in 00_Inbox/ChatGPT_To_Process.
    Use this when the user says: 总结，归档.
    """
    if not title.strip():
        raise ValueError("title is required")
    if not content.strip():
        raise ValueError("content is required")

    INBOX_DIR.mkdir(parents=True, exist_ok=True)

    filename = safe_markdown_filename(f"{now_local()} {title}")
    target = unique_path(ensure_inside(INBOX_DIR / filename, INBOX_DIR))

    write_text_file(target, content)

    return {
        "ok": True,
        "action": "created_inbox_note",
        "filename": target.name,
        "relative_path": str(target.relative_to(VAULT_ROOT)),
        "next_user_action": "Review the note, then say 确认 to promote it into Knowledge.",
    }


@mcp.tool()
def create_inbox_note_with_attachments(
    title: str,
    content: str,
    attachment_embeds: list[str] | None = None,
) -> dict[str, Any]:
    """
    Create a Markdown draft in Inbox and insert Obsidian attachment embeds.
    attachment_embeds should contain strings like ![[Assets/ChatGPT/YYYY-MM/file.png]].
    """
    embeds = attachment_embeds or []

    cleaned_embeds = []
    for embed in embeds:
        embed = embed.strip()
        if not embed:
            continue
        if not embed.startswith("![[") or not embed.endswith("]]"):
            raise ValueError("attachment_embeds must be Obsidian embeds like ![[Assets/.../file.png]]")
        cleaned_embeds.append(embed)

    if cleaned_embeds:
        attachment_block = "\n\n## 附件 / 截图\n\n" + "\n\n".join(cleaned_embeds) + "\n"
    else:
        attachment_block = ""

    full_content = content.rstrip() + attachment_block + "\n"
    return create_inbox_note(title=title, content=full_content)


@mcp.tool()
def list_inbox(limit: int = 20) -> list[dict[str, Any]]:
    """List recent Inbox drafts."""
    INBOX_DIR.mkdir(parents=True, exist_ok=True)
    limit = max(1, min(limit, 100))

    files = sorted(INBOX_DIR.glob("*.md"), key=lambda x: x.stat().st_mtime, reverse=True)

    result = []
    for p in files[:limit]:
        text = read_text_file(p)
        result.append(
            {
                "filename": p.name,
                "relative_path": str(p.relative_to(VAULT_ROOT)),
                "title": title_from_markdown(p, text),
                "size_bytes": p.stat().st_size,
                "modified_at": datetime.fromtimestamp(p.stat().st_mtime, LOCAL_TZ).isoformat(),
            }
        )

    return result


@mcp.tool()
def fetch_inbox_note(path_or_filename: str) -> dict[str, Any]:
    """Fetch an Inbox draft by filename or relative path."""
    path = normalize_inbox_path(path_or_filename)

    if not path.exists():
        raise FileNotFoundError("Inbox note not found")

    text = read_text_file(path)

    return {
        "ok": True,
        "relative_path": str(path.relative_to(VAULT_ROOT)),
        "title": title_from_markdown(path, text),
        "content": text,
    }


@mcp.tool()
def replace_inbox_note(path_or_filename: str, content: str) -> dict[str, Any]:
    """
    Replace the full content of an existing Inbox draft.
    This only works inside 00_Inbox/ChatGPT_To_Process and never creates a new file.
    """
    if not content.strip():
        raise ValueError("content is required")

    path = normalize_inbox_path(path_or_filename)

    if not path.exists():
        raise FileNotFoundError("Inbox note not found; refusing to create a new file")

    write_text_file(path, content)

    return {
        "ok": True,
        "action": "replaced_inbox_note",
        "relative_path": str(path.relative_to(VAULT_ROOT)),
        "filename": path.name,
        "size_bytes": path.stat().st_size,
        "modified_at": datetime.fromtimestamp(path.stat().st_mtime, LOCAL_TZ).isoformat(),
    }


@mcp.tool()
def append_inbox_note(path_or_filename: str, content: str, heading: str | None = None) -> dict[str, Any]:
    """
    Append content to an existing Inbox draft.
    This never creates a new file.
    """
    if not content.strip():
        raise ValueError("content is required")

    path = normalize_inbox_path(path_or_filename)

    if not path.exists():
        raise FileNotFoundError("Inbox note not found; refusing to create a new file")

    old_text = read_text_file(path)

    if heading and heading.strip():
        addition = f"\n\n## {heading.strip()}\n\n{content.strip()}\n"
    else:
        addition = f"\n\n{content.strip()}\n"

    write_text_file(path, old_text.rstrip() + addition)

    return {
        "ok": True,
        "action": "appended_inbox_note",
        "relative_path": str(path.relative_to(VAULT_ROOT)),
        "filename": path.name,
        "size_bytes": path.stat().st_size,
        "modified_at": datetime.fromtimestamp(path.stat().st_mtime, LOCAL_TZ).isoformat(),
    }


@mcp.tool()
def archive_inbox_note(path_or_filename: str, reason: str | None = None) -> dict[str, Any]:
    """
    Soft-archive an Inbox draft.
    The file is moved from 00_Inbox/ChatGPT_To_Process to Archive/Deleted/Inbox.
    No physical deletion is performed.
    """
    source = normalize_inbox_path(path_or_filename)

    if not source.exists():
        raise FileNotFoundError("Inbox note not found")

    relative = str(source.relative_to(VAULT_ROOT))
    old_text = read_text_file(source)

    archived_text = set_frontmatter(
        old_text,
        {
            "status": "archived",
            "archived_at": now_iso(),
            "archived_from": relative,
            "archived_reason": reason or "archive inbox draft",
        },
    )

    target_dir = inbox_archive_dir(source)
    target = unique_path(target_dir / safe_markdown_filename(archive_safe_timestamp()))

    write_text_file(target, archived_text)
    source.unlink()

    return {
        "ok": True,
        "action": "archived_inbox_note",
        "from": relative,
        "to": str(target.relative_to(VAULT_ROOT)),
        "status": "archived",
    }


@mcp.tool()
def promote_inbox_note(
    path_or_filename: str,
    knowledge_subdir: str = "Troubleshooting",
    final_title: str | None = None,
) -> dict[str, Any]:
    """
    Promote an Inbox draft into Knowledge after the user says 确认.
    This moves one Markdown file from 00_Inbox/ChatGPT_To_Process to Knowledge/<knowledge_subdir>.
    It never overwrites an existing Knowledge file.
    """
    source = normalize_inbox_path(path_or_filename)

    if not source.exists():
        raise FileNotFoundError("Inbox note not found")

    subdir = knowledge_subdir.strip().strip("/")
    if not subdir:
        subdir = "Troubleshooting"

    target_dir = ensure_inside(KNOWLEDGE_DIR / subdir, KNOWLEDGE_DIR)
    target_dir.mkdir(parents=True, exist_ok=True)

    text = read_text_file(source)
    text = set_frontmatter(
        text,
        {
            "status": "verified",
            "version": "1",
            "promoted_at": now_iso(),
            "location": f"Knowledge/{subdir}",
        },
    )

    if final_title and final_title.strip():
        filename = safe_markdown_filename(final_title.strip())
    else:
        filename = source.name

    target = unique_path(ensure_inside(target_dir / filename, target_dir))

    write_text_file(target, text)
    source.unlink()

    return {
        "ok": True,
        "action": "promoted_to_knowledge",
        "from": str(source.relative_to(VAULT_ROOT)),
        "to": str(target.relative_to(VAULT_ROOT)),
        "status": "verified",
    }


@mcp.tool()
def update_knowledge_note(relative_path: str, content: str, reason: str | None = None) -> dict[str, Any]:
    """
    Replace the current Knowledge note with new content.
    Before writing, the old version is copied to Archive/Versions.
    This is the default update path for conclusion-style knowledge.
    """
    if not content.strip():
        raise ValueError("content is required")

    path = normalize_knowledge_path(relative_path)

    if not path.exists():
        raise FileNotFoundError("Knowledge note not found")

    old_text = read_text_file(path)
    archived = archive_current_knowledge_version(path, archive_status="superseded", reason=reason)

    new_text = set_frontmatter(
        content,
        {
            "status": "verified",
            "version": str(next_version_number(old_text)),
            "updated_at": now_iso(),
            "previous_version": archived["relative_path"],
        },
    )

    write_text_file(path, new_text)

    return {
        "ok": True,
        "action": "updated_knowledge_note",
        "relative_path": str(path.relative_to(VAULT_ROOT)),
        "previous_version": archived["relative_path"],
        "size_bytes": path.stat().st_size,
    }


@mcp.tool()
def append_knowledge_note(
    relative_path: str,
    content: str,
    heading: str | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    """
    Append content to a current Knowledge note.
    Before appending, the old version is copied to Archive/Versions.
    Use this for log-like or cumulative notes.
    """
    if not content.strip():
        raise ValueError("content is required")

    path = normalize_knowledge_path(relative_path)

    if not path.exists():
        raise FileNotFoundError("Knowledge note not found")

    old_text = read_text_file(path)
    archived = archive_current_knowledge_version(path, archive_status="superseded", reason=reason)

    if heading and heading.strip():
        addition = f"\n\n## {heading.strip()}\n\n{content.strip()}\n"
    else:
        addition = f"\n\n{content.strip()}\n"

    new_text = old_text.rstrip() + addition
    new_text = set_frontmatter(
        new_text,
        {
            "status": "verified",
            "version": str(next_version_number(old_text)),
            "updated_at": now_iso(),
            "previous_version": archived["relative_path"],
        },
    )

    write_text_file(path, new_text)

    return {
        "ok": True,
        "action": "appended_knowledge_note",
        "relative_path": str(path.relative_to(VAULT_ROOT)),
        "previous_version": archived["relative_path"],
        "size_bytes": path.stat().st_size,
    }


@mcp.tool()
def archive_knowledge_note(relative_path: str, reason: str | None = None) -> dict[str, Any]:
    """
    Soft-delete / archive a Knowledge note.
    The file is moved to Archive/Deleted and removed from default Knowledge search.
    No physical deletion is performed.
    """
    path = normalize_knowledge_path(relative_path)

    if not path.exists():
        raise FileNotFoundError("Knowledge note not found")

    relative = str(path.relative_to(VAULT_ROOT))
    old_text = read_text_file(path)

    archived_text = set_frontmatter(
        old_text,
        {
            "status": "deleted",
            "archived_at": now_iso(),
            "archived_from": relative,
            "archived_reason": reason or "soft delete",
        },
    )

    target_dir = note_archive_dir(DELETED_DIR, relative)
    target = unique_path(target_dir / safe_markdown_filename(archive_safe_timestamp()))

    write_text_file(target, archived_text)
    path.unlink()

    return {
        "ok": True,
        "action": "archived_knowledge_note",
        "from": relative,
        "to": str(target.relative_to(VAULT_ROOT)),
        "status": "deleted",
    }


@mcp.tool()
def search_notes(query: str, scope: str = "Knowledge", limit: int = 10) -> list[dict[str, Any]]:
    """
    Search Markdown notes for future reference.
    Default scope is Knowledge so verified notes rank first.
    Valid scopes: Knowledge, Sources, Templates, Inbox, All, Archive.
    """
    q = query.strip()
    if not q:
        raise ValueError("query is required")

    limit = max(1, min(limit, 50))
    terms = [t for t in re.split(r"\s+", q.lower()) if t]

    scope_map = {
        "Knowledge": [KNOWLEDGE_DIR],
        "Sources": [SOURCES_DIR],
        "Templates": [TEMPLATES_DIR],
        "Inbox": [INBOX_DIR],
        "All": [KNOWLEDGE_DIR, SOURCES_DIR, TEMPLATES_DIR, INBOX_DIR],
        "Archive": [VERSIONS_DIR, DELETED_DIR],
    }

    roots = scope_map.get(scope, [KNOWLEDGE_DIR])
    hits = []

    for root in roots:
        for path in all_markdown_files(root):
            try:
                text = read_text_file(path)
            except Exception:
                continue

            haystack = (str(path.relative_to(VAULT_ROOT)) + "\n" + text).lower()
            score = 0
            for term in terms:
                score += haystack.count(term)

            if score <= 0:
                continue

            status = frontmatter_value(text, "status")
            hits.append(
                {
                    "score": score,
                    "relative_path": str(path.relative_to(VAULT_ROOT)),
                    "title": title_from_markdown(path, text),
                    "status": status,
                    "snippet": snippet(text, terms),
                    "modified_at": datetime.fromtimestamp(path.stat().st_mtime, LOCAL_TZ).isoformat(),
                }
            )

    hits.sort(key=lambda x: (x["score"], x["modified_at"]), reverse=True)
    return hits[:limit]


@mcp.tool()
def fetch_note(relative_path: str) -> dict[str, Any]:
    """
    Fetch a Markdown note from allowed read areas:
    Knowledge, Sources, Templates, or Inbox.
    """
    path = normalize_read_path(relative_path)

    if not path.exists():
        raise FileNotFoundError("Note not found")

    text = read_text_file(path)

    return {
        "ok": True,
        "relative_path": str(path.relative_to(VAULT_ROOT)),
        "title": title_from_markdown(path, text),
        "content": text,
    }


@mcp.tool()
def list_versions(relative_path: str, include_deleted: bool = True) -> list[dict[str, Any]]:
    """
    List archived versions for a Knowledge note.
    """
    knowledge_path = normalize_knowledge_path(relative_path)
    knowledge_relative = str(knowledge_path.relative_to(VAULT_ROOT))

    roots = [note_archive_dir(VERSIONS_DIR, knowledge_relative)]

    if include_deleted:
        roots.append(note_archive_dir(DELETED_DIR, knowledge_relative))

    versions = []

    for root in roots:
        if not root.exists():
            continue

        for p in sorted(root.glob("*.md"), key=lambda x: x.stat().st_mtime, reverse=True):
            text = read_text_file(p)
            versions.append(
                {
                    "relative_path": str(p.relative_to(VAULT_ROOT)),
                    "title": title_from_markdown(p, text),
                    "status": frontmatter_value(text, "status"),
                    "archived_at": frontmatter_value(text, "archived_at"),
                    "archived_reason": frontmatter_value(text, "archived_reason"),
                    "size_bytes": p.stat().st_size,
                    "modified_at": datetime.fromtimestamp(p.stat().st_mtime, LOCAL_TZ).isoformat(),
                }
            )

    return versions


@mcp.tool()
def fetch_version(archive_relative_path: str) -> dict[str, Any]:
    """
    Fetch an archived version from Archive/Versions or Archive/Deleted.
    """
    path = normalize_archive_path(archive_relative_path)

    if not path.exists():
        raise FileNotFoundError("Archived version not found")

    text = read_text_file(path)

    return {
        "ok": True,
        "relative_path": str(path.relative_to(VAULT_ROOT)),
        "title": title_from_markdown(path, text),
        "content": text,
    }


@mcp.tool()
def rollback_knowledge_note(
    relative_path: str,
    archive_relative_path: str,
    reason: str | None = None,
) -> dict[str, Any]:
    """
    Restore a Knowledge note from an archived version.
    Current Knowledge content is first archived to Archive/Versions.
    """
    target = normalize_knowledge_path(relative_path)
    version_path = normalize_archive_path(archive_relative_path)

    if not version_path.exists():
        raise FileNotFoundError("Archived version not found")

    if target.exists():
        current_archive = archive_current_knowledge_version(
            target,
            archive_status="superseded",
            reason=reason or "rollback current version",
        )
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        current_archive = None

    version_text = read_text_file(version_path)

    restored_text = set_frontmatter(
        version_text,
        {
            "status": "verified",
            "restored_at": now_iso(),
            "restored_from": str(version_path.relative_to(VAULT_ROOT)),
        },
    )

    write_text_file(target, restored_text)

    return {
        "ok": True,
        "action": "rolled_back_knowledge_note",
        "relative_path": str(target.relative_to(VAULT_ROOT)),
        "restored_from": str(version_path.relative_to(VAULT_ROOT)),
        "previous_current_archived_to": current_archive["relative_path"] if current_archive else None,
    }


@mcp.tool()
def capture_source_note(
    title: str,
    source_type: str,
    content: str,
    url: str | None = None,
    attachment_embeds: list[str] | None = None,
) -> dict[str, Any]:
    """
    Capture external information into 00_Inbox/Capture.
    Use for webpages, WeChat posts, video notes, copied text, screenshots, and other external sources.
    """
    if not title.strip():
        raise ValueError("title is required")
    if not content.strip():
        raise ValueError("content is required")

    CAPTURE_DIR.mkdir(parents=True, exist_ok=True)

    safe_source_type = re.sub(r"[^a-zA-Z0-9_-]+", "-", source_type.strip() or "source")
    filename = safe_markdown_filename(f"{now_local()} {safe_source_type} {title}")
    target = unique_path(ensure_inside(CAPTURE_DIR / filename, CAPTURE_DIR))

    frontmatter = [
        "---",
        "type: source",
        "status: unprocessed",
        f"source_type: {safe_source_type}",
        f"captured_at: {now_iso()}",
    ]

    if url:
        frontmatter.append(f"url: {url}")

    frontmatter.append("---")

    note = "\n".join(frontmatter) + f"\n\n# {title}\n\n{content.strip()}\n"

    embeds = attachment_embeds or []
    if embeds:
        note += "\n## 附件 / 截图\n\n"
        note += "\n\n".join(embeds)
        note += "\n"

    write_text_file(target, note)

    return {
        "ok": True,
        "action": "captured_source",
        "relative_path": str(target.relative_to(VAULT_ROOT)),
    }


async def http_health(request):
    try:
        for d in [INBOX_DIR, CAPTURE_DIR, KNOWLEDGE_DIR, ASSETS_DIR, ARCHIVE_DIR, VERSIONS_DIR, DELETED_DIR]:
            d.mkdir(parents=True, exist_ok=True)

        ok = True
        error = None
    except Exception as exc:
        ok = False
        error = str(exc)

    return JSONResponse(
        {
            "ok": ok,
            "version": APP_VERSION,
            "service": "obsidian-kb-business",
            "timezone": "Asia/Shanghai",
            "mcp_endpoint": "/mcp",
            "public_host": PUBLIC_HOST,
            "vault_root": str(VAULT_ROOT),
            "inbox": str(INBOX_DIR),
            "knowledge": str(KNOWLEDGE_DIR),
            "assets": str(ASSETS_DIR),
            "archive": str(ARCHIVE_DIR),
            "versions": str(VERSIONS_DIR),
            "deleted": str(DELETED_DIR),
            "error": error,
        }
    )


@contextlib.asynccontextmanager
async def lifespan(app):
    async with mcp.session_manager.run():
        yield


app = Starlette(
    routes=[
        Route("/health", http_health, methods=["GET"]),
        Mount("/", app=mcp.streamable_http_app()),
    ],
    middleware=[
        Middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_methods=["*"],
            allow_headers=["*"],
            expose_headers=["*"],
        ),
    ],
    lifespan=lifespan,
)
