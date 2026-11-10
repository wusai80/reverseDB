"""Public v1 extension contracts. Records use the existing JSON portfolio schema.

Plugins are trusted Python code, not a sandbox. The framework isolates record
copies and enforces evidence/admission boundaries, not plugin truthfulness.
"""
from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Literal, Mapping, Protocol

Record = Mapping[str, Any]


def fingerprint(value: Record) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


@dataclass(frozen=True)
class EvidenceStore:
    root: Path

    def read(self, ref: Record) -> bytes:
        root = Path(self.root).resolve()
        path = (root / ref['path']).resolve()
        if not path.is_relative_to(root):
            raise ValueError('Evidence outside repository')
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != ref['sha256']:
            raise ValueError('Changed artifact: ' + ref['path'])
        return data

    def document(self, ref: Record) -> dict:
        return json.loads(self.read(ref))


@dataclass(frozen=True)
class Verdict:
    status: Literal['allow', 'retain', 'unknown']
    reason: str
    details: Record = field(default_factory=dict)


@dataclass(frozen=True)
class MeasuredPlan:
    id: str
    family: str
    leaf_count: int
    blocks: tuple
    values_ms: tuple[float, ...]


@dataclass(frozen=True)
class Choice:
    selected: str
    reason: str
    details: Record = field(default_factory=dict)


class CandidateProvider(Protocol):
    def propose(self, context: Record, store: EvidenceStore) -> Iterable[Record]:
        """Supply concrete candidate records; proposals alone grant no admission."""
        ...


class SemanticValidator(Protocol):
    def validate(self, context: Record, candidate: Record, store: EvidenceStore) -> Verdict:
        """Check one family's obligations; unknown and retain both block admission."""
        ...


class MeasurementProvider(Protocol):
    def measure(self, context: Record, candidate: Record, store: EvidenceStore) -> Record | None:
        """Return a bound cost record, or None. Called only after admission.

        May replay evidence or run a benchmark in an explicitly configured
        implementation. It cannot change semantic eligibility.
        """
        ...


class SelectionPolicy(Protocol):
    def choose(self, context: Record, plans: tuple[MeasuredPlan, ...]) -> Choice:
        """Choose from admitted comparable plans, including original.

        No artifact handles or validation mutators are exposed here. The
        framework rejects a returned ID outside this supplied set.
        """
        ...
