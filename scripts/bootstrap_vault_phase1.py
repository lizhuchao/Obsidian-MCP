#!/usr/bin/env python3

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from kb_seed import bootstrap_llm_knowledge_base_files


LOCAL_TZ = ZoneInfo("Asia/Shanghai")


def read_text_file(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write_text_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content if content.endswith("\n") else content + "\n", encoding="utf-8")


def title_from_markdown(path: Path, text: str) -> str:
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return path.stem


def frontmatter_value(text: str, key: str) -> str | None:
    if not text.startswith("---\n"):
        return None

    end = text.find("\n---", 4)
    if end == -1:
        return None

    prefix = key + ":"
    for line in text[4:end].splitlines():
        if line.startswith(prefix):
            return line[len(prefix):].strip()
    return None


def collect_knowledge_records(vault_root: Path) -> list[dict]:
    knowledge_root = vault_root / "Knowledge"
    if not knowledge_root.exists():
        return []

    records = []
    for path in knowledge_root.rglob("*.md"):
        text = read_text_file(path)
        modified_at = datetime.fromtimestamp(path.stat().st_mtime, LOCAL_TZ)
        age_days = max(0, (datetime.now(LOCAL_TZ) - modified_at).days)
        records.append(
            {
                "relative_path": str(path.relative_to(vault_root)),
                "title": title_from_markdown(path, text),
                "type": frontmatter_value(text, "type"),
                "status": frontmatter_value(text, "status"),
                "modified_at": modified_at.isoformat(),
                "age_days": age_days,
                "is_stale": frontmatter_value(text, "status") == "verified" and age_days >= 180,
            }
        )
    return records


def main() -> int:
    if len(sys.argv) not in {2, 3}:
        print("Usage: bootstrap_vault_phase1.py <vault_root> [--scan-knowledge]", file=sys.stderr)
        return 2

    vault_root = Path(sys.argv[1]).expanduser().resolve()
    scan_knowledge = len(sys.argv) == 3 and sys.argv[2] == "--scan-knowledge"
    if not vault_root.exists() or not vault_root.is_dir():
        print(f"Vault root not found: {vault_root}", file=sys.stderr)
        return 1

    result = bootstrap_llm_knowledge_base_files(
        vault_root=vault_root,
        write_text_file=write_text_file,
        overwrite=False,
        note_records=collect_knowledge_records(vault_root) if scan_knowledge else [],
    )
    payload = {
        "ok": True,
        "vault_root": str(vault_root),
        **result,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
