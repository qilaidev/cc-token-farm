from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

from cc_token_farm.client import RequestResult
from cc_token_farm.pricing import ModelPrice
from cc_token_farm.util import human_int, human_usd


@dataclass
class Stats:
    total: int = 0
    success: int = 0
    failed: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    cost_usd: float = 0.0
    latencies: list[float] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)
    by_model: dict[str, dict[str, float | int]] = field(default_factory=dict)
    consecutive_failures: int = 0
    last_error: str = ""
    # Tokens/cost already counted before this process started (resume baseline).
    # Rate / ETA use only progress made after baseline so resume does not explode tok/s.
    baseline_tokens: int = 0
    baseline_cost_usd: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def add(self, r: RequestResult, price: ModelPrice | None = None, multiplier: float = 1.0) -> None:
        cost = 0.0
        if price:
            cost = price.cost_usd(
                r.input_tokens,
                r.output_tokens,
                r.cache_read_tokens,
                r.cache_creation_tokens,
                cost_multiplier=multiplier,
            )
        with self._lock:
            self.total += 1
            if r.ok:
                self.success += 1
                self.consecutive_failures = 0
            else:
                self.failed += 1
                self.consecutive_failures += 1
                self.last_error = r.error or f"http={r.status}"
            self.input_tokens += r.input_tokens
            self.output_tokens += r.output_tokens
            self.cache_read_tokens += r.cache_read_tokens
            self.cache_creation_tokens += r.cache_creation_tokens
            self.cost_usd += cost
            self.latencies.append(r.latency_ms)
            # keep latency memory bounded for multi-day runs
            if len(self.latencies) > 50_000:
                self.latencies = self.latencies[-20_000:]
            mid = r.model or "unknown"
            bucket = self.by_model.setdefault(
                mid,
                {
                    "requests": 0,
                    "success": 0,
                    "input": 0,
                    "output": 0,
                    "cache_read": 0,
                    "cache_creation": 0,
                    "cost": 0.0,
                },
            )
            bucket["requests"] = int(bucket["requests"]) + 1
            if r.ok:
                bucket["success"] = int(bucket["success"]) + 1
            bucket["input"] = int(bucket["input"]) + r.input_tokens
            bucket["output"] = int(bucket["output"]) + r.output_tokens
            bucket["cache_read"] = int(bucket["cache_read"]) + r.cache_read_tokens
            bucket["cache_creation"] = int(bucket["cache_creation"]) + r.cache_creation_tokens
            bucket["cost"] = float(bucket["cost"]) + cost

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_read_tokens
            + self.cache_creation_tokens
        )

    @property
    def elapsed(self) -> float:
        return max(0.001, time.time() - self.started_at)

    def tokens_per_sec(self) -> float:
        # Prefer this-run progress so --resume does not report absurd tok/s.
        run_tokens = max(0, self.total_tokens - self.baseline_tokens)
        if run_tokens > 0:
            return run_tokens / self.elapsed
        return self.total_tokens / self.elapsed

    def snapshot(self) -> dict:
        with self._lock:
            avg = (sum(self.latencies) / len(self.latencies)) if self.latencies else 0.0
            p95 = 0.0
            if self.latencies:
                s = sorted(self.latencies)
                p95 = s[min(len(s) - 1, int(len(s) * 0.95))]
            return {
                "total": self.total,
                "success": self.success,
                "failed": self.failed,
                "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens,
                "cache_read_tokens": self.cache_read_tokens,
                "cache_creation_tokens": self.cache_creation_tokens,
                "total_tokens": self.total_tokens,
                "cost_usd": self.cost_usd,
                "avg_latency_ms": avg,
                "p95_latency_ms": p95,
                "elapsed_s": self.elapsed,
                "tokens_per_sec": self.tokens_per_sec(),
                "consecutive_failures": self.consecutive_failures,
                "last_error": self.last_error,
                "by_model": {k: dict(v) for k, v in self.by_model.items()},
            }

    def summary_line(self) -> str:
        s = self.snapshot()
        return (
            f"req={s['success']}/{s['total']} fail={s['failed']} | "
            f"tok={human_int(s['total_tokens'])} "
            f"(in={human_int(s['input_tokens'])} out={human_int(s['output_tokens'])} "
            f"cr={human_int(s['cache_read_tokens'])} cc={human_int(s['cache_creation_tokens'])}) | "
            f"cost≈{human_usd(s['cost_usd'])} | "
            f"{s['tokens_per_sec']:.0f} tok/s | "
            f"avg={s['avg_latency_ms']:.0f}ms p95={s['p95_latency_ms']:.0f}ms"
        )

    def eta_for_target(self, target_tokens: int) -> str:
        if target_tokens <= 0:
            return "n/a"
        remain = max(0, target_tokens - self.total_tokens)
        rate = self.tokens_per_sec()
        if rate <= 0:
            return "…"
        sec = remain / rate
        if sec < 60:
            return f"{sec:.0f}s"
        if sec < 3600:
            return f"{sec/60:.1f}m"
        if sec < 86400:
            return f"{sec/3600:.1f}h"
        return f"{sec/86400:.2f}d"
