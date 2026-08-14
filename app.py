import base64
import json
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
from kb_seed import (
    CONTEXT_PACKS_REL,
    CONTEXT_PACK_SPECS,
    INDEXES_REL,
    INDEX_TITLES,
    SCHEMAS_REL,
    SCHEMA_ALIASES,
    SCHEMA_SPECS,
    bootstrap_llm_knowledge_base_files,
    context_pack_relative_path,
    generated_block_end,
    generated_block_start,
    index_relative_path,
    note_link,
    render_context_pack_markdown,
    render_decision_log_block,
    render_index_markdown,
    render_knowledge_map_block,
    render_schema_markdown,
    render_stale_notes_block,
    render_workflows_block,
    replace_generated_block,
    schema_relative_path,
)
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
SCHEMAS_DIR = (VAULT_ROOT / SCHEMAS_REL).resolve()
INDEXES_DIR = (VAULT_ROOT / INDEXES_REL).resolve()
CONTEXT_PACKS_DIR = (VAULT_ROOT / CONTEXT_PACKS_REL).resolve()
ASSETS_DIR = (VAULT_ROOT / ASSETS_REL).resolve()
ARCHIVE_DIR = (VAULT_ROOT / ARCHIVE_REL).resolve()
VERSIONS_DIR = (VAULT_ROOT / VERSIONS_REL).resolve()
DELETED_DIR = (VAULT_ROOT / DELETED_REL).resolve()

MAX_CONTENT_BYTES = int(os.environ.get("MAX_CONTENT_BYTES", "300000"))
MAX_ATTACHMENT_BYTES = int(os.environ.get("MAX_ATTACHMENT_BYTES", "15000000"))

APP_VERSION = "2026-08-14-autonomous-knowledge-ingest"

RELATION_TYPES = {
    "explains",
    "implements",
    "depends_on",
    "derived_from",
    "contradicts",
    "supersedes",
    "related_to",
}
RELATION_CONFIDENCES = {"high", "medium", "low", "needs_review"}
AI_RELATIONS_HEADING = "## AI 关联（自动维护）"
AI_RELATIONS_START = "<!-- AI_RELATIONS_START -->"
AI_RELATIONS_END = "<!-- AI_RELATIONS_END -->"
MAX_RELATIONS_PER_APPLY = 20
AUTONOMOUS_INGEST_LIFECYCLES = {"incubating", "knowledge", "review_needed"}


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
        SCHEMAS_DIR,
        INDEXES_DIR,
        CONTEXT_PACKS_DIR,
        ASSETS_DIR,
    ]

    if not any(candidate == root or root in candidate.parents for root in allowed_roots):
        raise ValueError("Path is not in an allowed read area")

    if ".obsidian" in candidate.parts:
        raise ValueError("Access to .obsidian is forbidden")

    if candidate.suffix != ".md":
        raise ValueError("Only Markdown files are readable through fetch_note")

    return candidate


def normalize_schema_name(schema_name: str) -> str:
    clean = schema_name.strip().lower()
    if clean not in SCHEMA_SPECS:
        raise ValueError(f"Unsupported schema: {schema_name}")
    return clean


def normalize_context_pack_name(name: str) -> str:
    clean = name.strip()
    if clean not in CONTEXT_PACK_SPECS:
        raise ValueError(f"Unsupported context pack: {name}")
    return clean


def frontmatter_map(text: str) -> dict[str, str]:
    if not text.startswith("---\n"):
        return {}

    end = text.find("\n---", 4)
    if end == -1:
        return {}

    data: dict[str, str] = {}
    for line in text[4:end].splitlines():
        if ":" not in line or line.startswith(" "):
            continue
        key, value = line.split(":", 1)
        data[key.strip()] = value.strip()
    return data


def markdown_sections(text: str) -> set[str]:
    sections = set()
    for line in text.splitlines():
        if line.startswith("## "):
            sections.add(line[3:].strip())
    return sections


def note_record_from_path(path: Path, stale_days: int = 180) -> dict[str, Any]:
    text = read_text_file(path)
    meta = frontmatter_map(text)
    modified_at = datetime.fromtimestamp(path.stat().st_mtime, LOCAL_TZ)
    age_days = max(0, (datetime.now(LOCAL_TZ) - modified_at).days)
    return {
        "relative_path": str(path.relative_to(VAULT_ROOT)),
        "title": title_from_markdown(path, text),
        "type": meta.get("type"),
        "status": meta.get("status"),
        "tags": meta.get("tags"),
        "modified_at": modified_at.isoformat(),
        "age_days": age_days,
        "is_stale": meta.get("status") == "verified" and age_days >= stale_days,
        "content": text,
    }


def collect_knowledge_note_records(stale_days: int = 180) -> list[dict[str, Any]]:
    records = []
    for path in all_markdown_files(KNOWLEDGE_DIR):
        try:
            records.append(note_record_from_path(path, stale_days=stale_days))
        except Exception:
            continue
    return records


def schema_name_for_note(text: str, schema_name: str | None = None) -> str:
    if schema_name:
        return normalize_schema_name(schema_name)

    inferred = frontmatter_value(text, "type")
    if not inferred:
        raise ValueError("schema_name is required when note type is missing")

    alias = SCHEMA_ALIASES.get(inferred.strip().lower())
    if not alias:
        raise ValueError(f"Unsupported note type for schema validation: {inferred}")
    return alias


def validate_note_text(text: str, schema_name: str) -> dict[str, Any]:
    schema_key = normalize_schema_name(schema_name)
    spec = SCHEMA_SPECS[schema_key]
    meta = frontmatter_map(text)
    sections = markdown_sections(text)

    missing_required_frontmatter = [
        field for field in spec["required_frontmatter"]
        if not meta.get(field)
    ]
    missing_recommended_frontmatter = [
        field for field in spec["recommended_frontmatter"]
        if not meta.get(field)
    ]
    missing_required_sections = [
        section for section in spec["required_sections"]
        if section not in sections
    ]
    missing_recommended_sections = [
        section for section in spec["recommended_sections"]
        if section not in sections
    ]

    return {
        "schema_name": schema_key,
        "missing_required_frontmatter": missing_required_frontmatter,
        "missing_recommended_frontmatter": missing_recommended_frontmatter,
        "missing_required_sections": missing_required_sections,
        "missing_recommended_sections": missing_recommended_sections,
        "is_valid": not (
            missing_required_frontmatter or missing_required_sections
        ),
    }


def embedded_asset_paths(text: str) -> list[str]:
    return re.findall(r"!\[\[([^\]]+)\]\]", text)


def wiki_link_targets(text: str) -> list[str]:
    """Return Obsidian wiki-link targets, excluding embeds and aliases."""
    targets = []
    for raw in re.findall(r"(?<!!)\[\[([^\]]+)\]\]", text):
        target = raw.split("|", 1)[0].split("#", 1)[0].split("^", 1)[0].strip()
        if target:
            targets.append(target)
    return targets


def resolve_wiki_link_target(target: str) -> Path | None:
    """Resolve a vault-relative wiki link without allowing path traversal."""
    clean = target.strip().lstrip("/")
    if not clean or clean.startswith("."):
        return None

    candidates = [clean]
    if not clean.endswith(".md"):
        candidates.append(f"{clean}.md")

    for candidate_raw in candidates:
        candidate = (VAULT_ROOT / candidate_raw).resolve()
        try:
            ensure_inside(candidate, VAULT_ROOT)
        except ValueError:
            return None
        if candidate.exists() and candidate.suffix == ".md":
            return candidate

    # Obsidian also permits title-only links. Resolve only an unambiguous title.
    if "/" not in clean:
        matches = []
        for path in all_markdown_files(KNOWLEDGE_DIR):
            try:
                if path.stem == clean or title_from_markdown(path, read_text_file(path)) == clean:
                    matches.append(path)
            except Exception:
                continue
        if len(matches) == 1:
            return matches[0]
    return None


def relation_metadata(text: str) -> list[dict[str, str]]:
    """Read JSON-in-YAML relation metadata, tolerating older or malformed notes."""
    raw = frontmatter_value(text, "relations")
    if not raw:
        return []
    try:
        import json
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return []
    if not isinstance(parsed, list):
        return []

    result = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        target = item.get("target")
        relation_type = item.get("type")
        if isinstance(target, str) and isinstance(relation_type, str):
            result.append({
                "target": target,
                "type": relation_type,
                "confidence": str(item.get("confidence") or "needs_review"),
                "rationale": str(item.get("rationale") or ""),
            })
    return result


def normalize_relation(relative_path: str, item: dict[str, Any]) -> dict[str, str]:
    if not isinstance(item, dict):
        raise ValueError("Each relation must be an object")
    target = str(item.get("target") or "").strip().lstrip("/")
    relation_type = str(item.get("type") or "").strip()
    confidence = str(item.get("confidence") or "needs_review").strip()
    rationale = str(item.get("rationale") or "").strip()

    if relation_type not in RELATION_TYPES:
        raise ValueError(f"Unsupported relation type: {relation_type}")
    if confidence not in RELATION_CONFIDENCES:
        raise ValueError(f"Unsupported relation confidence: {confidence}")
    if not rationale:
        raise ValueError("relation rationale is required")
    if len(rationale) > 500:
        raise ValueError("relation rationale is too long; max 500 characters")

    target_path = normalize_knowledge_path(target)
    if not target_path.exists():
        raise FileNotFoundError(f"Relation target not found: {target}")
    normalized_target = str(target_path.relative_to(VAULT_ROOT))
    if normalized_target == relative_path:
        raise ValueError("A note cannot relate to itself")

    return {
        "target": normalized_target,
        "type": relation_type,
        "confidence": confidence,
        "rationale": rationale,
    }


def render_ai_relations_block(relations: list[dict[str, str]]) -> str:
    lines = [AI_RELATIONS_HEADING, "", AI_RELATIONS_START, ""]
    if relations:
        for item in relations:
            target = item["target"]
            target_path = (VAULT_ROOT / target).resolve()
            target_title = target_path.stem
            if target_path.exists():
                target_title = title_from_markdown(target_path, read_text_file(target_path))
            lines.append(
                f"- [[{target}|{target_title}]] — `{item['type']}` / "
                f"`{item['confidence']}`：{item['rationale']}"
            )
    else:
        lines.append("- 暂无 AI 维护的关联")
    lines += ["", AI_RELATIONS_END]
    return "\n".join(lines)


def replace_ai_relations_block(text: str, relations: list[dict[str, str]]) -> str:
    block = render_ai_relations_block(relations)
    pattern = re.escape(AI_RELATIONS_HEADING) + r".*?" + re.escape(AI_RELATIONS_END)
    if re.search(pattern, text, flags=re.DOTALL):
        return re.sub(pattern, block, text, count=1, flags=re.DOTALL).rstrip() + "\n"
    return text.rstrip() + "\n\n" + block + "\n"


def relation_key(item: dict[str, str]) -> tuple[str, str]:
    return item["target"], item["type"]


def merge_relations(existing: list[dict[str, str]], incoming: list[dict[str, str]]) -> list[dict[str, str]]:
    merged = {relation_key(item): item for item in existing}
    for item in incoming:
        merged[relation_key(item)] = item
    return sorted(merged.values(), key=lambda item: (item["target"], item["type"]))


def relationship_candidates(path: Path, limit: int) -> list[dict[str, Any]]:
    """Deterministic candidate recall. A calling AI makes the semantic decision."""
    text = read_text_file(path)
    title = title_from_markdown(path, text)
    source_terms = set(re.findall(r"[\w\-]{2,}", title.lower()))
    source_terms.update(re.findall(r"[\w\-]{2,}", (frontmatter_value(text, "tags") or "").lower()))
    source_terms.discard("knowledge")
    existing_targets = {item["target"] for item in relation_metadata(text)}
    results = []
    for record in collect_knowledge_note_records():
        if record["relative_path"] == str(path.relative_to(VAULT_ROOT)):
            continue
        if record["relative_path"] in existing_targets:
            continue
        candidate_terms = set(re.findall(r"[\w\-]{2,}", (record["title"] + " " + (record.get("tags") or "")).lower()))
        shared = sorted(source_terms & candidate_terms)
        if not shared:
            continue
        results.append({
            "target": record["relative_path"],
            "title": record["title"],
            "score": len(shared),
            "matched_terms": shared,
            "snippet": snippet(record["content"], shared),
            "suggested_relation": {
                "type": "related_to",
                "confidence": "needs_review",
                "rationale": f"候选召回命中：{', '.join(shared)}。需要 AI 或人工复核关系类型与理由。",
            },
        })
    results.sort(key=lambda item: (item["score"], item["title"]), reverse=True)
    return results[:limit]


def broken_note_links(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    broken = []
    for record in records:
        for target in sorted(set(wiki_link_targets(record["content"]))):
            if resolve_wiki_link_target(target) is None:
                broken.append({
                    "relative_path": record["relative_path"],
                    "title": record["title"],
                    "missing_note": target,
                })
    return broken


def ensure_support_docs_exist(note_records: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return bootstrap_llm_knowledge_base_files(
        vault_root=VAULT_ROOT,
        write_text_file=write_text_file,
        overwrite=False,
        note_records=note_records or [],
    )


def generated_index_content(name: str, note_records: list[dict[str, Any]], stale_days: int = 180) -> str:
    if name == "knowledge-map":
        return render_knowledge_map_block(note_records)
    if name == "workflows":
        return render_workflows_block(note_records)
    if name == "decision-log":
        return render_decision_log_block(note_records)
    if name == "stale-notes":
        return render_stale_notes_block(note_records, stale_days)
    raise ValueError(f"Unsupported generated index: {name}")


def update_generated_index(name: str, note_records: list[dict[str, Any]], stale_days: int = 180) -> str:
    relative_path = index_relative_path(name)
    path = (VAULT_ROOT / relative_path).resolve()
    default_text = render_index_markdown(name, note_records=note_records, stale_days=stale_days)
    if not path.exists():
        write_text_file(path, default_text)
        return relative_path

    text = read_text_file(path)
    new_text = replace_generated_block(
        text,
        name,
        generated_index_content(name, note_records, stale_days),
    )
    if new_text != text:
        write_text_file(path, new_text)
    return relative_path


def duplicate_note_titles(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        key = record["title"].strip().lower()
        grouped.setdefault(key, []).append(record)

    return [
        {
            "title": items[0]["title"],
            "count": len(items),
            "relative_paths": [item["relative_path"] for item in items],
        }
        for items in grouped.values()
        if len(items) > 1
    ]


def stale_note_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "relative_path": record["relative_path"],
            "title": record["title"],
            "age_days": record["age_days"],
        }
        for record in records
        if record["is_stale"]
    ]


def broken_asset_links(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    broken = []
    for record in records:
        for relative_asset in embedded_asset_paths(record["content"]):
            asset_path = (VAULT_ROOT / relative_asset).resolve()
            if not asset_path.exists():
                broken.append(
                    {
                        "relative_path": record["relative_path"],
                        "title": record["title"],
                        "missing_asset": relative_asset,
                    }
                )
    return broken


def schema_violations(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    violations = []
    for record in records:
        inferred_type = (record.get("type") or "").strip().lower()
        alias = SCHEMA_ALIASES.get(inferred_type)
        if not alias:
            violations.append(
                {
                    "relative_path": record["relative_path"],
                    "title": record["title"],
                    "reason": f"Unsupported or missing schema type: {record.get('type')}",
                }
            )
            continue

        result = validate_note_text(record["content"], alias)
        if result["is_valid"]:
            continue

        violations.append(
            {
                "relative_path": record["relative_path"],
                "title": record["title"],
                "schema_name": alias,
                "missing_required_frontmatter": result["missing_required_frontmatter"],
                "missing_required_sections": result["missing_required_sections"],
            }
        )
    return violations


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
        SCHEMAS_DIR, INDEXES_DIR, CONTEXT_PACKS_DIR,
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
            "checkpoint_conversation",
            "ingest_knowledge",
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
            "bootstrap_llm_knowledge_base",
            "list_schemas",
            "fetch_schema",
            "list_context_packs",
            "fetch_context_pack",
            "validate_note_against_schema",
            "generate_indexes",
            "update_knowledge_map",
            "update_decision_log",
            "audit_knowledge_base",
            "find_duplicate_notes",
            "find_stale_notes",
            "find_broken_asset_links",
            "list_note_relations",
            "suggest_note_relations",
            "apply_note_relations",
            "remove_note_relation",
            "find_broken_note_links",
            "find_schema_violations",
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
            SCHEMAS_REL,
            INDEXES_REL,
            CONTEXT_PACKS_REL,
        ],
        "read_allowed": [
            INBOX_REL,
            KNOWLEDGE_REL,
            SOURCES_REL,
            TEMPLATES_REL,
            SCHEMAS_REL,
            INDEXES_REL,
            CONTEXT_PACKS_REL,
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
    Valid scopes: Knowledge, Sources, Templates, Inbox, Schemas, Indexes,
    Context_Packs, All, Archive.
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
        "Schemas": [SCHEMAS_DIR],
        "Indexes": [INDEXES_DIR],
        "Context_Packs": [CONTEXT_PACKS_DIR],
        "All": [KNOWLEDGE_DIR, SOURCES_DIR, TEMPLATES_DIR, INBOX_DIR, SCHEMAS_DIR, INDEXES_DIR, CONTEXT_PACKS_DIR],
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
def list_note_relations(relative_path: str) -> dict[str, Any]:
    """List explicit AI-managed outgoing relations and incoming Obsidian links."""
    path = normalize_knowledge_path(relative_path)
    if not path.exists():
        raise FileNotFoundError("Knowledge note not found")

    relative = str(path.relative_to(VAULT_ROOT))
    text = read_text_file(path)
    outgoing = relation_metadata(text)
    incoming = []
    for record in collect_knowledge_note_records():
        if record["relative_path"] == relative:
            continue
        explicit = [item for item in relation_metadata(record["content"]) if item["target"] == relative]
        wiki_targets = wiki_link_targets(record["content"])
        if explicit or any(resolve_wiki_link_target(target) == path for target in wiki_targets):
            incoming.append({
                "relative_path": record["relative_path"],
                "title": record["title"],
                "explicit_relations": explicit,
            })

    return {
        "ok": True,
        "relative_path": relative,
        "outgoing_relations": outgoing,
        "incoming_links": sorted(incoming, key=lambda item: item["relative_path"]),
        "wiki_link_targets": sorted(set(wiki_link_targets(text))),
    }


@mcp.tool()
def suggest_note_relations(relative_path: str, limit: int = 10) -> dict[str, Any]:
    """
    Return deterministic candidate notes for a caller/AI to review. This tool never
    writes relations and does not claim that a candidate is semantically correct.
    """
    path = normalize_knowledge_path(relative_path)
    if not path.exists():
        raise FileNotFoundError("Knowledge note not found")
    limit = max(1, min(limit, 20))
    return {
        "ok": True,
        "relative_path": str(path.relative_to(VAULT_ROOT)),
        "candidates": relationship_candidates(path, limit),
        "next_action": "Review candidates, then call apply_note_relations with explicit type, confidence, and rationale.",
    }


@mcp.tool()
def apply_note_relations(
    relative_path: str,
    relations: list[dict[str, Any]],
    apply: bool = False,
    reason: str | None = None,
) -> dict[str, Any]:
    """
    Add or update explicit note relations. Defaults to dry-run; set apply=true only
    after reviewing the returned change. Existing manual prose is never modified.
    """
    path = normalize_knowledge_path(relative_path)
    if not path.exists():
        raise FileNotFoundError("Knowledge note not found")
    if len(relations) > MAX_RELATIONS_PER_APPLY:
        raise ValueError(f"Too many relations; max {MAX_RELATIONS_PER_APPLY} per apply")

    relative = str(path.relative_to(VAULT_ROOT))
    incoming = [normalize_relation(relative, item) for item in relations]
    if len({relation_key(item) for item in incoming}) != len(incoming):
        raise ValueError("Duplicate target/type pairs are not allowed")

    old_text = read_text_file(path)
    merged = merge_relations(relation_metadata(old_text), incoming)
    import json
    proposed = set_frontmatter(
        replace_ai_relations_block(old_text, merged),
        {
            "relations": json.dumps(merged, ensure_ascii=False, separators=(",", ":")),
            "relation_checked_at": now_iso(),
        },
    )

    result = {
        "ok": True,
        "action": "applied_note_relations" if apply else "preview_note_relations",
        "relative_path": relative,
        "relations": merged,
        "added_or_updated": incoming,
        "changed": proposed != old_text,
        "dry_run": not apply,
    }
    if not apply:
        result["preview"] = proposed
        return result

    if proposed != old_text:
        archived = archive_current_knowledge_version(
            path,
            archive_status="superseded",
            reason=reason or "update note relations",
        )
        new_text = set_frontmatter(
            proposed,
            {
                "status": "verified",
                "version": str(next_version_number(old_text)),
                "updated_at": now_iso(),
                "previous_version": archived["relative_path"],
            },
        )
        write_text_file(path, new_text)
        result["previous_version"] = archived["relative_path"]
    return result


@mcp.tool()
def remove_note_relation(
    relative_path: str,
    target: str,
    relation_type: str | None = None,
    apply: bool = False,
    reason: str | None = None,
) -> dict[str, Any]:
    """Remove one explicit relation, or all relation types for one target; dry-run by default."""
    path = normalize_knowledge_path(relative_path)
    if not path.exists():
        raise FileNotFoundError("Knowledge note not found")
    target_path = normalize_knowledge_path(target)
    normalized_target = str(target_path.relative_to(VAULT_ROOT))
    if relation_type is not None and relation_type not in RELATION_TYPES:
        raise ValueError(f"Unsupported relation type: {relation_type}")

    old_text = read_text_file(path)
    old_relations = relation_metadata(old_text)
    remaining = [
        item for item in old_relations
        if not (item["target"] == normalized_target and (relation_type is None or item["type"] == relation_type))
    ]
    if len(remaining) == len(old_relations):
        raise ValueError("Matching relation not found")

    import json
    proposed = set_frontmatter(
        replace_ai_relations_block(old_text, remaining),
        {
            "relations": json.dumps(remaining, ensure_ascii=False, separators=(",", ":")),
            "relation_checked_at": now_iso(),
        },
    )
    result = {
        "ok": True,
        "action": "removed_note_relation" if apply else "preview_remove_note_relation",
        "relative_path": str(path.relative_to(VAULT_ROOT)),
        "removed_target": normalized_target,
        "removed_relation_type": relation_type,
        "relations": remaining,
        "dry_run": not apply,
    }
    if not apply:
        result["preview"] = proposed
        return result

    archived = archive_current_knowledge_version(
        path,
        archive_status="superseded",
        reason=reason or "remove note relation",
    )
    new_text = set_frontmatter(
        proposed,
        {
            "status": "verified",
            "version": str(next_version_number(old_text)),
            "updated_at": now_iso(),
            "previous_version": archived["relative_path"],
        },
    )
    write_text_file(path, new_text)
    result["previous_version"] = archived["relative_path"]
    return result


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


def ingest_frontmatter(
    content: str,
    *,
    topic: str,
    lifecycle: str,
    checkpoint_path: str,
    schema_name: str | None = None,
    existing_content: str | None = None,
) -> str:
    """Stamp the minimum provenance needed by an autonomous ingest."""
    checkpoints: list[str] = []
    if existing_content:
        try:
            previous = json.loads(frontmatter_value(existing_content, "source_checkpoints") or "[]")
            if isinstance(previous, list):
                checkpoints = [str(item) for item in previous if isinstance(item, str) and item]
        except (TypeError, ValueError):
            pass
    if checkpoint_path not in checkpoints:
        checkpoints.append(checkpoint_path)
    updates = {
        "topic": topic,
        "knowledge_lifecycle": lifecycle,
        "source_checkpoints": json.dumps(checkpoints, ensure_ascii=False),
    }
    if schema_name:
        updates["type"] = schema_name
    return set_frontmatter(content, updates)


def default_knowledge_subdir(schema_name: str) -> str:
    """Return a Knowledge-relative destination for one of the canonical schemas."""
    default_directory = str(SCHEMA_SPECS[normalize_schema_name(schema_name)]["default_directory"])
    if not default_directory.startswith(f"{KNOWLEDGE_REL}/"):
        raise ValueError(f"Schema default directory must be under {KNOWLEDGE_REL}: {default_directory}")
    return default_directory[len(KNOWLEDGE_REL) + 1:]


@mcp.tool()
def checkpoint_conversation(
    topic: str,
    content: str,
    session_id: str | None = None,
    source_url: str | None = None,
) -> dict[str, Any]:
    """
    Persist an append-only conversation checkpoint before context compaction or a
    topic shift. Agents should call this automatically during long, meaningful
    conversations; it never creates or changes a formal Knowledge note.
    """
    clean_topic = topic.strip()
    if not clean_topic:
        raise ValueError("topic is required")
    if not content.strip():
        raise ValueError("content is required")

    checkpoint = capture_source_note(
        title=f"{clean_topic} — conversation checkpoint",
        source_type="conversation-checkpoint",
        content=content,
        url=source_url,
    )
    path = normalize_read_path(checkpoint["relative_path"])
    text = read_text_file(path)
    text = set_frontmatter(
        text,
        {
            "status": "captured",
            "topic": clean_topic,
            "session_id": session_id or "",
            "checkpoint_at": now_iso(),
            "append_only": "true",
        },
    )
    write_text_file(path, text)
    return {
        **checkpoint,
        "action": "checkpointed_conversation",
        "topic": clean_topic,
        "session_id": session_id,
        "lifecycle": "source",
    }


@mcp.tool()
def ingest_knowledge(
    topic: str,
    session_content: str,
    distilled_content: str,
    lifecycle: str = "incubating",
    schema_name: str | None = None,
    topic_note_path: str | None = None,
    knowledge_subdir: str | None = None,
    relations: list[dict[str, Any]] | None = None,
    session_id: str | None = None,
    source_url: str | None = None,
) -> dict[str, Any]:
    """
    Complete the controlled storage half of an autonomous LLM-Wiki ingest.

    The calling Agent supplies the semantic synthesis, lifecycle decision, and
    any explicit relations after it has searched relevant Inbox and Knowledge
    notes. This tool persists a source checkpoint, updates the selected ongoing
    topic note or creates one, promotes mature content automatically, applies
    validated relations, and preserves Knowledge versions on every update.

    Use when a user says “总结一下，入库” or an equivalent request. Do not ask
    the user to select a folder or note when the Agent can infer it reliably.
    """
    clean_topic = topic.strip()
    if not clean_topic:
        raise ValueError("topic is required")
    if not session_content.strip():
        raise ValueError("session_content is required")
    if not distilled_content.strip():
        raise ValueError("distilled_content is required")

    lifecycle_key = lifecycle.strip().lower()
    if lifecycle_key not in AUTONOMOUS_INGEST_LIFECYCLES:
        raise ValueError(f"Unsupported lifecycle: {lifecycle}")

    if lifecycle_key == "knowledge" and not schema_name:
        raise ValueError("schema_name is required when lifecycle is knowledge")
    normalized_schema = normalize_schema_name(schema_name) if schema_name else None

    existing_content = None
    if topic_note_path:
        existing_path = (
            normalize_knowledge_path(topic_note_path)
            if lifecycle_key == "knowledge"
            else normalize_inbox_path(topic_note_path)
        )
        if not existing_path.exists():
            raise FileNotFoundError("topic_note_path not found in the selected lifecycle area")
        existing_content = read_text_file(existing_path)

    checkpoint = checkpoint_conversation(
        topic=clean_topic,
        content=session_content,
        session_id=session_id,
        source_url=source_url,
    )
    checkpoint_path = checkpoint["relative_path"]
    content = ingest_frontmatter(
        distilled_content,
        topic=clean_topic,
        lifecycle=lifecycle_key,
        checkpoint_path=checkpoint_path,
        schema_name=normalized_schema,
        existing_content=existing_content,
    )

    relation_items = relations or []
    if lifecycle_key in {"incubating", "review_needed"}:
        status = "review-needed" if lifecycle_key == "review_needed" else "incubating"
        content = set_frontmatter(content, {"status": status, "updated_at": now_iso()})

        if topic_note_path:
            target = normalize_inbox_path(topic_note_path)
            if not target.exists():
                raise FileNotFoundError("topic_note_path Inbox note not found")
            old_text = read_text_file(target)
            write_text_file(target, content)
            result = {
                "ok": True,
                "action": "updated_incubating_topic",
                "relative_path": str(target.relative_to(VAULT_ROOT)),
                "replaced_size_bytes": len(old_text.encode("utf-8")),
            }
        else:
            created = create_inbox_note(title=clean_topic, content=content)
            result = {
                "ok": True,
                "action": "created_incubating_topic",
                "relative_path": created["relative_path"],
            }

        return {
            **result,
            "checkpoint": checkpoint_path,
            "lifecycle": lifecycle_key,
            "relations_applied": [],
            "next_action": "Continue this same topic note on later sessions; promote automatically once it is self-contained and reliable.",
        }

    if topic_note_path:
        target = normalize_knowledge_path(topic_note_path)
        if not target.exists():
            raise FileNotFoundError("topic_note_path Knowledge note not found")
        updated = update_knowledge_note(
            relative_path=str(target.relative_to(VAULT_ROOT)),
            content=content,
            reason="autonomous knowledge ingest",
        )
        knowledge_path = updated["relative_path"]
        result = {**updated, "action": "updated_knowledge_topic"}
    else:
        subdir = (knowledge_subdir or default_knowledge_subdir(normalized_schema)).strip().strip("/")
        created = create_inbox_note(title=clean_topic, content=content)
        promoted = promote_inbox_note(
            path_or_filename=created["relative_path"],
            knowledge_subdir=subdir,
            final_title=clean_topic,
        )
        knowledge_path = promoted["to"]
        result = {**promoted, "action": "created_knowledge_topic"}

    applied_relations: list[dict[str, str]] = []
    if relation_items:
        relation_result = apply_note_relations(
            relative_path=knowledge_path,
            relations=relation_items,
            apply=True,
            reason="autonomous knowledge ingest relations",
        )
        applied_relations = relation_result["added_or_updated"]

    return {
        **result,
        "ok": True,
        "checkpoint": checkpoint_path,
        "lifecycle": "knowledge",
        "relations_applied": applied_relations,
    }


@mcp.tool()
def bootstrap_llm_knowledge_base(overwrite: bool = False) -> dict[str, Any]:
    """
    Create the top-level Schemas, Indexes, and Context_Packs directories and
    seed them with the canonical Phase 1 Markdown documents.
    """
    records = collect_knowledge_note_records()
    result = bootstrap_llm_knowledge_base_files(
        vault_root=VAULT_ROOT,
        write_text_file=write_text_file,
        overwrite=overwrite,
        note_records=records,
    )
    return {
        "ok": True,
        "action": "bootstrapped_llm_knowledge_base",
        **result,
    }


@mcp.tool()
def list_schemas() -> list[dict[str, Any]]:
    """List the canonical schema documents available for the knowledge base."""
    SCHEMAS_DIR.mkdir(parents=True, exist_ok=True)
    result = []
    for schema_name, spec in SCHEMA_SPECS.items():
        relative_path = schema_relative_path(schema_name)
        path = (VAULT_ROOT / relative_path).resolve()
        result.append(
            {
                "schema_name": schema_name,
                "title": spec["title"],
                "relative_path": relative_path,
                "default_directory": spec["default_directory"],
                "exists": path.exists(),
            }
        )
    return result


@mcp.tool()
def fetch_schema(schema_name: str) -> dict[str, Any]:
    """Fetch a schema Markdown document from Schemas/."""
    schema_key = normalize_schema_name(schema_name)
    path = normalize_read_path(schema_relative_path(schema_key))
    if not path.exists():
        raise FileNotFoundError("Schema note not found")

    text = read_text_file(path)
    return {
        "ok": True,
        "schema_name": schema_key,
        "relative_path": str(path.relative_to(VAULT_ROOT)),
        "title": title_from_markdown(path, text),
        "content": text,
    }


@mcp.tool()
def list_context_packs() -> list[dict[str, Any]]:
    """List the canonical context-pack documents available for task execution."""
    CONTEXT_PACKS_DIR.mkdir(parents=True, exist_ok=True)
    result = []
    for name, spec in CONTEXT_PACK_SPECS.items():
        relative_path = context_pack_relative_path(name)
        path = (VAULT_ROOT / relative_path).resolve()
        result.append(
            {
                "context_pack_name": name,
                "title": spec["title"],
                "relative_path": relative_path,
                "exists": path.exists(),
            }
        )
    return result


@mcp.tool()
def fetch_context_pack(name: str) -> dict[str, Any]:
    """Fetch a context-pack Markdown document from Context_Packs/."""
    pack_name = normalize_context_pack_name(name)
    path = normalize_read_path(context_pack_relative_path(pack_name))
    if not path.exists():
        raise FileNotFoundError("Context pack not found")

    text = read_text_file(path)
    return {
        "ok": True,
        "context_pack_name": pack_name,
        "relative_path": str(path.relative_to(VAULT_ROOT)),
        "title": title_from_markdown(path, text),
        "content": text,
    }


@mcp.tool()
def validate_note_against_schema(relative_path: str, schema_name: str | None = None) -> dict[str, Any]:
    """
    Validate an Inbox or Knowledge note against one of the canonical schemas.
    Checks required frontmatter and required section headings only.
    """
    path = normalize_read_path(relative_path)
    if not path.exists():
        raise FileNotFoundError("Note not found")

    text = read_text_file(path)
    result = validate_note_text(text, schema_name_for_note(text, schema_name=schema_name))
    result["ok"] = True
    result["relative_path"] = str(path.relative_to(VAULT_ROOT))
    return result


@mcp.tool()
def update_knowledge_map(stale_days: int = 180) -> dict[str, Any]:
    """Refresh the auto-generated block in Indexes/knowledge-map.md."""
    records = collect_knowledge_note_records(stale_days=stale_days)
    ensure_support_docs_exist(note_records=records)
    relative_path = update_generated_index("knowledge-map", records, stale_days=stale_days)
    return {
        "ok": True,
        "action": "updated_knowledge_map",
        "relative_path": relative_path,
    }


@mcp.tool()
def update_decision_log(stale_days: int = 180) -> dict[str, Any]:
    """Refresh the auto-generated block in Indexes/decision-log.md."""
    records = collect_knowledge_note_records(stale_days=stale_days)
    ensure_support_docs_exist(note_records=records)
    relative_path = update_generated_index("decision-log", records, stale_days=stale_days)
    return {
        "ok": True,
        "action": "updated_decision_log",
        "relative_path": relative_path,
    }


@mcp.tool()
def generate_indexes(stale_days: int = 180) -> dict[str, Any]:
    """
    Rebuild the generated sections of index notes without overwriting the
    surrounding hand-written content.
    """
    records = collect_knowledge_note_records(stale_days=stale_days)
    ensure_support_docs_exist(note_records=records)

    changed_files = []
    for name in ["knowledge-map", "workflows", "decision-log", "stale-notes"]:
        changed_files.append(update_generated_index(name, records, stale_days=stale_days))

    return {
        "ok": True,
        "action": "generated_indexes",
        "changed_files": changed_files,
    }


@mcp.tool()
def find_duplicate_notes() -> list[dict[str, Any]]:
    """Find duplicate titles under Knowledge/."""
    return duplicate_note_titles(collect_knowledge_note_records())


@mcp.tool()
def find_stale_notes(stale_days: int = 180) -> list[dict[str, Any]]:
    """Find verified notes that have not been updated for a while."""
    return stale_note_records(collect_knowledge_note_records(stale_days=stale_days))


@mcp.tool()
def find_broken_asset_links() -> list[dict[str, Any]]:
    """Find embedded assets referenced by Knowledge notes that do not exist."""
    return broken_asset_links(collect_knowledge_note_records())


@mcp.tool()
def find_broken_note_links() -> list[dict[str, Any]]:
    """Find Obsidian wiki links in Knowledge notes whose target note is missing."""
    return broken_note_links(collect_knowledge_note_records())


@mcp.tool()
def find_schema_violations() -> list[dict[str, Any]]:
    """Find Knowledge notes missing schema-required metadata or sections."""
    return schema_violations(collect_knowledge_note_records())


@mcp.tool()
def audit_knowledge_base(stale_days: int = 180, inbox_backlog_threshold: int = 20) -> dict[str, Any]:
    """
    Run a non-destructive health audit against the vault and return findings.
    """
    records = collect_knowledge_note_records(stale_days=stale_days)
    inbox_count = len(list(INBOX_DIR.glob("*.md"))) if INBOX_DIR.exists() else 0
    duplicates = duplicate_note_titles(records)
    stale = stale_note_records(records)
    broken = broken_asset_links(records)
    broken_notes = broken_note_links(records)
    violations = schema_violations(records)
    missing_metadata = [
        {
            "relative_path": record["relative_path"],
            "title": record["title"],
            "missing_fields": [
                field for field in ["type", "status", "tags"]
                if not frontmatter_map(record["content"]).get(field)
            ],
        }
        for record in records
        if any(not frontmatter_map(record["content"]).get(field) for field in ["type", "status", "tags"])
    ]
    knowledge_drafts = [
        {
            "relative_path": record["relative_path"],
            "title": record["title"],
        }
        for record in records
        if (record.get("status") or "").strip().lower() == "draft"
    ]

    recommendations = []
    if inbox_count > inbox_backlog_threshold:
        recommendations.append("Inbox 堆积较多，优先清理待确认草稿。")
    if duplicates:
        recommendations.append("处理重复标题，避免 LLM 选择错误知识卡。")
    if stale:
        recommendations.append("复查长期未更新但仍是 verified 的知识卡。")
    if broken:
        recommendations.append("补齐丢失的 Assets 或修复嵌入链接。")
    if broken_notes:
        recommendations.append("修复指向不存在 Markdown 笔记的 Obsidian 双链。")
    if violations or missing_metadata:
        recommendations.append("按 schema 补齐 frontmatter 和必填章节。")
    if not recommendations:
        recommendations.append("当前知识库未发现高优先级结构问题。")

    return {
        "ok": True,
        "summary": {
            "inbox_backlog_count": inbox_count,
            "knowledge_draft_count": len(knowledge_drafts),
            "duplicate_title_count": len(duplicates),
            "stale_note_count": len(stale),
            "missing_metadata_count": len(missing_metadata),
            "broken_asset_link_count": len(broken),
            "broken_note_link_count": len(broken_notes),
            "schema_violation_count": len(violations),
            "rollback_audit_supported": True,
        },
        "checks": {
            "inbox_backlog_exceeded": inbox_count > inbox_backlog_threshold,
            "knowledge_drafts": knowledge_drafts,
            "duplicate_titles": duplicates,
            "stale_notes": stale,
            "missing_metadata": missing_metadata,
            "broken_asset_links": broken,
            "broken_note_links": broken_notes,
            "schema_violations": violations,
        },
        "recommendations": recommendations,
    }


async def http_health(request):
    try:
        for d in [INBOX_DIR, CAPTURE_DIR, KNOWLEDGE_DIR, SOURCES_DIR, TEMPLATES_DIR, SCHEMAS_DIR, INDEXES_DIR, CONTEXT_PACKS_DIR, ASSETS_DIR, ARCHIVE_DIR, VERSIONS_DIR, DELETED_DIR]:
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
            "schemas": str(SCHEMAS_DIR),
            "indexes": str(INDEXES_DIR),
            "context_packs": str(CONTEXT_PACKS_DIR),
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
