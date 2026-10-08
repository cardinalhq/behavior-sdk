# Copyright (c) 2025-2026 CardinalHQ, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Bounded, receipted semantic decisions for diagnostic UDFs.

The caller may select candidates using mechanically knowable fields (operation
name, identity, time, source). It must not gate candidates with lexical or
heuristic approximations of the proposition that ``decide`` will judge. This
module forwards every supplied evidence item verbatim; it never prefilters by
words in the proposition. Typed extraction is intentionally outside v1.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Protocol


class Verdict(str, Enum):
    YES = "YES"
    NO = "NO"
    UNCERTAIN = "UNCERTAIN"


class PacketError(ValueError):
    """The caller supplied an invalid or oversized evidence packet."""


class JudgeExecutionError(RuntimeError):
    """No valid semantic decision after bounded transport/response attempts."""


class JudgeBudgetExceeded(RuntimeError):
    """The diagnostic exceeded its configured decision-call budget."""


@dataclass(frozen=True)
class EvidenceItem:
    ref: str
    field: str
    text: str


@dataclass(frozen=True)
class JudgeConfig:
    model: str
    model_version: str
    prompt_version: str = "jev-decide-v1"
    max_packet_chars: int = 16_000
    max_items: int = 64
    max_calls: int = 8
    max_input_tokens: int = 20_000
    max_output_tokens: int = 256
    max_retries: int = 1
    input_usd_per_million: float = 0.0
    output_usd_per_million: float = 0.0

    def __post_init__(self) -> None:
        if any(not isinstance(value, str) or not value for value in
               (self.model, self.model_version, self.prompt_version)):
            raise ValueError("model, model_version and prompt_version are required")
        if any(type(value) is not int or value <= 0 for value in
               (self.max_packet_chars, self.max_items, self.max_calls,
                self.max_input_tokens, self.max_output_tokens)):
            raise ValueError("judge limits must be positive integers")
        if type(self.max_retries) is not int or not 0 <= self.max_retries <= 2:
            raise ValueError("max_retries must be between 0 and 2")
        if any(type(price) not in (int, float) or not math.isfinite(price) or price < 0 for price in
               (self.input_usd_per_million, self.output_usd_per_million)):
            raise ValueError("token prices must be finite and nonnegative")

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)

    @property
    def digest(self) -> str:
        return _sha(self.as_dict())


@dataclass(frozen=True)
class Decision:
    verdict: Verdict
    evidence_refs: tuple[str, ...]
    reason: str
    receipt: Mapping[str, Any] = field(compare=False)

    @property
    def yes(self):
        return self.verdict == Verdict.YES

    @property
    def no(self):
        return self.verdict == Verdict.NO

    @property
    def uncertain(self):
        return self.verdict == Verdict.UNCERTAIN

    def __bool__(self):
        raise TypeError("Decision is tri-state; inspect yes, no, or uncertain explicitly")


@dataclass(frozen=True)
class PacketCheck:
    """Exact mechanical preflight; invalid provenance still raises PacketError."""
    fits: bool
    reasons: tuple[str, ...]
    item_count: int
    packet_chars: int
    request_bytes: int

    def __bool__(self):
        raise TypeError("inspect PacketCheck.fits explicitly")


class JudgeBackend(Protocol):
    """Backend receives one fixed-format request and returns a model response.

    Response is ``{"text": JSON-string, "usage": {"input_tokens": int,
    "output_tokens": int}, "model": str, "model_version": str}``. Usage may
    be absent or partial when unavailable; it must never be invented as zero.
    Optional ``cost_usd`` reports actual request cost. The backend must enforce
    the requested model/output token limit and report the provider identity.
    Network transport and credentials live outside diagnostic code.
    """

    def __call__(self, request: Mapping[str, Any]) -> Mapping[str, Any]: ...


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _field_text(source: Any, field_name: str, *, is_trace: bool) -> str:
    if field_name.startswith("attrs.") and field_name[6:]:
        key = field_name[6:]
        value = source.attrs.get(key)
    elif is_trace:
        raise PacketError("run supports only attrs.<key> evidence fields")
    elif field_name in {"input", "output", "error", "name"}:
        value = getattr(source, field_name)
    else:
        raise PacketError(f"unsupported evidence field {field_name!r}")
    if value is None:
        return ""
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def _parse_reply(text: str, items: tuple[EvidenceItem, ...]) -> tuple[Verdict, tuple[str, ...], str]:
    try:
        obj = json.loads(text)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid_json") from exc
    if not isinstance(obj, dict) or set(obj) != {"answer", "evidence_refs", "short_reason"}:
        raise ValueError("invalid_schema")
    try:
        verdict = Verdict(obj["answer"])
    except (ValueError, TypeError) as exc:
        raise ValueError("invalid_answer") from exc
    refs = obj["evidence_refs"]
    if not isinstance(refs, list) or not all(type(n) is int and 1 <= n <= len(items) for n in refs):
        raise ValueError("invalid_evidence_refs")
    reason = obj["short_reason"]
    if not isinstance(reason, str) or len(reason) > 300:
        raise ValueError("invalid_reason")
    return verdict, tuple(dict.fromkeys(items[n - 1].ref for n in refs)), reason


def _prepare_packet(trace, config, proposition, evidence, subject_refs, frame):
    events = {e.id: e for e in trace.events}
    def resolve(ref):
        if ref == "run":
            return trace, True
        if ref not in events:
            raise PacketError(f"unknown trace ref {ref!r}")
        return events[ref], False
    reasons = []
    if not isinstance(proposition, str) or not proposition or len(proposition) > 1_000:
        raise PacketError("proposition must contain 1-1000 characters")
    if not isinstance(frame, str) or len(frame) > 600:
        raise PacketError("frame must contain at most 600 characters")
    items = tuple(evidence)
    if not 1 <= len(items) <= config.max_items:
        reasons.append("item_limit")
    subjects = tuple(subject_refs)
    for ref in subjects:
        if not isinstance(ref, str):
            raise PacketError("subject refs must be strings")
        resolve(ref)
    for item in items:
        if not isinstance(item, EvidenceItem) or not isinstance(item.text, str):
            raise PacketError("evidence items must be EvidenceItem(ref, field, text)")
        source, is_trace = resolve(item.ref)
        if item.text not in _field_text(source, item.field, is_trace=is_trace):
            raise PacketError(f"evidence is not a verbatim substring of {item.ref}.{item.field}")
    packet = {"proposition": proposition, "frame": frame, "subject_refs": subjects,
              "evidence": [{"ref": i.ref, "field": i.field, "text": i.text} for i in items]}
    chars = len(proposition) + len(frame) + sum(len(i.text) for i in items)
    if chars > config.max_packet_chars:
        reasons.append("packet_chars_limit")
    request = {"task": "bounded_decision", "trace_id": trace.trace_id,
               "prompt_version": config.prompt_version,
               "config_digest": config.digest,
               "model": config.model, "model_version": config.model_version,
               "max_input_tokens": config.max_input_tokens,
               "max_output_tokens": config.max_output_tokens,
               "instruction": "Return only JSON with answer (YES, NO, UNCERTAIN), evidence_refs (1-based item numbers), and short_reason (at most 300 characters).",
               "packet": packet}
    # A token can encode only part of a Unicode character; bound UTF-8 bytes,
    # not Python code points. Count the full
    # serialized request, including the fixed instruction and source refs.
    if len(_canonical(request).encode("utf-8")) > config.max_input_tokens:
        reasons.append("request_bytes_limit")
    check = PacketCheck(not reasons, tuple(reasons), len(items), chars, len(_canonical(request).encode("utf-8")))
    return packet, request, check


class JEV:
    """Public bounded decision ABI, shared by direct and sandbox transports."""

    def decide(self, *, proposition: str, evidence: list[EvidenceItem] | tuple[EvidenceItem, ...],
               subject_refs: list[str] | tuple[str, ...] = (), frame: str = "") -> Decision:
        return self._decide(proposition=proposition, evidence=evidence,
                            subject_refs=subject_refs, frame=frame)

    def preflight(self, *, proposition, evidence, subject_refs=(), frame="") -> PacketCheck:
        """No model call, truncation, or verdict. Uses exactly decide's packet checks.

        An exhausted model-call budget is a separate execution concern. Hosts
        expose trace/config on their JEV proxy; preflight never consumes budget.
        """
        return _prepare_packet(self.trace, self.config, proposition, tuple(evidence),
                               tuple(subject_refs), frame)[2]


class Judge(JEV):
    """Runtime-owned semantic decision service bound to one trace and profile.

    ``decide`` cannot accept a freeform prompt or choose a model. The caller
    supplies a proposition and trace-backed evidence; this service validates
    provenance, applies bounds, validates responses and keeps receipts. A
    failed/malformed model execution raises JudgeExecutionError with a receipt;
    only a valid UNCERTAIN answer represents semantic uncertainty.
    """

    def __init__(self, trace: Any, config: JudgeConfig, backend: JudgeBackend):
        self.trace = trace
        self.config = config
        self._backend = backend
        self.receipts: list[dict[str, Any]] = []
        self._events = {e.id: e for e in trace.events}
        self._cache: dict[str, Decision] = {}

    def _source(self, ref: str) -> tuple[Any, bool]:
        if ref == "run":
            return self.trace, True
        if ref in self._events:
            return self._events[ref], False
        raise PacketError(f"unknown trace ref {ref!r}")

    def _decide(self, *, proposition: str, evidence: list[EvidenceItem] | tuple[EvidenceItem, ...],
               subject_refs: list[str] | tuple[str, ...] = (), frame: str = "") -> Decision:
        items = tuple(evidence)
        packet, request, check = _prepare_packet(self.trace, self.config, proposition, items, subject_refs, frame)
        subjects = packet["subject_refs"]
        if not check.fits:
            messages = {"item_limit": f"evidence must contain 1-{self.config.max_items} items",
                        "packet_chars_limit": "evidence packet exceeds configured character limit",
                        "request_bytes_limit": "judge request exceeds conservative input token limit"}
            raise PacketError("; ".join(messages[reason] for reason in check.reasons))
        packet_hash = _sha(packet)
        if packet_hash in self._cache:
            return self._cache[packet_hash]
        if len(self.receipts) >= self.config.max_calls:
            raise JudgeBudgetExceeded("decision-call budget exceeded for this trace")
        request_hash = _sha(request)
        attempts: list[dict[str, Any]] = []
        verdict, refs, reason = Verdict.UNCERTAIN, (), "judge unavailable"
        parse_status = "unavailable"
        reported_usage = {"input_tokens": 0, "output_tokens": 0}
        complete_usage = dict.fromkeys(reported_usage, True)
        total_cost = 0.0
        complete_cost = True
        cost_sources: set[str] = set()
        actual_models: list[str] = []
        interrupted: BaseException | None = None
        for attempt in range(self.config.max_retries + 1):
            raw = None
            details: dict[str, Any] = {"attempt": attempt + 1,
                                      "usage": dict.fromkeys(reported_usage),
                                      "cost_usd": None, "cost_basis": "unavailable"}
            error_code = "backend_failure"
            terminal = False
            try:
                response = self._backend(request)
                error_code = "invalid_backend_response"
                if not isinstance(response, Mapping):
                    raise ValueError("backend response must be a mapping")
                raw = response.get("text")
                # Preserve all trustworthy accounting before any model, limit,
                # refusal or decision validation can fail this attempt.
                usage = response.get("usage")
                invalid_usage = usage is not None and not isinstance(usage, Mapping)
                if isinstance(usage, Mapping):
                    for key in reported_usage:
                        value = usage.get(key)
                        if value is None:
                            continue
                        if type(value) is not int or value < 0:
                            invalid_usage = True
                        else:
                            details["usage"][key] = value
                invalid_cost = False
                cost = response.get("cost_usd")
                if cost is not None:
                    if type(cost) not in (int, float) or not math.isfinite(cost) or cost < 0:
                        invalid_cost = True
                    else:
                        details.update(cost_usd=cost, cost_basis="backend")
                elif (all(value is not None for value in details["usage"].values())
                      and (self.config.input_usd_per_million or self.config.output_usd_per_million)):
                    details.update(cost_usd=(details["usage"]["input_tokens"] * self.config.input_usd_per_million
                                             + details["usage"]["output_tokens"] * self.config.output_usd_per_million) / 1_000_000,
                                   cost_basis="configured_token_prices")
                actual_model = response.get("model")
                actual_version = response.get("model_version")
                details["model"] = actual_model if isinstance(actual_model, str) else None
                details["model_version"] = actual_version if isinstance(actual_version, str) else None
                if isinstance(actual_model, str):
                    actual_models.append(actual_model)
                error_code = "invalid_provider_metadata"
                if "replayed" in response:
                    if type(response["replayed"]) is not bool:
                        raise ValueError("invalid replay indicator")
                    details["replayed"] = response["replayed"]
                if "provider" in response:
                    provider = response["provider"]
                    if not isinstance(provider, str) or not 1 <= len(provider) <= 128:
                        raise ValueError("invalid provider identity")
                    details["provider"] = provider
                if "provider_answer" in response:
                    provider_answer = _canonical(response["provider_answer"])
                    if len(provider_answer.encode("utf-8")) > 4096:
                        raise ValueError("provider answer exceeds receipt bound")
                    details["provider_answer"] = json.loads(provider_answer)
                for key, limit in (("input_tokens", self.config.max_input_tokens),
                                   ("output_tokens", self.config.max_output_tokens)):
                    value = details["usage"][key]
                    if value is not None and value > limit:
                        error_code = key + "_limit_exceeded"
                        terminal = True  # Retry must not repeat an over-budget call.
                        raise ValueError("backend exceeded configured token limit")
                error_code = "invalid_usage"
                if invalid_usage:
                    raise ValueError("invalid usage")
                error_code = "invalid_cost"
                if invalid_cost:
                    raise ValueError("invalid cost")
                error_code = "invalid_model_identity"
                if not isinstance(actual_model, str):
                    raise ValueError("invalid response model")
                if actual_model != self.config.model or actual_version != self.config.model_version:
                    raise ValueError("backend model differs from pinned configuration")
                error_code = "response_size_limit_exceeded"
                if isinstance(raw, str) and len(raw) > self.config.max_output_tokens * 8:
                    raise ValueError("response exceeds configured output bound")
                error_code = "refusal"
                if response.get("refusal") is True:
                    raise ValueError("judge refused to execute decision")
                error_code = "invalid_decision"
                verdict, refs, reason = _parse_reply(raw, items)
                parse_status = "ok"
            except BaseException as exc:  # record cancellation, but never retry or swallow it
                if not isinstance(exc, Exception):
                    interrupted = exc
                    terminal = True
                    error_code = "execution_interrupted"
                parse_status = "invalid_after_retry" if terminal or attempt == self.config.max_retries else "retrying"
                reason = f"judge execution failed: {error_code} ({type(exc).__name__})"
                details.update(error=type(exc).__name__, error_code=error_code)
            details.update(status=parse_status, response_sha256=_sha(raw) if isinstance(raw, str) else None)
            attempts.append(details)
            for key, value in details["usage"].items():
                if value is None:
                    complete_usage[key] = False
                else:
                    reported_usage[key] += value
            if details["cost_usd"] is None:
                complete_cost = False
            else:
                total_cost += details["cost_usd"]
                cost_sources.add(details["cost_basis"])
            if parse_status != "retrying":
                break
        failed = parse_status == "invalid_after_retry"
        receipt = {"receipt_version": "jev-receipt-v1",
                   "trace_id": self.trace.trace_id,
                   "packet_sha256": packet_hash, "request_sha256": request_hash,
                   "proposition": proposition, "frame": frame, "subject_refs": list(subjects),
                   "evidence": packet["evidence"], "config": self.config.as_dict(),
                   "config_digest": self.config.digest, "actual_models": actual_models,
                   "decision": "ERROR" if failed else verdict.value, "decision_evidence_refs": list(refs),
                   "reason": reason, "parse_status": parse_status, "attempts": attempts,
                   "usage": {key: reported_usage[key] if complete_usage[key] else None
                             for key in reported_usage},
                   "reported_usage": reported_usage, "usage_complete": complete_usage,
                   "cost_usd": total_cost if complete_cost else None,
                   "cost_basis": "+".join(sorted(cost_sources)) if complete_cost else "unavailable",
                   "replay_key": request_hash}
        receipt["receipt_id"] = _sha(receipt)
        decision = Decision(verdict, refs, reason, receipt)
        self.receipts.append(receipt)
        if interrupted is not None:
            raise interrupted
        if failed:
            raise JudgeExecutionError(reason)
        self._cache[packet_hash] = decision
        return decision


__all__ = ["Decision", "EvidenceItem", "Judge", "JudgeBackend", "JudgeBudgetExceeded",
           "JudgeConfig", "JudgeExecutionError", "PacketError", "Verdict"]
