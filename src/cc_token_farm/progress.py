from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from cc_token_farm.util import ensure_parent


@dataclass
class ProgressState:
    version: int = 1
    session_id: str = ""
    started_at: float = 0.0
    updated_at: float = 0.0
    proxy: str = ""
    format: str = ""
    model: str = ""
    target_tokens: int = 0
    max_cost_usd: float = 0.0
    total: int = 0
    success: int = 0
    failed: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    cost_usd: float = 0.0
    status: str = "running"  # running | completed | stopped | failed
    note: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_read_tokens
            + self.cache_creation_tokens
        )


class ProgressStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> ProgressState | None:
        if not self.path.exists():
            return None
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            known = {f.name for f in ProgressState.__dataclass_fields__.values()}  # type: ignore[attr-defined]
            filtered = {k: v for k, v in data.items() if k in known}
            return ProgressState(**filtered)
        except Exception:
            return None

    def save(self, state: ProgressState) -> None:
        state.updated_at = time.time()
        ensure_parent(self.path)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(asdict(state), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(self.path)
