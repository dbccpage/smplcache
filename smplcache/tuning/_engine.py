"""A small learning loop with five verbs: propose, evaluate, authorize, record, learn.

The loop watches a system's own receipts, proposes typed candidate changes,
tests each candidate against recorded history, and lets a separate authorizer
decide. Nothing self-applies: a candidate reaches the caller's ``apply``
callback only after a ``supported`` verdict and an ``approved`` authorization,
and every step is appended to a hash-chained receipt log that can be replayed.

Design rules enforced here, not merely documented:

- Proposer, evaluator and authorizer are three distinct objects. ``Loop``
  refuses to start when one object fills two roles, and refuses an
  authorization whose ``authorizer`` name equals the candidate's ``origin``.
- Verdicts are ``supported``, ``refuted`` or ``underdetermined``; only
  ``supported`` may be authorized. ``underdetermined`` always needs a human.
- ``learn`` builds a mistake memory from refuted receipts. Every proposal is
  checked against it first, so a refuted candidate is not proposed twice.
- Receipts are content-addressed (SHA-256 over canonical JSON including the
  previous hash). ``ReceiptLog.verify`` finds the first broken link.

Standard library only. ``numpy`` is never required. This file is the reference
implementation; a verbatim copy may be vendored by another project, which
records the source revision it copied.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

Json = dict[str, Any]
VerdictStatus = Literal["supported", "refuted", "underdetermined"]
AuthorizationStatus = Literal["approved", "denied", "needs_human"]
ReceiptStep = Literal[
    "proposed",
    "skipped_known_mistake",
    "evaluated",
    "authorized",
    "deferred",
    "applied",
    "apply_failed",
    "learned",
]

ENGINE_VERSION = "tuner/1"


def canonical_json(value: Any) -> str:
    """Deterministic JSON: sorted keys, no whitespace, ASCII only."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)


def content_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("ascii")).hexdigest()


class TunerError(Exception):
    """Base class for refusals raised by the loop."""


class RoleConflict(TunerError):
    """One object was offered for two of proposer / evaluator / authorizer."""


class SelfAuthorization(TunerError):
    """An authorization named the candidate's own origin as its authorizer."""


class ReceiptChainBroken(TunerError):
    """A receipt's hash does not match its content or its predecessor."""


@dataclass(frozen=True)
class Candidate:
    """A typed proposed change. ``payload`` must be JSON-serialisable."""

    kind: str
    payload: Mapping[str, Any]
    origin: str
    rationale: str = ""

    @property
    def candidate_id(self) -> str:
        return content_hash({"kind": self.kind, "payload": dict(self.payload)})

    def to_json(self) -> Json:
        return {
            "candidate_id": self.candidate_id,
            "kind": self.kind,
            "payload": dict(self.payload),
            "origin": self.origin,
            "rationale": self.rationale,
        }


@dataclass(frozen=True)
class Verdict:
    """What recorded history says about a candidate, with the numbers behind it."""

    status: VerdictStatus
    evaluator: str
    reason: str
    evidence: Mapping[str, Any] = field(default_factory=dict)

    def to_json(self) -> Json:
        return {
            "status": self.status,
            "evaluator": self.evaluator,
            "reason": self.reason,
            "evidence": dict(self.evidence),
        }


@dataclass(frozen=True)
class Authorization:
    status: AuthorizationStatus
    authorizer: str
    reason: str
    policy_id: str

    def to_json(self) -> Json:
        return {
            "status": self.status,
            "authorizer": self.authorizer,
            "reason": self.reason,
            "policy_id": self.policy_id,
        }


@dataclass(frozen=True)
class Policy:
    """A declared authorization rule. Anything not covered needs a human."""

    policy_id: str
    auto_approve_kinds: frozenset[str] = frozenset()
    human_review_kinds: frozenset[str] = frozenset()
    min_improvement: float = 0.0
    improvement_key: str = "improvement"
    max_applied_per_run: int | None = None

    def to_json(self) -> Json:
        return {
            "policy_id": self.policy_id,
            "auto_approve_kinds": sorted(self.auto_approve_kinds),
            "human_review_kinds": sorted(self.human_review_kinds),
            "min_improvement": self.min_improvement,
            "improvement_key": self.improvement_key,
            "max_applied_per_run": self.max_applied_per_run,
        }


@dataclass(frozen=True)
class Receipt:
    seq: int
    step: ReceiptStep
    candidate_id: str
    body: Mapping[str, Any]
    prev_hash: str
    hash: str

    @staticmethod
    def compute_hash(seq: int, step: str, candidate_id: str, body: Mapping[str, Any], prev_hash: str) -> str:
        return content_hash(
            {
                "seq": seq,
                "step": step,
                "candidate_id": candidate_id,
                "body": dict(body),
                "prev_hash": prev_hash,
                "engine": ENGINE_VERSION,
            }
        )

    def to_json(self) -> Json:
        return {
            "seq": self.seq,
            "step": self.step,
            "candidate_id": self.candidate_id,
            "body": dict(self.body),
            "prev_hash": self.prev_hash,
            "hash": self.hash,
        }

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> Receipt:
        return cls(
            seq=int(data["seq"]),
            step=data["step"],
            candidate_id=str(data["candidate_id"]),
            body=dict(data["body"]),
            prev_hash=str(data["prev_hash"]),
            hash=str(data["hash"]),
        )


GENESIS_HASH = "0" * 64


class ReceiptLog:
    """Append-only, hash-chained receipts. ``verify`` returns the first bad index or -1."""

    def __init__(self, receipts: Iterable[Receipt] = ()) -> None:
        self._receipts: list[Receipt] = list(receipts)

    def __len__(self) -> int:
        return len(self._receipts)

    def __iter__(self) -> Any:
        return iter(self._receipts)

    @property
    def receipts(self) -> tuple[Receipt, ...]:
        return tuple(self._receipts)

    @property
    def head(self) -> str:
        return self._receipts[-1].hash if self._receipts else GENESIS_HASH

    def append(self, step: ReceiptStep, candidate_id: str, body: Mapping[str, Any]) -> Receipt:
        seq = len(self._receipts)
        prev = self.head
        digest = Receipt.compute_hash(seq, step, candidate_id, body, prev)
        receipt = Receipt(seq=seq, step=step, candidate_id=candidate_id, body=dict(body), prev_hash=prev, hash=digest)
        self._receipts.append(receipt)
        return receipt

    def verify(self) -> int:
        """Return -1 when the whole chain is intact, else the index of the first bad receipt."""
        prev = GENESIS_HASH
        for index, receipt in enumerate(self._receipts):
            if receipt.seq != index or receipt.prev_hash != prev:
                return index
            expected = Receipt.compute_hash(receipt.seq, receipt.step, receipt.candidate_id, receipt.body, receipt.prev_hash)
            if receipt.hash != expected:
                return index
            prev = receipt.hash
        return -1

    def require_intact(self) -> None:
        bad = self.verify()
        if bad >= 0:
            raise ReceiptChainBroken(f"receipt {bad} does not match its content or predecessor")

    def to_json(self) -> list[Json]:
        return [r.to_json() for r in self._receipts]

    @classmethod
    def from_json(cls, data: Iterable[Mapping[str, Any]]) -> ReceiptLog:
        return cls(Receipt.from_json(d) for d in data)

    def dumps(self) -> str:
        return json.dumps(self.to_json(), indent=1, sort_keys=True)

    @classmethod
    def loads(cls, text: str) -> ReceiptLog:
        return cls.from_json(json.loads(text))


@dataclass(frozen=True)
class Mistake:
    candidate_id: str
    kind: str
    reason: str
    evidence: Mapping[str, Any]
    receipt_seq: int


class MistakeMemory:
    """Refuted candidates and why. Checked before any new proposal."""

    def __init__(self) -> None:
        self._by_id: dict[str, Mistake] = {}

    def __len__(self) -> int:
        return len(self._by_id)

    def __contains__(self, candidate_id: str) -> bool:
        return candidate_id in self._by_id

    def lookup(self, candidate: Candidate) -> Mistake | None:
        return self._by_id.get(candidate.candidate_id)

    def remember(self, mistake: Mistake) -> None:
        self._by_id.setdefault(mistake.candidate_id, mistake)

    @property
    def mistakes(self) -> tuple[Mistake, ...]:
        return tuple(self._by_id.values())

    def to_json(self) -> list[Json]:
        return [
            {
                "candidate_id": m.candidate_id,
                "kind": m.kind,
                "reason": m.reason,
                "evidence": dict(m.evidence),
                "receipt_seq": m.receipt_seq,
            }
            for m in self._by_id.values()
        ]


class Proposer(Protocol):
    name: str

    def propose(self, context: Mapping[str, Any]) -> Sequence[Candidate]: ...


class Evaluator(Protocol):
    name: str

    def evaluate(self, candidate: Candidate, history: Any) -> Verdict: ...


class Authorizer(Protocol):
    name: str

    def authorize(self, candidate: Candidate, verdict: Verdict, policy: Policy) -> Authorization: ...


def propose(proposer: Proposer, context: Mapping[str, Any], memory: MistakeMemory | None = None) -> list[Candidate]:
    """Ask ``proposer`` for candidates; stamp each with the proposer's name as origin.

    Candidates already in ``memory`` are left in the result (the loop records them
    as ``skipped_known_mistake``) so the caller can see what was filtered and why.
    """
    out: list[Candidate] = []
    for cand in proposer.propose(context):
        if cand.origin != proposer.name:
            cand = Candidate(kind=cand.kind, payload=cand.payload, origin=proposer.name, rationale=cand.rationale)
        out.append(cand)
    return out


def evaluate(evaluator: Evaluator, candidate: Candidate, history: Any) -> Verdict:
    verdict = evaluator.evaluate(candidate, history)
    if verdict.status not in ("supported", "refuted", "underdetermined"):
        raise TunerError(f"evaluator returned an unknown status: {verdict.status!r}")
    if verdict.evaluator != evaluator.name:
        verdict = Verdict(status=verdict.status, evaluator=evaluator.name, reason=verdict.reason, evidence=verdict.evidence)
    return verdict


def authorize(authorizer: Authorizer, candidate: Candidate, verdict: Verdict, policy: Policy) -> Authorization:
    """Run the authorizer, then enforce the separation rules on its answer."""
    if authorizer.name == candidate.origin:
        raise SelfAuthorization(f"{authorizer.name!r} may not authorize its own proposal")
    auth = authorizer.authorize(candidate, verdict, policy)
    if auth.authorizer == candidate.origin:
        raise SelfAuthorization(f"authorization names the proposer {candidate.origin!r} as authorizer")
    if auth.status == "approved" and verdict.status != "supported":
        return Authorization(
            status="denied",
            authorizer=auth.authorizer,
            reason=f"approval refused: verdict is {verdict.status}, not supported",
            policy_id=policy.policy_id,
        )
    return auth


def record(log: ReceiptLog, step: ReceiptStep, candidate: Candidate | str, body: Mapping[str, Any]) -> Receipt:
    candidate_id = candidate if isinstance(candidate, str) else candidate.candidate_id
    return log.append(step, candidate_id, body)


def learn(receipts: Iterable[Receipt], memory: MistakeMemory | None = None) -> MistakeMemory:
    """Fold refuted verdicts from ``receipts`` into a mistake memory."""
    memory = memory if memory is not None else MistakeMemory()
    for receipt in receipts:
        if receipt.step != "evaluated":
            continue
        verdict = receipt.body.get("verdict", {})
        if verdict.get("status") != "refuted":
            continue
        candidate = receipt.body.get("candidate", {})
        memory.remember(
            Mistake(
                candidate_id=receipt.candidate_id,
                kind=str(candidate.get("kind", "")),
                reason=str(verdict.get("reason", "")),
                evidence=dict(verdict.get("evidence", {})),
                receipt_seq=receipt.seq,
            )
        )
    return memory


class PolicyAuthorizer:
    """Authorizes from the declared ``Policy`` alone. Never consults the proposer."""

    def __init__(self, name: str = "policy") -> None:
        self.name = name

    def authorize(self, candidate: Candidate, verdict: Verdict, policy: Policy) -> Authorization:
        if verdict.status == "refuted":
            return Authorization("denied", self.name, "verdict refuted: " + verdict.reason, policy.policy_id)
        if verdict.status == "underdetermined":
            return Authorization("needs_human", self.name, "verdict underdetermined: " + verdict.reason, policy.policy_id)
        if candidate.kind in policy.human_review_kinds:
            return Authorization("needs_human", self.name, f"kind {candidate.kind!r} is reserved for human review", policy.policy_id)
        if candidate.kind not in policy.auto_approve_kinds:
            return Authorization("needs_human", self.name, f"kind {candidate.kind!r} has no auto-approve rule", policy.policy_id)
        improvement = verdict.evidence.get(policy.improvement_key)
        if not isinstance(improvement, (int, float)):
            return Authorization("needs_human", self.name, f"evidence lacks numeric {policy.improvement_key!r}", policy.policy_id)
        if improvement < policy.min_improvement:
            return Authorization(
                "denied",
                self.name,
                f"{policy.improvement_key}={improvement} below policy minimum {policy.min_improvement}",
                policy.policy_id,
            )
        return Authorization("approved", self.name, f"policy {policy.policy_id} auto-approves {candidate.kind}", policy.policy_id)


@dataclass
class RunSummary:
    proposed: int = 0
    skipped_known_mistakes: int = 0
    supported: int = 0
    refuted: int = 0
    underdetermined: int = 0
    approved: int = 0
    denied: int = 0
    needs_human: int = 0
    deferred: int = 0
    applied: int = 0
    apply_failed: int = 0
    applied_candidates: list[Candidate] = field(default_factory=list)
    pending_human: list[tuple[Candidate, Verdict, Authorization]] = field(default_factory=list)

    @property
    def proposal_error_rate(self) -> float:
        """Fraction of evaluated proposals that history refuted."""
        evaluated = self.supported + self.refuted + self.underdetermined
        return (self.refuted / evaluated) if evaluated else 0.0

    def to_json(self) -> Json:
        return {
            "proposed": self.proposed,
            "skipped_known_mistakes": self.skipped_known_mistakes,
            "supported": self.supported,
            "refuted": self.refuted,
            "underdetermined": self.underdetermined,
            "approved": self.approved,
            "denied": self.denied,
            "needs_human": self.needs_human,
            "deferred": self.deferred,
            "applied": self.applied,
            "apply_failed": self.apply_failed,
            "proposal_error_rate": self.proposal_error_rate,
        }


class Loop:
    """Wire the five verbs together with the separation rules enforced."""

    def __init__(
        self,
        proposer: Proposer,
        evaluator: Evaluator,
        authorizer: Authorizer,
        policy: Policy,
        log: ReceiptLog | None = None,
        memory: MistakeMemory | None = None,
    ) -> None:
        roles: dict[str, Proposer | Evaluator | Authorizer] = {
            "proposer": proposer,
            "evaluator": evaluator,
            "authorizer": authorizer,
        }
        for a, obj_a in roles.items():
            for b, obj_b in roles.items():
                if a < b and (obj_a is obj_b or obj_a.name == obj_b.name):
                    raise RoleConflict(f"{a} and {b} are the same component ({obj_a.name!r})")
        self.proposer = proposer
        self.evaluator = evaluator
        self.authorizer = authorizer
        self.policy = policy
        self.log = log if log is not None else ReceiptLog()
        self.memory = memory if memory is not None else learn(self.log)

    def run_once(
        self,
        context: Mapping[str, Any],
        history: Any,
        apply: Callable[[Candidate], Mapping[str, Any]] | None = None,
        run_label: str = "",
    ) -> RunSummary:
        self.log.require_intact()
        summary = RunSummary()
        for candidate in propose(self.proposer, context, self.memory):
            summary.proposed += 1
            known = self.memory.lookup(candidate)
            if known is not None:
                summary.skipped_known_mistakes += 1
                record(
                    self.log,
                    "skipped_known_mistake",
                    candidate,
                    {"run": run_label, "candidate": candidate.to_json(), "prior_reason": known.reason, "prior_receipt_seq": known.receipt_seq},
                )
                continue
            record(self.log, "proposed", candidate, {"run": run_label, "candidate": candidate.to_json()})
            verdict = evaluate(self.evaluator, candidate, history)
            setattr(summary, verdict.status, getattr(summary, verdict.status) + 1)
            receipt = record(
                self.log, "evaluated", candidate, {"run": run_label, "candidate": candidate.to_json(), "verdict": verdict.to_json()}
            )
            if verdict.status == "refuted":
                learn([receipt], self.memory)
                record(self.log, "learned", candidate, {"run": run_label, "reason": verdict.reason, "memory_size": len(self.memory)})
            auth = authorize(self.authorizer, candidate, verdict, self.policy)
            setattr(summary, auth.status, getattr(summary, auth.status) + 1)
            record(
                self.log,
                "authorized",
                candidate,
                {"run": run_label, "authorization": auth.to_json(), "policy": self.policy.to_json(), "verdict_status": verdict.status},
            )
            if auth.status == "needs_human":
                summary.pending_human.append((candidate, verdict, auth))
                continue
            if auth.status != "approved" or verdict.status != "supported":
                continue
            if apply is None:
                continue
            cap = self.policy.max_applied_per_run
            if cap is not None and summary.applied >= cap:
                summary.deferred += 1
                record(self.log, "deferred", candidate, {"run": run_label, "reason": f"policy applies at most {cap} change(s) per run"})
                continue
            try:
                outcome = apply(candidate)
            except Exception as exc:  # the failure is a receipt, not a crash
                summary.apply_failed += 1
                record(self.log, "apply_failed", candidate, {"run": run_label, "error": f"{type(exc).__name__}: {exc}"})
                continue
            summary.applied += 1
            summary.applied_candidates.append(candidate)
            record(self.log, "applied", candidate, {"run": run_label, "outcome": dict(outcome)})
        return summary


def replay(log: ReceiptLog) -> Json:
    """Rebuild the loop's observable state from receipts alone (no live components)."""
    log.require_intact()
    memory = learn(log)
    applied: list[str] = []
    pending: list[str] = []
    for receipt in log:
        if receipt.step == "applied":
            applied.append(receipt.candidate_id)
        elif receipt.step == "authorized" and receipt.body.get("authorization", {}).get("status") == "needs_human":
            pending.append(receipt.candidate_id)
    return {
        "receipts": len(log),
        "head": log.head,
        "mistakes": memory.to_json(),
        "applied": applied,
        "pending_human": pending,
    }
