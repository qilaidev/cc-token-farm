from __future__ import annotations

import random
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable

from cc_token_farm.client import (
    ProxyClient,
    RequestResult,
    build_user_prompt,
    check_proxy,
    default_cache_system,
)
from cc_token_farm.config import FarmConfig
from cc_token_farm.pricing import ModelPrice, PricingCatalog
from cc_token_farm.progress import ProgressState, ProgressStore
from cc_token_farm.stats import Stats
from cc_token_farm.util import human_int, human_usd


class FarmEngine:
    def __init__(
        self,
        cfg: FarmConfig,
        catalog: PricingCatalog,
        stop_event: threading.Event | None = None,
        on_result: Callable[[RequestResult, Stats], None] | None = None,
    ) -> None:
        self.cfg = cfg
        self.catalog = catalog
        self.stop = stop_event or threading.Event()
        self.on_result = on_result
        self.stats = Stats()
        self._seq = 0
        self._seq_lock = threading.Lock()
        self._model_idx = 0
        self._model_lock = threading.Lock()
        self._rps_lock = threading.Lock()
        self._next_slot = 0.0
        self._prices: dict[str, ModelPrice] = {
            m: catalog.require(m) for m in cfg.effective_models()
        }
        self._system = cfg.system
        if cfg.cache and not self._system:
            self._system = default_cache_system()
        self.client = ProxyClient(
            proxy=cfg.proxy,
            api_key=cfg.api_key,
            timeout=cfg.timeout,
            extra_headers=cfg.headers,
        )
        self.progress_path = (
            Path(cfg.progress_file)
            if cfg.progress_file
            else Path.home() / ".cc-token-farm" / "progress.json"
        )
        self.store = ProgressStore(self.progress_path)
        self.session_id = uuid.uuid4().hex[:12]
        self.state = ProgressState(
            session_id=self.session_id,
            started_at=time.time(),
            proxy=cfg.proxy,
            format=cfg.format,
            model=",".join(cfg.effective_models()),
            target_tokens=cfg.target_tokens or 0,
            max_cost_usd=cfg.max_cost_usd or 0.0,
            status="running",
        )

        self._stop_reason = ""  # budget | fail_threshold | target | interrupt | proxy

        if cfg.resume:
            prev = self.store.load()
            if prev is None and self.progress_path.exists():
                print(
                    f"[warn] --resume: progress file unreadable or corrupt: {self.progress_path}",
                    file=sys.stderr,
                )
            elif prev and prev.status in {"running", "stopped"}:
                self.stats.input_tokens = prev.input_tokens
                self.stats.output_tokens = prev.output_tokens
                self.stats.cache_read_tokens = prev.cache_read_tokens
                self.stats.cache_creation_tokens = prev.cache_creation_tokens
                self.stats.cost_usd = prev.cost_usd
                self.stats.total = prev.total
                self.stats.success = prev.success
                self.stats.failed = prev.failed
                # Baseline so tok/s and ETA use only this-run progress.
                self.stats.baseline_tokens = self.stats.total_tokens
                self.stats.baseline_cost_usd = self.stats.cost_usd
                self.state = prev
                self.state.status = "running"
                self.session_id = prev.session_id or self.session_id
            elif prev and prev.status not in {"running", "stopped"}:
                print(
                    f"[warn] --resume: previous status={prev.status!r}; "
                    f"starting a new session (use a different --progress-file to keep).",
                    file=sys.stderr,
                )

    def _next_seq(self) -> int:
        with self._seq_lock:
            self._seq += 1
            return self._seq

    def _pick_model(self) -> str:
        models = self.cfg.effective_models()
        if len(models) == 1:
            return models[0]
        with self._model_lock:
            m = models[self._model_idx % len(models)]
            self._model_idx += 1
            return m

    def _rate_limit(self) -> None:
        rps = self.cfg.rps
        if not rps or rps <= 0:
            return
        min_interval = 1.0 / rps
        with self._rps_lock:
            now = time.time()
            if self._next_slot <= now:
                self._next_slot = now + min_interval
                return
            wait = self._next_slot - now
            self._next_slot += min_interval
        self.stop.wait(wait)

    def _should_continue(self, done_this_run: int) -> bool:
        if self.stop.is_set():
            return False
        if self.stats.consecutive_failures >= self.cfg.fail_threshold:
            return False
        if self.cfg.max_cost_usd is not None and self.stats.cost_usd >= self.cfg.max_cost_usd:
            return False
        if self.cfg.forever:
            return True
        if self.cfg.target_tokens is not None:
            return self.stats.total_tokens < self.cfg.target_tokens
        if self.cfg.count is not None:
            # count is for this run, not including resumed totals for request count mode
            return done_this_run < self.cfg.count
        return done_this_run < 10

    def _one(self) -> RequestResult:
        if self.stop.is_set():
            return RequestResult(ok=False, status=None, latency_ms=0, error="stopped")
        self._rate_limit()
        if self.stop.is_set():
            return RequestResult(ok=False, status=None, latency_ms=0, error="stopped")

        seq = self._next_seq()
        model = self._pick_model()
        user_text = build_user_prompt(self.cfg.prompt, self.cfg.prompt_chars, seq)

        if self.cfg.dry_run:
            return RequestResult(
                ok=True,
                status=0,
                latency_ms=0,
                model=model,
                input_tokens=max(1, len(user_text) // 4),
                output_tokens=min(self.cfg.max_tokens, 8),
                preview="dry-run",
            )

        return self.client.send(
            fmt=self.cfg.format,
            model=model,
            user_text=user_text,
            max_tokens=self.cfg.max_tokens,
            system=self._system,
            stream=self.cfg.stream,
            enable_cache=self.cfg.cache,
        )

    def _persist(self, status: str | None = None) -> None:
        snap = self.stats.snapshot()
        self.state.total = snap["total"]
        self.state.success = snap["success"]
        self.state.failed = snap["failed"]
        self.state.input_tokens = snap["input_tokens"]
        self.state.output_tokens = snap["output_tokens"]
        self.state.cache_read_tokens = snap["cache_read_tokens"]
        self.state.cache_creation_tokens = snap["cache_creation_tokens"]
        self.state.cost_usd = snap["cost_usd"]
        if status:
            self.state.status = status
        if self._stop_reason:
            self.state.note = self._stop_reason
        if self.stats.last_error:
            self.state.extra = {
                **(self.state.extra or {}),
                "last_error": self.stats.last_error,
                "consecutive_failures": self.stats.consecutive_failures,
            }
        try:
            self.store.save(self.state)
        except Exception as e:  # noqa: BLE001
            # Always surface persist failures: long runs rely on progress for resume.
            print(f"[warn] progress save failed: {e}", file=sys.stderr)

    def estimate_banner(self) -> str:
        models = self.cfg.effective_models()
        lines = [
            f"▶ session={self.session_id}",
            f"  proxy={self.cfg.proxy} format={self.cfg.format}",
            f"  models={','.join(models)}",
            f"  concurrency={self.cfg.concurrency} interval={self.cfg.interval}s "
            f"rps={self.cfg.rps or '-'} max_tokens={self.cfg.max_tokens} "
            f"prompt_chars={self.cfg.prompt_chars} cache={self.cfg.cache}",
        ]
        if self.cfg.target_tokens:
            lines.append(f"  target_tokens={human_int(self.cfg.target_tokens)}")
            # estimate cost per model
            for mid in models:
                p = self._prices[mid]
                est = p.estimate_for_mix(
                    self.cfg.target_tokens,
                    input_ratio=0.85,
                    output_ratio=0.15,
                    cost_multiplier=self.cfg.cost_multiplier,
                )
                lines.append(
                    f"  estimate[{mid}] ~{human_usd(est)} "
                    f"(in ${p.input}/M out ${p.output}/M ×{self.cfg.cost_multiplier})"
                )
        if self.cfg.max_cost_usd is not None:
            lines.append(f"  max_cost_usd={human_usd(self.cfg.max_cost_usd)} (hard stop)")
        if self.cfg.count:
            lines.append(f"  count={self.cfg.count}")
        if self.cfg.forever:
            lines.append("  mode=forever")
        lines.append(f"  progress={self.progress_path}")
        lines.append(f"  pricing_source={self.catalog.source}")
        return "\n".join(lines)

    def _budget_batch_size(self, concurrency: int) -> int:
        """Shrink in-flight batch near max_cost to limit overshoot."""
        if self.cfg.max_cost_usd is None or self.stats.total == 0:
            return concurrency
        remaining = self.cfg.max_cost_usd - self.stats.cost_usd
        if remaining <= 0:
            return 0
        # Average cost per completed request this session (incl. resume totals).
        per_req = self.stats.cost_usd / max(1, self.stats.total)
        if per_req <= 0:
            return concurrency
        # Keep headroom for ~1 request; clamp to [1, concurrency].
        safe = max(1, int(remaining / per_req))
        return min(concurrency, safe)

    def run(self) -> int:
        if not self.cfg.quiet:
            print(self.estimate_banner())

        if self.cfg.stream and not self.cfg.quiet:
            print(
                "[warn] --stream: usage tokens/cost are often missing from stream bodies; "
                "token counters and --max-cost-usd may under-count. Prefer non-stream for budgets.",
                file=sys.stderr,
            )

        if not self.cfg.dry_run:
            ok, msg = check_proxy(self.cfg.proxy, timeout=min(5.0, self.cfg.timeout))
            if not ok:
                print(f"[error] {msg}", file=sys.stderr)
                print(
                    "Start CC Switch local proxy and enable app takeover "
                    f"({'Claude' if self.cfg.format == 'anthropic' else 'Codex'}).",
                    file=sys.stderr,
                )
                self._stop_reason = "proxy_unreachable"
                self._persist("failed")
                return 2
            if not self.cfg.quiet:
                print(f"✓ {msg}")

        # high cost confirmation for huge targets
        if (
            self.cfg.confirm_high_cost
            and self.cfg.target_tokens
            and not self.cfg.dry_run
            and sys.stdin.isatty()
        ):
            mid = self.cfg.primary_model()
            est = self._prices[mid].estimate_for_mix(
                self.cfg.target_tokens, cost_multiplier=self.cfg.cost_multiplier
            )
            if est >= self.cfg.high_cost_usd:
                print(
                    f"\n⚠ Estimated cost ≈ {human_usd(est)} for target "
                    f"{human_int(self.cfg.target_tokens)} tokens on {mid}.\n"
                    f"  Real upstream traffic — real bills. Continue? [y/N] ",
                    end="",
                    flush=True,
                )
                ans = sys.stdin.readline().strip().lower()
                if ans not in {"y", "yes"}:
                    print("Aborted.")
                    self._stop_reason = "user_abort"
                    self._persist("stopped")
                    return 130

        concurrency = max(1, self.cfg.concurrency)
        interval = max(0.0, self.cfg.interval)
        jitter = max(0.0, self.cfg.jitter)
        done = 0
        last_persist = 0.0
        backoff = 0.0

        try:
            with ThreadPoolExecutor(max_workers=concurrency) as pool:
                while self._should_continue(done):
                    if self.stop.is_set():
                        break

                    if self.cfg.target_tokens is not None:
                        batch = concurrency
                    elif self.cfg.forever:
                        batch = concurrency
                    else:
                        remain = (self.cfg.count or 10) - done
                        if remain <= 0:
                            break
                        batch = min(concurrency, remain)

                    # Near budget: reduce in-flight requests to limit overshoot.
                    budget_batch = self._budget_batch_size(concurrency)
                    if budget_batch == 0:
                        self._stop_reason = "max_cost_usd"
                        if not self.cfg.quiet:
                            print(
                                f"[stop] max cost reached: {human_usd(self.stats.cost_usd)}"
                            )
                        self.stop.set()
                        break
                    batch = min(batch, budget_batch)

                    futures = [pool.submit(self._one) for _ in range(batch)]
                    for fut in as_completed(futures):
                        if self.stop.is_set():
                            break
                        try:
                            result = fut.result()
                        except Exception as e:  # noqa: BLE001
                            result = RequestResult(
                                ok=False, status=None, latency_ms=0, error=str(e)
                            )
                        price = self._prices.get(result.model) or self.catalog.require(
                            result.model or self.cfg.primary_model()
                        )
                        self.stats.add(result, price, multiplier=self.cfg.cost_multiplier)
                        done += 1

                        if self.on_result:
                            self.on_result(result, self.stats)
                        elif not self.cfg.quiet:
                            flag = "OK" if result.ok else "FAIL"
                            print(
                                f"[{flag}] #{self.stats.total} {result.model} "
                                f"http={result.status} {result.latency_ms:.0f}ms "
                                f"in={result.input_tokens} out={result.output_tokens} "
                                f"cr={result.cache_read_tokens} cc={result.cache_creation_tokens} "
                                f"| {result.error or result.preview}"
                            )

                        # adaptive backoff on failures
                        if not result.ok:
                            backoff = min(
                                self.cfg.backoff_max,
                                max(self.cfg.backoff_base, backoff * 1.5 or self.cfg.backoff_base),
                            )
                        else:
                            backoff = 0.0

                        if (
                            self.cfg.max_cost_usd is not None
                            and self.stats.cost_usd >= self.cfg.max_cost_usd
                        ):
                            if not self.cfg.quiet:
                                print(
                                    f"[stop] max cost reached: {human_usd(self.stats.cost_usd)}"
                                )
                            self._stop_reason = "max_cost_usd"
                            self.stop.set()
                            break
                        if (
                            self.cfg.target_tokens is not None
                            and self.stats.total_tokens >= self.cfg.target_tokens
                        ):
                            self._stop_reason = "target_tokens"
                            self.stop.set()
                            break
                        if self.stats.consecutive_failures >= self.cfg.fail_threshold:
                            print(
                                f"[stop] {self.cfg.fail_threshold} consecutive failures. "
                                f"last={self.stats.last_error}",
                                file=sys.stderr,
                            )
                            self._stop_reason = "fail_threshold"
                            self.stop.set()
                            break

                    now = time.time()
                    if now - last_persist >= 2.0:
                        self._persist("running")
                        last_persist = now
                        if not self.cfg.quiet and self.cfg.target_tokens:
                            print(
                                f"… {self.stats.summary_line()} | "
                                f"ETA {self.stats.eta_for_target(self.cfg.target_tokens)}"
                            )

                    if not self._should_continue(done):
                        break

                    delay = interval + backoff
                    if jitter > 0:
                        delay += random.uniform(0, jitter)
                    if delay > 0 and not self.stop.is_set():
                        self.stop.wait(delay)

        except KeyboardInterrupt:
            self.stop.set()
            self._stop_reason = self._stop_reason or "interrupt"
            if not self.cfg.quiet:
                print("\n[interrupt] stopping…")

        # final status
        if self._stop_reason == "max_cost_usd":
            # Budget hit is a controlled stop, not a crash.
            status = "completed" if self.stats.success > 0 or self.cfg.dry_run else "stopped"
            code = 0 if self.stats.success > 0 or self.cfg.dry_run else 1
        elif self._stop_reason == "fail_threshold" and self.stats.success == 0:
            status = "failed"
            code = 1
        elif self.stats.consecutive_failures >= self.cfg.fail_threshold and self.stats.success == 0:
            status = "failed"
            code = 1
        elif self.stop.is_set() and (
            (self.cfg.target_tokens and self.stats.total_tokens < self.cfg.target_tokens)
            or (self.cfg.count and done < (self.cfg.count or 0))
            or self.cfg.forever
        ):
            status = "stopped"
            code = 130 if self.stats.success == 0 else 0
            self._stop_reason = self._stop_reason or "stopped"
        else:
            status = "completed"
            code = 0 if self.stats.success > 0 or self.cfg.dry_run else 1
            if not self._stop_reason:
                self._stop_reason = "completed"

        self._persist(status)

        if not self.cfg.quiet:
            print("─" * 64)
            print(self.stats.summary_line())
            reason = f" reason={self._stop_reason}" if self._stop_reason else ""
            print(
                f"status={status}{reason} elapsed={self.stats.elapsed:.1f}s "
                f"progress={self.progress_path}"
            )
            if self.stats.by_model:
                print("by model:")
                for mid, b in sorted(self.stats.by_model.items()):
                    print(
                        f"  {mid}: ok={b['success']}/{b['requests']} "
                        f"tok={human_int(int(b['input'])+int(b['output'])+int(b['cache_read'])+int(b['cache_creation']))} "
                        f"cost≈{human_usd(float(b['cost']))}"
                    )
            print(
                "Note: dashboard tokens are CC Switch normalized values; "
                "printed usage comes from response bodies and may differ slightly."
            )
        return code
