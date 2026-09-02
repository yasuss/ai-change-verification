"""Vendor-neutral ACV Stage B Core with durable V11 decision semantics.

The Core accepts trusted capture objects from a host adapter. It never executes
commands, reads provider configuration, or treats a receipt as live authority.
SQLite writer transactions are the linearization point for registration and
finalization.
"""

from __future__ import annotations

import contextlib
import dataclasses
import enum
import hashlib
import importlib.util
import json
import os
import pathlib
import sqlite3
import sys
from typing import Any, Iterable, Mapping, Optional


class StageBError(RuntimeError):
    """Base error for fail-closed Stage B operations."""


class StoreCapabilityError(StageBError):
    """The local store cannot provide its declared durability profile."""


class ProvenanceError(StageBError):
    """The canonical provenance graph is incomplete, cyclic, or invalid."""


class TransformMode(str, enum.Enum):
    EXPLICIT_INPUT_TRANSFORM = "EXPLICIT_INPUT_TRANSFORM"
    OPAQUE_TRUST_BASE_TRANSFORM = "OPAQUE_TRUST_BASE_TRANSFORM"


class InvocationBindingMode(str, enum.Enum):
    CANONICAL_EXPLICIT_INPUT = "CANONICAL_EXPLICIT_INPUT"
    TRUSTED_INVOCATION_SNAPSHOT_DIGEST_OR_EPOCH = "TRUSTED_INVOCATION_SNAPSHOT_DIGEST_OR_EPOCH"
    REALIZATION_INVARIANT_INTERNAL_STATE = "REALIZATION_INVARIANT_INTERNAL_STATE"
    NON_REUSABLE_OPAQUE_STATE = "NON_REUSABLE_OPAQUE_STATE"


class VerificationRunStatus(str, enum.Enum):
    COMPLETE = "COMPLETE"
    INCOMPLETE = "INCOMPLETE"
    INVALID = "INVALID"


class ReviewReadiness(str, enum.Enum):
    READY_FOR_HUMAN_REVIEW = "READY_FOR_HUMAN_REVIEW"
    NOT_READY_FOR_HUMAN_REVIEW = "NOT_READY_FOR_HUMAN_REVIEW"
    BLOCKED_ON_MISSING_EVIDENCE = "BLOCKED_ON_MISSING_EVIDENCE"


_TERMINAL_STATUSES = frozenset({"PASS", "FAIL", "ERROR", "CANCELLED", "NOT_RUN", "INCONCLUSIVE"})
_EXECUTED_TERMINAL_STATUSES = frozenset({"PASS", "FAIL", "ERROR", "CANCELLED"})
_BINDING_MODES = frozenset(item.value for item in InvocationBindingMode)
_MODULE_MISSING = object()


def _plain(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        return {key: _plain(item) for key, item in dataclasses.asdict(value).items()}
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_plain(item) for item in value]
    return value


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(_plain(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _identity(kind: str, value: Any) -> str:
    return _sha256(kind.encode("utf-8") + b"\0" + _canonical_bytes(value))


def _get(value: Any, name: str, default: Any = None) -> Any:
    return value.get(name, default) if isinstance(value, Mapping) else getattr(value, name, default)


@dataclasses.dataclass(frozen=True)
class ProviderCapabilities:
    admission_visibility_complete: bool = False
    effective_operation_identity: bool = False
    terminal_outcome_identity: bool = False
    material_execution_context_binding: bool = False
    final_event_frontier: bool = False
    drain_acknowledgement: bool = False
    realization_accepted: bool = False

    @property
    def qualifying(self) -> bool:
        return all(dataclasses.astuple(self))


@dataclasses.dataclass(frozen=True)
class InvocationInputBinding:
    mode: str
    value: Any = None
    binding_id: Optional[str] = None
    reusable: bool = True

    def __post_init__(self) -> None:
        mode = self.mode.value if isinstance(self.mode, enum.Enum) else self.mode
        if mode not in _BINDING_MODES:
            raise StageBError("UNSUPPORTED_INVOCATION_INPUT_BINDING_MODE")
        if mode == InvocationBindingMode.NON_REUSABLE_OPAQUE_STATE.value and self.reusable:
            raise StageBError("OPAQUE_STATE_MUST_BE_NON_REUSABLE")

    @property
    def canonical_id(self) -> str:
        return self.binding_id or _identity("invocation-input-binding", self)


@dataclasses.dataclass(frozen=True)
class ProviderEvent:
    event_id: str
    invocation_id: str
    check_id: str
    status: str
    operation: Any = None
    payload: Any = None
    sequence: int = 0

    @classmethod
    def from_value(cls, value: Any) -> "ProviderEvent":
        if isinstance(value, cls):
            return value
        if not isinstance(value, Mapping):
            raise StageBError("PROVIDER_EVENT_INVALID")
        try:
            return cls(str(value["event_id"]), str(value["invocation_id"]), str(value["check_id"]), str(value["status"]), value.get("operation"), value.get("payload"), int(value.get("sequence", 0)))
        except (KeyError, TypeError, ValueError) as exc:
            raise StageBError("PROVIDER_EVENT_INVALID") from exc

    @property
    def terminal(self) -> bool:
        return self.status in _TERMINAL_STATUSES

    @property
    def executed_terminal(self) -> bool:
        return self.status in _EXECUTED_TERMINAL_STATUSES

    def canonical(self, acquisition_id: Optional[str] = None) -> dict[str, Any]:
        value = _plain(self)
        if acquisition_id is not None:
            value["acquisition_id"] = acquisition_id
        return value


@dataclasses.dataclass(frozen=True)
class TransformRecord:
    producer_realization_id: str
    transform_mode: str
    direct_input_object_ids: tuple[str, ...] = ()
    invocation_input_bindings: tuple[Any, ...] = ()
    capability_profile_id: Optional[str] = None
    transform_contract_identity: Optional[str] = None
    object_id: Optional[str] = None

    def __post_init__(self) -> None:
        mode = self.transform_mode.value if isinstance(self.transform_mode, enum.Enum) else self.transform_mode
        if mode not in {item.value for item in TransformMode}:
            raise ProvenanceError("TRANSFORM_MODE_INVALID")
        if not self.producer_realization_id:
            raise ProvenanceError("PRODUCER_REALIZATION_MISSING")

    @property
    def canonical_object_id(self) -> str:
        return self.object_id or _identity("provenance-transform", self)


@dataclasses.dataclass(frozen=True)
class AuthoritySnapshot:
    snapshot_id: str
    subject_digest: str
    policy_snapshot_id: str = ""
    authority_root_id: str = ""
    accepted_realization_ids: tuple[str, ...] = ()
    revoked_realization_ids: tuple[str, ...] = ()
    topology_complete: bool = False
    complete: bool = False
    current_epoch: Optional[str] = None
    self_authorized: bool = False

    def accepts(self, realization_id: Optional[str]) -> bool:
        return bool(realization_id) and not self.self_authorized and self.complete and self.topology_complete and realization_id in self.accepted_realization_ids and realization_id not in self.revoked_realization_ids

    def accepts_required(self, realization_ids: Iterable[str]) -> bool:
        required = tuple(str(item) for item in realization_ids)
        return bool(required) and all(self.accepts(item) for item in required)


@dataclasses.dataclass(frozen=True)
class FinalizationResult:
    decision_key: str
    verification_run_status: str
    review_readiness: str
    finalization_id: Optional[str] = None
    generation: Optional[int] = None
    required_fact_roots: tuple[str, ...] = ()
    trust_closure_digest: Optional[str] = None
    decision_protocol_realization_id: str = ""
    limitations: tuple[str, ...] = ()
    reason_codes: tuple[str, ...] = ()
    committed: bool = False
    subject_digest: str = ""
    policy_snapshot_id: str = ""
    authority_snapshot_id: str = ""
    provider_realization_ids: tuple[str, ...] = ()
    provider_capability_profile_ids: tuple[str, ...] = ()
    store_profile_id: str = ""
    acquisition_epoch: Optional[int] = None
    operation_id: str = ""
    attempt_lineage_digest: str = ""
    lineage_blockers: tuple[str, ...] = ()
    subject_snapshot_id: str = ""
    verification_contract_id: str = ""

    @property
    def state(self) -> str:
        return self.review_readiness


@dataclasses.dataclass(frozen=True)
class CurrentReadinessResult:
    state: str
    reason_codes: tuple[str, ...] = ()
    finalization_id: Optional[str] = None
    current: bool = False
    generation: Optional[int] = None
    acquisition_epoch: Optional[int] = None
    subject_snapshot_id: str = ""
    policy_snapshot_id: str = ""
    authority_snapshot_id: str = ""
    decision_protocol_realization_id: str = ""
    store_profile_id: str = ""
    trust_closure_digest: str = ""


class RunBoundEvidenceSession:
    """Trusted adapter-fed capture for one operation and one subject."""

    def __init__(self, session_id: str, subject_digest: str, provider_realization_id: str, provider_capability_profile_id: str, capabilities: ProviderCapabilities, verification_contract_id: str = "") -> None:
        self.session_id = session_id
        self.subject_digest = subject_digest
        self.provider_realization_id = provider_realization_id
        self.provider_capability_profile_id = provider_capability_profile_id
        self.capabilities = capabilities
        self.invocations: dict[str, dict[str, Any]] = {}
        self.events: dict[str, ProviderEvent] = {}
        self.sealed = False
        self.final_provider_event_frontier: Optional[int] = None
        self.trusted_ingestion_drain_frontier: Optional[int] = None
        self.store: Optional[DecisionStore] = None
        self.decision_key = ""
        self.acquisition_id = ""
        self.acquisition_epoch: Optional[int] = None
        self.operation_id = ""
        self.post_execution_snapshot_id = ""
        self.verification_contract_id = verification_contract_id

    def attach_store(self, store: "DecisionStore", decision_key: str, operation_id: Optional[str] = None) -> dict[str, Any]:
        requested_operation = operation_id or self.operation_id or self.session_id
        if self.store is None:
            if self.invocations or self.events or self.sealed or self.trusted_ingestion_drain_frontier is not None:
                raise StageBError("LATE_ACQUISITION_ATTACHMENT")
            if not self.verification_contract_id:
                raise StageBError("VERIFICATION_CONTRACT_REQUIRED")
            info = store.register_acquisition(decision_key, self.subject_digest, requested_operation, self.provider_realization_id, self.provider_capability_profile_id, self.verification_contract_id)
            self.store, self.decision_key, self.operation_id = store, decision_key, requested_operation
            self.acquisition_id, self.acquisition_epoch = info["acquisition_id"], info["epoch"]
            return info
        if self.decision_key != decision_key or self.operation_id != requested_operation:
            raise StageBError("ACQUISITION_REBIND_MISMATCH")
        if not self.acquisition_id or self.acquisition_epoch is None:
            raise StageBError("ACQUISITION_NOT_DURABLY_REGISTERED")
        store.validate_registered_acquisition(
            acquisition_id=self.acquisition_id,
            decision_key=decision_key,
            operation_id=requested_operation,
            subject_digest=self.subject_digest,
            provider_realization_id=self.provider_realization_id,
            provider_profile_id=self.provider_capability_profile_id,
            epoch=self.acquisition_epoch,
            verification_contract_id=self.verification_contract_id,
        )
        self.store = store
        return {"acquisition_id": self.acquisition_id, "epoch": self.acquisition_epoch, "operation_id": requested_operation}

    def register_invocation(self, invocation_id: str, check_id: str, operation: Any = None, input_bindings: Optional[Iterable[Any]] = None, required: bool = True) -> None:
        if self.sealed:
            raise StageBError("INVOCATION_AFTER_SEAL")
        record = {"invocation_id": invocation_id, "check_id": check_id, "operation": operation, "input_bindings": tuple(input_bindings or ()), "required": required}
        prior = self.invocations.get(invocation_id)
        if prior is not None and _canonical_bytes(prior) != _canonical_bytes(record):
            raise StageBError("INVOCATION_ID_CONFLICT")
        if self.store is not None:
            self.store.register_invocation(self.acquisition_id, record)
        self.invocations[invocation_id] = record

    def activate_invocation(self, invocation_id: str) -> None:
        if invocation_id not in self.invocations:
            raise StageBError("INVOCATION_NOT_REGISTERED")
        if self.sealed:
            raise StageBError("INVOCATION_AFTER_SEAL")
        if self.store is not None:
            self.store.activate_invocation(self.acquisition_id, invocation_id)
        self.invocations[invocation_id]["state"] = "ACTIVE"

    def ingest_event(self, event: Any) -> str:
        item = ProviderEvent.from_value(event)
        record = self.invocations.get(item.invocation_id)
        if record is None:
            raise StageBError("EVENT_FOR_UNADMITTED_INVOCATION")
        prior = self.events.get(item.event_id)
        if prior is not None:
            if _canonical_bytes(prior) != _canonical_bytes(item):
                raise StageBError("EVENT_ID_CONFLICT")
            return "IDEMPOTENT"
        if item.sequence <= 0:
            raise StageBError("EVENT_SEQUENCE_INVALID")
        if self.sealed and record.get("state", "REGISTERED") != "ACTIVE":
            raise StageBError("SEALED_EVENT_FOR_NON_INFLIGHT_INVOCATION")
        if item.executed_terminal and record.get("state", "REGISTERED") == "REGISTERED":
            raise StageBError("INVOCATION_NOT_ACTIVE")
        if self.sealed and self.final_provider_event_frontier is not None and item.sequence > self.final_provider_event_frontier:
            raise StageBError("EVENT_AFTER_SEALED_FRONTIER")
        if item.check_id != record["check_id"] or (record.get("operation") is not None and item.operation != record["operation"]):
            raise StageBError("EVENT_INVOCATION_IDENTITY_MISMATCH")
        if any(existing.sequence == item.sequence and existing.event_id != item.event_id for existing in self.events.values()):
            raise StageBError("EVENT_SEQUENCE_CONFLICT")
        if self.store is not None:
            self.store.persist_event(self.acquisition_id, item)
            if item.terminal:
                self.store.terminal_invocation(self.acquisition_id, item.invocation_id)
        self.events[item.event_id] = item
        if item.terminal:
            record["state"] = "TERMINAL"
        return "RECORDED"

    record_event = ingest_event

    def seal(self, final_provider_event_frontier: Optional[int] = None) -> None:
        if self.sealed:
            if final_provider_event_frontier != self.final_provider_event_frontier:
                raise StageBError("SEAL_FRONTIER_CONFLICT")
            return
        if not self.capabilities.final_event_frontier:
            raise StageBError("FINAL_EVENT_FRONTIER_UNTRUSTED")
        frontier = max((event.sequence for event in self.events.values()), default=0) if final_provider_event_frontier is None else final_provider_event_frontier
        if type(frontier) is not int or frontier < 0:
            raise StageBError("SEAL_FRONTIER_INVALID")
        if any(event.sequence > frontier for event in self.events.values()):
            raise StageBError("SEAL_FRONTIER_BELOW_EVENT")
        if self.store is not None:
            self.store.seal_acquisition(self.acquisition_id, frontier)
        self.final_provider_event_frontier = frontier
        self.sealed = True

    seal_capture = seal

    def acknowledge_drain(self, frontier: int) -> None:
        if not self.sealed:
            raise StageBError("DRAIN_BEFORE_SEAL")
        if not self.capabilities.drain_acknowledgement:
            raise StageBError("DRAIN_ACK_UNTRUSTED")
        if type(frontier) is not int or self.final_provider_event_frontier is None or frontier < self.final_provider_event_frontier:
            raise StageBError("DRAIN_BELOW_FRONTIER")
        expected = set(range(1, self.final_provider_event_frontier + 1))
        actual = {event.sequence for event in self.events.values()}
        if actual != expected:
            raise StageBError("DRAIN_EVENT_FRONTIER_GAP")
        if self.store is not None:
            self.store.drain_acquisition(self.acquisition_id, frontier)
        self.trusted_ingestion_drain_frontier = frontier

    acknowledge_trusted_drain = acknowledge_drain

    @property
    def drain_complete(self) -> bool:
        return self.sealed and self.final_provider_event_frontier is not None and self.trusted_ingestion_drain_frontier is not None and self.trusted_ingestion_drain_frontier >= self.final_provider_event_frontier

    def reconcile_attempts(self, selected_checks: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
        selected = {str(item.get("id")) for item in selected_checks}
        observed = {event.check_id for event in self.events.values() if event.executed_terminal}
        missing = tuple(sorted(selected - observed))
        invalid = tuple(sorted(event.event_id for event in self.events.values() if event.invocation_id not in self.invocations))
        return {"complete": not missing and not invalid and all(event.status in _EXECUTED_TERMINAL_STATUSES for event in self.events.values()), "missing": missing, "invalid": invalid}


class DecisionStore:
    """File-backed SQLite store whose V11 writer transactions are canonical."""

    SCHEMA_VERSION = "ACV_STAGE_B_STORE_V11"

    def __init__(self, path: str | pathlib.Path) -> None:
        if str(path) == ":memory:":
            raise StoreCapabilityError("IN_MEMORY_STORE_CANNOT_PROVE_WAL_DURABILITY")
        raw = os.fspath(path)
        if raw.startswith(("\\\\", "//")):
            raise StoreCapabilityError("NETWORK_SHARE_STORE_UNSUPPORTED")
        self.path = pathlib.Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.connection = sqlite3.connect(str(self.path), isolation_level=None, timeout=30, check_same_thread=False)
            self._configure()
            self._create_schema()
            self.health_check()
        except StoreCapabilityError:
            if hasattr(self, "connection"):
                self.connection.close()
            raise
        except (OSError, sqlite3.Error) as exc:
            if hasattr(self, "connection"):
                self.connection.close()
            raise StoreCapabilityError("STORE_OPEN_FAILED") from exc

    @staticmethod
    def _single_pragma_value(cursor: Any, error_code: str) -> Any:
        try:
            row = cursor.fetchone()
            if row is None or len(row) != 1:
                raise ValueError("PRAGMA_RESULT_SHAPE_INVALID")
            return row[0]
        except (AttributeError, KeyError, IndexError, TypeError, ValueError, sqlite3.Error) as exc:
            raise StoreCapabilityError(error_code) from exc

    def _read_pragma_value(self, statement: str, error_code: str) -> Any:
        try:
            return self._single_pragma_value(self.connection.execute(statement), error_code)
        except sqlite3.Error as exc:
            raise StoreCapabilityError(error_code) from exc

    def _execute_pragma_setter(self, statement: str, error_code: str) -> None:
        try:
            self.connection.execute(statement)
        except sqlite3.Error as exc:
            raise StoreCapabilityError(error_code) from exc

    def _configure(self) -> None:
        if self.connection.in_transaction:
            raise StoreCapabilityError("STORE_CONFIGURATION_ACTIVE_TRANSACTION")
        journal_setter = self._read_pragma_value("PRAGMA journal_mode=WAL", "STORE_PROFILE_SET_FAILED")
        if type(journal_setter) is not str or journal_setter.lower() != "wal":
            raise StoreCapabilityError("UNSUPPORTED_STORE_PROFILE")
        journal = self._read_pragma_value("PRAGMA journal_mode", "STORE_PROFILE_QUERY_FAILED")
        self._execute_pragma_setter("PRAGMA synchronous=FULL", "STORE_PROFILE_SET_FAILED")
        synchronous = self._read_pragma_value("PRAGMA synchronous", "STORE_PROFILE_QUERY_FAILED")
        self._execute_pragma_setter("PRAGMA foreign_keys=ON", "STORE_PROFILE_SET_FAILED")
        foreign_keys = self._read_pragma_value("PRAGMA foreign_keys", "STORE_PROFILE_QUERY_FAILED")
        if type(journal) is not str or journal.lower() != "wal" or type(synchronous) is not int or synchronous != 2 or type(foreign_keys) is not int or foreign_keys != 1:
            raise StoreCapabilityError("UNSUPPORTED_STORE_PROFILE")
        self.profile = {"python_version": sys.version.split()[0], "sqlite_library_version": sqlite3.sqlite_version, "journal_mode": journal.lower(), "synchronous": synchronous, "foreign_keys": foreign_keys, "support_profile": "local_sqlite_wal_full"}
        self.profile_id = _identity("store-realization", self.profile)

    def _create_schema(self) -> None:
        existing = self.connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='store_meta'").fetchone()
        if existing is not None:
            marker = self.connection.execute("SELECT value FROM store_meta WHERE key='schema_version'").fetchone()
            if marker is None or marker[0] not in ("ACV_STAGE_B_STORE_V9", self.SCHEMA_VERSION):
                raise StoreCapabilityError("LEGACY_STAGE_B_SEMANTICS")
            if marker[0] == "ACV_STAGE_B_STORE_V9":
                acquisition_columns = {row[1] for row in self.connection.execute("PRAGMA table_info(acquisitions)")}
                finalization_columns = {row[1] for row in self.connection.execute("PRAGMA table_info(finalizations)")}
                if "verification_contract_id" not in acquisition_columns:
                    self.connection.execute("ALTER TABLE acquisitions ADD COLUMN verification_contract_id TEXT")
                if "abort_reason" not in acquisition_columns:
                    self.connection.execute("ALTER TABLE acquisitions ADD COLUMN abort_reason TEXT")
                if "verification_contract_id" not in finalization_columns:
                    self.connection.execute("ALTER TABLE finalizations ADD COLUMN verification_contract_id TEXT")
                self.connection.execute("UPDATE store_meta SET value=? WHERE key='schema_version'", (self.SCHEMA_VERSION,))
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS store_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS store_profile (profile_id TEXT PRIMARY KEY, profile_json TEXT NOT NULL, profile_sha256 TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS objects (object_id TEXT PRIMARY KEY, kind TEXT NOT NULL, canonical_payload TEXT NOT NULL, payload_sha256 TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS decision_heads (decision_key TEXT PRIMARY KEY, generation INTEGER NOT NULL, finalization_id TEXT NOT NULL, head_object_id TEXT NOT NULL REFERENCES objects(object_id));
            CREATE TABLE IF NOT EXISTS finalizations (finalization_id TEXT PRIMARY KEY, decision_key TEXT NOT NULL, generation INTEGER NOT NULL, verification_contract_id TEXT, payload_json TEXT NOT NULL, head_object_id TEXT NOT NULL REFERENCES objects(object_id));
            CREATE TABLE IF NOT EXISTS decision_runtime (decision_key TEXT PRIMARY KEY, acquisition_epoch INTEGER NOT NULL, finalized_epoch INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS acquisitions (acquisition_id TEXT PRIMARY KEY, operation_id TEXT NOT NULL UNIQUE, decision_key TEXT NOT NULL, epoch INTEGER NOT NULL, subject_digest TEXT NOT NULL, provider_realization_id TEXT NOT NULL, provider_profile_id TEXT NOT NULL, verification_contract_id TEXT, abort_reason TEXT, state TEXT NOT NULL, frontier INTEGER, drained_through INTEGER);
            CREATE TABLE IF NOT EXISTS invocations (invocation_id TEXT PRIMARY KEY, acquisition_id TEXT NOT NULL REFERENCES acquisitions(acquisition_id), check_id TEXT NOT NULL, state TEXT NOT NULL, record_json TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS provider_events (event_id TEXT PRIMARY KEY, acquisition_id TEXT NOT NULL REFERENCES acquisitions(acquisition_id), invocation_id TEXT NOT NULL, check_id TEXT NOT NULL, sequence INTEGER NOT NULL, object_id TEXT NOT NULL REFERENCES objects(object_id), canonical_json TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS operation_publications (operation_id TEXT PRIMARY KEY, acquisition_id TEXT NOT NULL REFERENCES acquisitions(acquisition_id), finalization_id TEXT, state TEXT NOT NULL, result_digest TEXT);
            CREATE UNIQUE INDEX IF NOT EXISTS provider_event_sequence ON provider_events(acquisition_id, sequence);
            """
        )
        self.connection.execute("INSERT OR REPLACE INTO store_meta(key,value) VALUES ('schema_version',?)", (self.SCHEMA_VERSION,))
        self.connection.execute("INSERT OR IGNORE INTO store_profile(profile_id,profile_json,profile_sha256) VALUES (?,?,?)", (self.profile_id, _canonical_bytes(self.profile).decode("utf-8"), _sha256(_canonical_bytes(self.profile))))

    def health_check(self) -> str:
        result = self._read_pragma_value("PRAGMA integrity_check", "STORE_HEALTH_CHECK_FAILED")
        if type(result) is not str or result != "ok":
            raise StoreCapabilityError("STORE_CORRUPT")
        return result

    @staticmethod
    def _rowcount(cursor: Any, code: str) -> None:
        if cursor.rowcount != 1:
            raise StageBError(code)

    def current_head(self, decision_key: str) -> Optional[dict[str, Any]]:
        row = self.connection.execute("SELECT generation,finalization_id,head_object_id FROM decision_heads WHERE decision_key=?", (decision_key,)).fetchone()
        return None if row is None else {"generation": int(row[0]), "finalization_id": row[1], "head_object_id": row[2]}

    def runtime_state(self, decision_key: str) -> dict[str, int]:
        row = self.connection.execute("SELECT acquisition_epoch,finalized_epoch FROM decision_runtime WHERE decision_key=?", (decision_key,)).fetchone()
        return {"acquisition_epoch": int(row[0]), "finalized_epoch": int(row[1])} if row else {"acquisition_epoch": 0, "finalized_epoch": 0}

    def put_object(self, kind: str, payload: Any, object_id: Optional[str] = None) -> str:
        canonical = _canonical_bytes(payload).decode("utf-8")
        expected = _identity("object:" + kind, json.loads(canonical))
        identifier = object_id or expected
        if identifier != expected:
            raise ProvenanceError("PROVENANCE_OBJECT_DIGEST_MISMATCH")
        payload_sha = _sha256(canonical.encode("utf-8"))
        row = self.connection.execute("SELECT kind,canonical_payload,payload_sha256 FROM objects WHERE object_id=?", (identifier,)).fetchone()
        if row is not None and tuple(row) != (kind, canonical, payload_sha):
            raise ProvenanceError("PROVENANCE_OBJECT_ID_CONTENT_MISMATCH")
        self.connection.execute("INSERT OR IGNORE INTO objects(object_id,kind,canonical_payload,payload_sha256) VALUES (?,?,?,?)", (identifier, kind, canonical, payload_sha))
        return identifier

    def get_object(self, object_id: str) -> Any:
        row = self.connection.execute("SELECT kind,canonical_payload,payload_sha256 FROM objects WHERE object_id=?", (object_id,)).fetchone()
        if row is None or _sha256(row[1].encode("utf-8")) != row[2] or _identity("object:" + row[0], json.loads(row[1])) != object_id:
            raise ProvenanceError("PROVENANCE_OBJECT_DIGEST_MISMATCH")
        return json.loads(row[1])

    def register_acquisition(self, decision_key: str, subject_digest: str, operation_id: str, provider_realization_id: str, provider_profile_id: str, verification_contract_id: str) -> dict[str, Any]:
        if not isinstance(verification_contract_id, str) or not verification_contract_id:
            raise StageBError("VERIFICATION_CONTRACT_REQUIRED")
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            existing = self.connection.execute("SELECT acquisition_id,epoch,decision_key,subject_digest,provider_realization_id,provider_profile_id,verification_contract_id,state FROM acquisitions WHERE operation_id=?", (operation_id,)).fetchone()
            if existing is not None:
                if tuple(existing[2:7]) != (decision_key, subject_digest, provider_realization_id, provider_profile_id, verification_contract_id):
                    raise StageBError("OPERATION_ID_CONFLICT")
                self.connection.execute("COMMIT")
                return {"acquisition_id": existing[0], "epoch": int(existing[1]), "state": existing[7], "operation_id": operation_id, "verification_contract_id": existing[6]}
            runtime = self.connection.execute("SELECT acquisition_epoch,finalized_epoch FROM decision_runtime WHERE decision_key=?", (decision_key,)).fetchone()
            current, finalized = (int(runtime[0]), int(runtime[1])) if runtime else (0, 0)
            epoch = current if current > finalized else finalized + 1
            conflicts = self.connection.execute("SELECT verification_contract_id FROM acquisitions WHERE decision_key=? AND epoch=? AND state IN ('OPEN','SEALED')", (decision_key, epoch)).fetchall()
            if any(row[0] != verification_contract_id for row in conflicts):
                raise StageBError("EPOCH_VERIFICATION_CONTRACT_CONFLICT")
            acquisition_id = "acq-" + _sha256((operation_id + "\0" + decision_key + "\0" + str(epoch)).encode("utf-8"))[:32]
            self.connection.execute("INSERT INTO acquisitions(acquisition_id,operation_id,decision_key,epoch,subject_digest,provider_realization_id,provider_profile_id,verification_contract_id,abort_reason,state,frontier,drained_through) VALUES (?,?,?,?,?,?,?, ?,NULL,'OPEN',NULL,NULL)", (acquisition_id, operation_id, decision_key, epoch, subject_digest, provider_realization_id, provider_profile_id, verification_contract_id))
            if runtime is None:
                self.connection.execute("INSERT INTO decision_runtime(decision_key,acquisition_epoch,finalized_epoch) VALUES (?,?,?)", (decision_key, epoch, finalized))
            else:
                cursor = self.connection.execute("UPDATE decision_runtime SET acquisition_epoch=? WHERE decision_key=?", (epoch, decision_key))
                self._rowcount(cursor, "RUNTIME_EPOCH_UPDATE_FAILED")
            self.connection.execute("INSERT INTO operation_publications(operation_id,acquisition_id,finalization_id,state,result_digest) VALUES (?, ?, NULL, 'NOT_PUBLISHED', NULL)", (operation_id, acquisition_id))
            self.connection.execute("COMMIT")
            return {"acquisition_id": acquisition_id, "epoch": epoch, "state": "OPEN", "operation_id": operation_id, "verification_contract_id": verification_contract_id}
        except BaseException:
            if self.connection.in_transaction:
                self.connection.execute("ROLLBACK")
            raise

    def validate_registered_acquisition(self, *, acquisition_id: str, decision_key: str, operation_id: str, subject_digest: str, provider_realization_id: str, provider_profile_id: str, epoch: int, verification_contract_id: str) -> dict[str, Any]:
        row = self.connection.execute("SELECT acquisition_id,operation_id,decision_key,epoch,subject_digest,provider_realization_id,provider_profile_id,verification_contract_id,state FROM acquisitions WHERE acquisition_id=?", (acquisition_id,)).fetchone()
        expected = (acquisition_id, operation_id, decision_key, int(epoch), subject_digest, provider_realization_id, provider_profile_id, verification_contract_id)
        if row is None or tuple(row[:8]) != expected:
            raise StageBError("ACQUISITION_NOT_DURABLY_REGISTERED")
        return {"acquisition_id": row[0], "operation_id": row[1], "decision_key": row[2], "epoch": int(row[3]), "state": row[8], "verification_contract_id": row[7]}

    def register_invocation(self, acquisition_id: str, record: Mapping[str, Any]) -> None:
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            acq = self.connection.execute("SELECT state FROM acquisitions WHERE acquisition_id=?", (acquisition_id,)).fetchone()
            if acq is None:
                raise StageBError("ACQUISITION_NOT_FOUND")
            if acq[0] != "OPEN":
                raise StageBError("INVOCATION_AFTER_SEAL")
            canonical = _canonical_bytes(record).decode("utf-8")
            prior = self.connection.execute("SELECT acquisition_id,record_json FROM invocations WHERE invocation_id=?", (record["invocation_id"],)).fetchone()
            if prior is not None:
                if prior[0] != acquisition_id or prior[1] != canonical:
                    raise StageBError("INVOCATION_ID_CONFLICT")
                self.connection.execute("COMMIT")
                return
            self.connection.execute("INSERT INTO invocations(invocation_id,acquisition_id,check_id,state,record_json) VALUES (?,?,?,'REGISTERED',?)", (record["invocation_id"], acquisition_id, record["check_id"], canonical))
            self.connection.execute("COMMIT")
        except BaseException:
            if self.connection.in_transaction:
                self.connection.execute("ROLLBACK")
            raise

    def activate_invocation(self, acquisition_id: str, invocation_id: str) -> None:
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            cursor = self.connection.execute("UPDATE invocations SET state='ACTIVE' WHERE acquisition_id=? AND invocation_id=? AND state='REGISTERED' AND EXISTS (SELECT 1 FROM acquisitions WHERE acquisition_id=? AND state='OPEN')", (acquisition_id, invocation_id, acquisition_id))
            self._rowcount(cursor, "INVOCATION_ACTIVATION_AFTER_SEAL")
            self.connection.execute("COMMIT")
        except BaseException:
            if self.connection.in_transaction:
                self.connection.execute("ROLLBACK")
            raise

    def terminal_invocation(self, acquisition_id: str, invocation_id: str) -> None:
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            prior = self.connection.execute("SELECT state FROM invocations WHERE acquisition_id=? AND invocation_id=?", (acquisition_id, invocation_id)).fetchone()
            if prior is not None and prior[0] == "TERMINAL":
                self.connection.execute("COMMIT")
                return
            cursor = self.connection.execute("UPDATE invocations SET state='TERMINAL' WHERE acquisition_id=? AND invocation_id=? AND state IN ('REGISTERED','ACTIVE')", (acquisition_id, invocation_id))
            self._rowcount(cursor, "INVOCATION_TERMINAL_TRANSITION_INVALID")
            self.connection.execute("COMMIT")
        except BaseException:
            if self.connection.in_transaction:
                self.connection.execute("ROLLBACK")
            raise

    def validate_session_journal(self, session: RunBoundEvidenceSession) -> None:
        """Require the in-memory journal to be exactly the durable journal."""
        inv_rows = self.connection.execute("SELECT invocation_id,record_json FROM invocations WHERE acquisition_id=?", (session.acquisition_id,)).fetchall()
        durable_invocations = {row[0]: row[1] for row in inv_rows}
        if set(durable_invocations) != set(session.invocations):
            raise StageBError("SESSION_JOURNAL_MISMATCH")
        for invocation_id, record in session.invocations.items():
            durable_record = {key: value for key, value in record.items() if key != "state"}
            if durable_invocations[invocation_id] != _canonical_bytes(durable_record).decode("utf-8"):
                raise StageBError("SESSION_JOURNAL_MISMATCH")
        event_rows = self.connection.execute("SELECT event_id,canonical_json FROM provider_events WHERE acquisition_id=?", (session.acquisition_id,)).fetchall()
        durable_events = {row[0]: row[1] for row in event_rows}
        if set(durable_events) != set(session.events):
            raise StageBError("SESSION_JOURNAL_MISMATCH")
        for event_id, event in session.events.items():
            if durable_events[event_id] != _canonical_bytes(event.canonical(session.acquisition_id)).decode("utf-8"):
                raise StageBError("SESSION_JOURNAL_MISMATCH")

    def persist_event(self, acquisition_id: str, event: ProviderEvent) -> str:
        if type(event.sequence) is not int or event.sequence <= 0:
            raise StageBError("EVENT_SEQUENCE_INVALID")
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            canonical = _canonical_bytes(event.canonical(acquisition_id)).decode("utf-8")
            prior = self.connection.execute("SELECT canonical_json FROM provider_events WHERE event_id=?", (event.event_id,)).fetchone()
            if prior is not None:
                if prior[0] != canonical:
                    raise StageBError("EVENT_ID_CONFLICT")
                self.connection.execute("COMMIT")
                return "IDEMPOTENT"
            acq = self.connection.execute("SELECT state,frontier FROM acquisitions WHERE acquisition_id=?", (acquisition_id,)).fetchone()
            inv = self.connection.execute("SELECT state,check_id FROM invocations WHERE invocation_id=? AND acquisition_id=?", (event.invocation_id, acquisition_id)).fetchone()
            if acq is None or inv is None:
                raise StageBError("EVENT_FOR_UNADMITTED_INVOCATION")
            if acq[0] not in ("OPEN", "SEALED"):
                raise StageBError("EVENT_AFTER_TERMINAL_ACQUISITION")
            if acq[0] == "SEALED" and acq[1] is not None and event.sequence > int(acq[1]):
                raise StageBError("EVENT_AFTER_SEALED_FRONTIER")
            if acq[0] == "SEALED" and inv[0] != "ACTIVE":
                raise StageBError("SEALED_EVENT_FOR_NON_INFLIGHT_INVOCATION")
            if event.executed_terminal and inv[0] == "REGISTERED":
                raise StageBError("INVOCATION_NOT_ACTIVE")
            if event.check_id != inv[1]:
                raise StageBError("EVENT_INVOCATION_IDENTITY_MISMATCH")
            sequence_prior = self.connection.execute("SELECT event_id FROM provider_events WHERE acquisition_id=? AND sequence=?", (acquisition_id, event.sequence)).fetchone()
            if sequence_prior is not None and sequence_prior[0] != event.event_id:
                raise StageBError("EVENT_SEQUENCE_CONFLICT")
            oid = self.put_object("PROVIDER_EVENT_RECORD", event.canonical(acquisition_id))
            self.connection.execute("INSERT INTO provider_events(event_id,acquisition_id,invocation_id,check_id,sequence,object_id,canonical_json) VALUES (?,?,?,?,?,?,?)", (event.event_id, acquisition_id, event.invocation_id, event.check_id, event.sequence, oid, canonical))
            self.connection.execute("COMMIT")
            return "RECORDED"
        except BaseException:
            if self.connection.in_transaction:
                self.connection.execute("ROLLBACK")
            raise

    def seal_acquisition(self, acquisition_id: str, frontier: int) -> None:
        if type(frontier) is not int or frontier < 0:
            raise StageBError("SEAL_FRONTIER_INVALID")
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            max_row = self.connection.execute("SELECT MAX(sequence) FROM provider_events WHERE acquisition_id=?", (acquisition_id,)).fetchone()
            if max_row is not None and max_row[0] is not None and int(max_row[0]) > frontier:
                raise StageBError("SEAL_FRONTIER_BELOW_EVENT")
            cursor = self.connection.execute("UPDATE acquisitions SET state='SEALED',frontier=? WHERE acquisition_id=? AND state='OPEN'", (int(frontier), acquisition_id))
            self._rowcount(cursor, "ACQUISITION_SEAL_INVALID")
            self.connection.execute("COMMIT")
        except BaseException:
            if self.connection.in_transaction:
                self.connection.execute("ROLLBACK")
            raise

    def drain_acquisition(self, acquisition_id: str, frontier: int) -> None:
        if type(frontier) is not int or frontier < 0:
            raise StageBError("DRAIN_FRONTIER_INVALID")
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            row = self.connection.execute("SELECT state,frontier FROM acquisitions WHERE acquisition_id=?", (acquisition_id,)).fetchone()
            if row is None or row[0] != "SEALED":
                raise StageBError("ACQUISITION_DRAIN_INVALID")
            if row[1] is None or int(frontier) != int(row[1]):
                raise StageBError("DRAIN_FRONTIER_CONFLICT")
            count, minimum, maximum = self.connection.execute("SELECT COUNT(*),MIN(sequence),MAX(sequence) FROM provider_events WHERE acquisition_id=?", (acquisition_id,)).fetchone()
            if int(count) != int(frontier) or (int(frontier) and (minimum != 1 or maximum != int(frontier))):
                raise StageBError("DRAIN_EVENT_FRONTIER_GAP")
            if self.connection.execute("SELECT 1 FROM invocations WHERE acquisition_id=? AND state NOT IN ('TERMINAL') LIMIT 1", (acquisition_id,)).fetchone() is not None:
                raise StageBError("DRAIN_WITH_NONTERMINAL_INVOCATION")
            cursor = self.connection.execute("UPDATE acquisitions SET state='DRAINED',drained_through=? WHERE acquisition_id=? AND state='SEALED'", (int(frontier), acquisition_id))
            self._rowcount(cursor, "ACQUISITION_DRAIN_INVALID")
            self.connection.execute("COMMIT")
        except BaseException:
            if self.connection.in_transaction:
                self.connection.execute("ROLLBACK")
            raise

    def abort_acquisition(self, acquisition_id: str, reason: str = "RECOVERY_ABORTED") -> None:
        if not isinstance(reason, str) or not reason:
            raise StageBError("ABORT_REASON_INVALID")
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            cursor = self.connection.execute("UPDATE acquisitions SET state='ABORTED',abort_reason=? WHERE acquisition_id=? AND state IN ('OPEN','SEALED')", (reason, acquisition_id))
            self._rowcount(cursor, "ACQUISITION_ABORT_INVALID")
            self.connection.execute("COMMIT")
        except BaseException:
            if self.connection.in_transaction:
                self.connection.execute("ROLLBACK")
            raise

    def has_active_acquisition(self, decision_key: str) -> bool:
        return self.connection.execute("SELECT 1 FROM acquisitions WHERE decision_key=? AND state IN ('OPEN','SEALED') LIMIT 1", (decision_key,)).fetchone() is not None

    def acquisition_blockers(self, acquisition_id: str) -> tuple[str, ...]:
        row = self.connection.execute("SELECT state FROM acquisitions WHERE acquisition_id=?", (acquisition_id,)).fetchone()
        if row is None:
            return ("ACQUISITION_NOT_FOUND",)
        blockers = []
        if row[0] in ("OPEN", "SEALED"):
            blockers.append("ACQUISITION_IN_PROGRESS")
        if row[0] == "ABORTED":
            blockers.append("ACQUISITION_ABORTED_INCOMPLETE")
        if self.connection.execute("SELECT 1 FROM invocations WHERE acquisition_id=? AND state IN ('REGISTERED','ACTIVE') LIMIT 1", (acquisition_id,)).fetchone() is not None:
            blockers.append("TRUSTED_ATTEMPT_OUTCOME_UNKNOWN")
        return tuple(sorted(set(blockers)))

    def _reconcile_epoch_locked(self, decision_key: str, epoch: int, selected_checks: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
        acquisitions = self.connection.execute("SELECT acquisition_id,operation_id,state,subject_digest FROM acquisitions WHERE decision_key=? AND epoch=? ORDER BY acquisition_id", (decision_key, epoch)).fetchall()
        blockers: set[str] = set()
        attempts: list[dict[str, Any]] = []
        if not acquisitions:
            blockers.add("EPOCH_ACQUISITION_MISSING")
        selected = {str(item.get("id")) for item in selected_checks}
        for acquisition_id, operation_id, state, subject_digest in acquisitions:
            observed_checks: set[str] = set()
            if state in ("OPEN", "SEALED"):
                blockers.add("EPOCH_INCOMPLETE")
            if state == "ABORTED":
                blockers.add("ACQUISITION_ABORTED_INCOMPLETE")
            invocations = self.connection.execute("SELECT invocation_id,check_id,state,record_json FROM invocations WHERE acquisition_id=? ORDER BY invocation_id", (acquisition_id,)).fetchall()
            for invocation_id, check_id, inv_state, record_json in invocations:
                events = self.connection.execute("SELECT event_id,sequence,canonical_json FROM provider_events WHERE acquisition_id=? AND invocation_id=? ORDER BY sequence,event_id", (acquisition_id, invocation_id)).fetchall()
                decoded = [json.loads(item[2]) for item in events]
                terminals = [item for item in decoded if item.get("status") in _EXECUTED_TERMINAL_STATUSES]
                if inv_state in ("REGISTERED", "ACTIVE") or not terminals:
                    blockers.add("TRUSTED_ATTEMPT_OUTCOME_UNKNOWN")
                statuses = [item.get("status") for item in terminals]
                if "FAIL" in statuses:
                    blockers.add("TRUSTED_MECHANICAL_FAIL")
                if any(item in {"ERROR", "CANCELLED"} for item in statuses):
                    blockers.add("TRUSTED_TERMINAL_FAILURE")
                if "PASS" in statuses and any(item in {"FAIL", "ERROR", "CANCELLED"} for item in statuses):
                    blockers.add("TRUSTED_ATTEMPT_CONFLICT")
                if terminals:
                    observed_checks.add(str(check_id))
                attempts.append({"acquisition_id": acquisition_id, "operation_id": operation_id, "subject_digest": subject_digest, "invocation_id": invocation_id, "check_id": check_id, "invocation_state": inv_state, "record_json": record_json, "events": decoded})
            blockers.update("MISSING_TERMINAL_ATTEMPT:" + check for check in sorted(selected - observed_checks))
        digest = _identity("attempt-lineage", {"decision_key": decision_key, "epoch": epoch, "attempts": attempts, "blockers": sorted(blockers)})
        return {"complete": not blockers, "blockers": tuple(sorted(blockers)), "attempt_lineage_digest": digest, "acquisitions": acquisitions, "attempts": attempts}

    def reconcile_epoch_attempts(self, decision_key: str, epoch: int, selected_checks: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            result = self._reconcile_epoch_locked(decision_key, epoch, selected_checks)
            self.connection.execute("COMMIT")
            return result
        except BaseException:
            if self.connection.in_transaction:
                self.connection.execute("ROLLBACK")
            raise

    def _persisted_closure_locked(self, bindings: Mapping[str, str], authority: AuthoritySnapshot) -> dict[str, Any]:
        visited: set[str] = set()
        active: set[str] = set()
        producers: set[str] = set()

        def visit(identifier: str) -> None:
            if identifier in active:
                raise ProvenanceError("PROVENANCE_CYCLE")
            if identifier in visited:
                return
            active.add(identifier)
            record = self.get_object(identifier)
            producer = record.get("producer_realization_id")
            if not producer:
                raise ProvenanceError("PRODUCER_REALIZATION_MISSING")
            if not authority.accepts(producer):
                raise ProvenanceError("PROVENANCE_REALIZATION_NOT_ACCEPTED")
            producers.add(str(producer))
            for parent in record.get("direct_input_object_ids", ()):
                visit(str(parent))
            active.remove(identifier)
            visited.add(identifier)

        for root in sorted(bindings):
            visit(bindings[root])
        return {"roots": tuple(sorted(bindings.items())), "objects": tuple(sorted(visited)), "producers": tuple(sorted(producers)), "digest": _identity("decision-trust-closure", {"roots": sorted(bindings.items()), "objects": sorted(visited)}), "complete": True}

    def finalize_epoch(self, decision_key: str, epoch: int, selected_checks: Iterable[Mapping[str, Any]], payload: Mapping[str, Any], expected_generation: int, expected_head: Optional[str], authority: AuthoritySnapshot) -> dict[str, Any]:
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            head = self.connection.execute("SELECT generation,finalization_id,head_object_id FROM decision_heads WHERE decision_key=?", (decision_key,)).fetchone()
            actual_generation, actual_head = (int(head[0]), head[2]) if head else (0, None)
            requested_contract = payload.get("verification_contract_id")
            runtime = self.connection.execute("SELECT acquisition_epoch,finalized_epoch FROM decision_runtime WHERE decision_key=?", (decision_key,)).fetchone()
            runtime_finalized = int(runtime[1]) if runtime else 0
            if head is not None and runtime_finalized >= epoch:
                prior_payload = self.get_finalization(head[1])
                operation_id = payload.get("operation_id")
                member = self.connection.execute("SELECT 1 FROM acquisitions WHERE decision_key=? AND epoch=? AND operation_id=? AND verification_contract_id=?", (decision_key, epoch, operation_id, requested_contract)).fetchone()
                if int(prior_payload.get("acquisition_epoch", -1)) == epoch and requested_contract and prior_payload.get("verification_contract_id") == requested_contract and member is not None:
                    self.connection.execute("COMMIT")
                    return {"finalization_id": head[1], "generation": actual_generation, "head_object_id": head[2], "payload": prior_payload}
            if actual_generation != expected_generation or actual_head != expected_head:
                raise StageBError("STALE_EXPECTED_HEAD")
            current_epoch = int(runtime[0]) if runtime else 0
            if current_epoch != epoch:
                raise StageBError("FINALIZATION_EPOCH_NOT_CURRENT")
            reconciliation = self._reconcile_epoch_locked(decision_key, epoch, selected_checks)
            if not reconciliation["acquisitions"]:
                raise StageBError("EPOCH_ACQUISITION_MISSING")
            if len({row[3] for row in reconciliation["acquisitions"]}) != 1:
                raise StageBError("EPOCH_SUBJECT_MISMATCH")
            contracts = {row[0] for row in self.connection.execute("SELECT DISTINCT verification_contract_id FROM acquisitions WHERE decision_key=? AND epoch=?", (decision_key, epoch)).fetchall()}
            if not requested_contract or contracts != {requested_contract}:
                raise StageBError("EPOCH_VERIFICATION_CONTRACT_CONFLICT")
            if any(row[2] in ("OPEN", "SEALED") for row in reconciliation["acquisitions"]):
                raise StageBError("EPOCH_INCOMPLETE")
            final_payload = dict(payload)
            if head is not None:
                prior_payload = self.get_finalization(head[1])
                final_payload["lineage_blockers"] = sorted(set(final_payload.get("lineage_blockers", ())) | set(prior_payload.get("lineage_blockers", ())))
            closure = self._persisted_closure_locked(final_payload.get("required_fact_root_bindings", {}), authority)
            final_payload["epoch_reconciliation_complete"] = reconciliation["complete"]
            final_payload["attempt_lineage_digest"] = reconciliation["attempt_lineage_digest"]
            final_payload["lineage_blockers"] = sorted(set(final_payload.get("lineage_blockers", ())) | set(reconciliation["blockers"]))
            final_payload["review_readiness"] = ReviewReadiness.NOT_READY_FOR_HUMAN_REVIEW.value if final_payload["lineage_blockers"] else ReviewReadiness.READY_FOR_HUMAN_REVIEW.value
            final_payload["reason_codes"] = list(final_payload["lineage_blockers"])
            final_payload["trust_closure_digest"] = closure["digest"]
            required_trust = set(final_payload.get("required_trust_realization_ids", ())) | set(closure["producers"])
            if not authority.accepts_required(required_trust):
                raise StageBError("TRUST_REALIZATION_NOT_CURRENT")
            final_payload["required_trust_realization_ids"] = sorted(required_trust)
            final_payload["finalization_id"] = str(final_payload.get("finalization_id") or _identity("finalization", final_payload))
            canonical = _canonical_bytes(final_payload).decode("utf-8")
            oid = self.put_object("FINALIZATION_RECORD", final_payload)
            generation = actual_generation + 1
            self.connection.execute("INSERT INTO finalizations(finalization_id,decision_key,generation,verification_contract_id,payload_json,head_object_id) VALUES (?,?,?,?,?,?)", (final_payload["finalization_id"], decision_key, generation, final_payload.get("verification_contract_id"), canonical, oid))
            if head is None:
                self.connection.execute("INSERT INTO decision_heads(decision_key,generation,finalization_id,head_object_id) VALUES (?,?,?,?)", (decision_key, generation, final_payload["finalization_id"], oid))
            else:
                cursor = self.connection.execute("UPDATE decision_heads SET generation=?,finalization_id=?,head_object_id=? WHERE decision_key=?", (generation, final_payload["finalization_id"], oid, decision_key))
                self._rowcount(cursor, "DECISION_HEAD_UPDATE_FAILED")
            if runtime is None:
                self.connection.execute("INSERT INTO decision_runtime(decision_key,acquisition_epoch,finalized_epoch) VALUES (?,?,?)", (decision_key, epoch, epoch))
            else:
                cursor = self.connection.execute("UPDATE decision_runtime SET finalized_epoch=? WHERE decision_key=?", (epoch, decision_key))
                self._rowcount(cursor, "FINALIZED_EPOCH_UPDATE_FAILED")
            for acquisition_id, operation_id, *_ in reconciliation["acquisitions"]:
                cursor = self.connection.execute("UPDATE operation_publications SET finalization_id=?,state='PENDING' WHERE operation_id=? AND acquisition_id=?", (final_payload["finalization_id"], operation_id, acquisition_id))
                self._rowcount(cursor, "OPERATION_PUBLICATION_LINK_FAILED")
            self.connection.execute("COMMIT")
            return {"finalization_id": final_payload["finalization_id"], "generation": generation, "head_object_id": oid, "payload": final_payload}
        except BaseException:
            if self.connection.in_transaction:
                self.connection.execute("ROLLBACK")
            raise

    def commit_finalization(self, decision_key: str, finalization_payload: Mapping[str, Any], expected_generation: int = 0, expected_head: Optional[str] = None) -> dict[str, Any]:
        payload = dict(finalization_payload)
        finalization_id = str(payload.get("finalization_id") or _identity("finalization", payload))
        payload["finalization_id"] = finalization_id
        canonical = _canonical_bytes(payload).decode("utf-8")
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            row = self.connection.execute("SELECT generation,head_object_id FROM decision_heads WHERE decision_key=?", (decision_key,)).fetchone()
            actual_generation, actual_head = (int(row[0]), row[1]) if row else (0, None)
            if actual_generation != expected_generation or actual_head != expected_head:
                raise StageBError("STALE_EXPECTED_HEAD")
            oid = self.put_object("FINALIZATION_RECORD", payload)
            generation = actual_generation + 1
            self.connection.execute("INSERT INTO finalizations(finalization_id,decision_key,generation,verification_contract_id,payload_json,head_object_id) VALUES (?,?,?,?,?,?)", (finalization_id, decision_key, generation, payload.get("verification_contract_id"), canonical, oid))
            if row is None:
                self.connection.execute("INSERT INTO decision_heads VALUES (?,?,?,?)", (decision_key, generation, finalization_id, oid))
            else:
                cursor = self.connection.execute("UPDATE decision_heads SET generation=?,finalization_id=?,head_object_id=? WHERE decision_key=?", (generation, finalization_id, oid, decision_key))
                self._rowcount(cursor, "DECISION_HEAD_UPDATE_FAILED")
            self.connection.execute("COMMIT")
            return {"finalization_id": finalization_id, "generation": generation, "head_object_id": oid}
        except BaseException:
            if self.connection.in_transaction:
                self.connection.execute("ROLLBACK")
            raise

    def get_finalization(self, finalization_id: str) -> dict[str, Any]:
        row = self.connection.execute("SELECT payload_json,head_object_id FROM finalizations WHERE finalization_id=?", (finalization_id,)).fetchone()
        if row is None or self.get_object(row[1]) != json.loads(row[0]):
            raise ProvenanceError("FINALIZATION_OBJECT_MISMATCH")
        return json.loads(row[0])

    def pending_publications_for_decision(self, decision_key: str) -> list[dict[str, Any]]:
        head = self.current_head(decision_key)
        if head is None:
            return []
        rows = self.connection.execute("SELECT operation_id,acquisition_id,finalization_id,state FROM operation_publications WHERE finalization_id=? AND state='PENDING' ORDER BY operation_id", (head["finalization_id"],)).fetchall()
        return [{"operation_id": row[0], "acquisition_id": row[1], "finalization_id": row[2], "state": row[3]} for row in rows]

    def mark_projection_published(self, operation_id: str, result_digest: str) -> None:
        cursor = self.connection.execute("UPDATE operation_publications SET state='PUBLISHED',result_digest=? WHERE operation_id=? AND state='PENDING'", (result_digest, operation_id))
        self._rowcount(cursor, "PROJECTION_PUBLICATION_UPDATE_FAILED")

    def recover_operation(self, operation_id: str) -> Optional[dict[str, Any]]:
        rows = self.connection.execute("SELECT acquisitions.decision_key,operation_publications.finalization_id FROM operation_publications JOIN acquisitions USING(acquisition_id) WHERE operation_publications.operation_id=? AND operation_publications.state='PENDING'", (operation_id,)).fetchall()
        if not rows:
            return None
        head = self.current_head(rows[0][0])
        if head is None or head["finalization_id"] != rows[0][1]:
            return None
        return self.get_finalization(rows[0][1])

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "DecisionStore":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()


def _receipt_value(receipt: Any) -> Mapping[str, Any]:
    if isinstance(receipt, (str, pathlib.Path)):
        return json.loads(pathlib.Path(receipt).read_text(encoding="utf-8"))
    if not isinstance(receipt, Mapping):
        raise StageBError("RECEIPT_INVALID")
    return receipt


@contextlib.contextmanager
def _load_adjacent_validator(module_name: str, path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise StageBError("STAGE_A_VALIDATOR_UNAVAILABLE")
    module = importlib.util.module_from_spec(spec)
    previous = sys.modules.get(module_name, _MODULE_MISSING)
    if previous is not _MODULE_MISSING:
        raise StageBError("STAGE_A_VALIDATOR_MODULE_COLLISION")
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
        yield module
    finally:
        if sys.modules.get(module_name) is module:
            sys.modules.pop(module_name, None)


def _validate_stage_a_receipt(receipt: Mapping[str, Any]) -> None:
    try:
        with _load_adjacent_validator("acv_stage_a_validator_" + _sha256(os.urandom(16))[:16], pathlib.Path(__file__).resolve().parent / "validate_receipt.py") as module:
            module.validate(receipt)
    except Exception as exc:
        raise StageBError("STAGE_A_RECEIPT_NOT_VALID") from exc


def derive_verification_contract_id(validated_receipt: Mapping[str, Any]) -> str:
    """Identify the validated receipt contract separately from its decision key."""
    if not isinstance(validated_receipt, Mapping):
        raise StageBError("RECEIPT_INVALID")
    _validate_stage_a_receipt(validated_receipt)
    return _sha256(b"ACV-VERIFICATION-CONTRACT-v1\0" + _canonical_bytes(validated_receipt))


def derive_decision_key(receipt: Mapping[str, Any]) -> str:
    subject = receipt.get("subject") or {}
    product = receipt.get("product")
    digest = subject.get("subject_digest")
    if not isinstance(product, str) or not isinstance(digest, str) or not digest:
        raise StageBError("DECISION_KEY_INPUT_INVALID")
    return _identity("decision-key", {"product": product, "subject_digest": digest})


def derive_required_fact_roots(receipt: Mapping[str, Any], current_verifier: Any = None) -> tuple[str, ...]:
    if current_verifier is not None:
        roots = current_verifier(receipt) if callable(current_verifier) else _get(current_verifier, "required_fact_roots")
        if roots is not None:
            return tuple(sorted(str(item) for item in roots))
    checks = [check for check in receipt.get("verification_plan", []) if check.get("selected") is True]
    obligations = [item for item in receipt.get("obligations", []) if item.get("material") is True]
    return tuple(sorted([f"check:{item['id']}:{item['check_contract_digest']}" for item in checks] + [f"obligation:{item['id']}" for item in obligations]))


def derive_decision_trust_closure(required_fact_roots: Iterable[str], provenance_graph: Mapping[str, Any], authority_snapshot: Optional[AuthoritySnapshot] = None) -> dict[str, Any]:
    roots = tuple(sorted(str(root) for root in required_fact_roots))
    visited: set[str] = set()
    active: set[str] = set()
    producers: set[str] = set()

    def visit(node_id: str) -> None:
        if node_id in active:
            raise ProvenanceError("PROVENANCE_CYCLE")
        if node_id in visited:
            return
        record = provenance_graph.get(node_id)
        if record is None:
            raise ProvenanceError("PROVENANCE_PARENT_MISSING")
        active.add(node_id)
        producer = _get(record, "producer_realization_id")
        if not producer:
            raise ProvenanceError("PRODUCER_REALIZATION_MISSING")
        if authority_snapshot is not None and not authority_snapshot.accepts(str(producer)):
            raise ProvenanceError("PROVENANCE_REALIZATION_NOT_ACCEPTED")
        producers.add(str(producer))
        for parent in (_get(record, "direct_input_object_ids") or _get(record, "parents") or ()):
            visit(str(parent))
        active.remove(node_id)
        visited.add(node_id)

    for root in roots:
        visit(root)
    return {"roots": roots, "objects": tuple(sorted(visited)), "producers": tuple(sorted(producers)), "digest": _identity("decision-trust-closure", {"roots": roots, "objects": sorted(visited)}), "complete": True}


def evaluate_evidence_reuse(*_args: Any, **_kwargs: Any) -> str:
    return "REVERIFY_REQUIRED"


def _persist_graph(store: DecisionStore, graph: Mapping[str, Any]) -> dict[str, str]:
    cache: dict[str, str] = {}
    active: set[str] = set()

    def persist(key: str) -> str:
        if key in cache:
            return cache[key]
        if key in active:
            raise ProvenanceError("PROVENANCE_CYCLE")
        record = graph.get(key)
        if record is None:
            raise ProvenanceError("PROVENANCE_PARENT_MISSING")
        active.add(key)
        payload = _plain(record)
        parents = list(payload.get("direct_input_object_ids") or payload.get("parents") or ())
        payload["direct_input_object_ids"] = [persist(str(parent)) if str(parent) in graph else str(parent) for parent in parents]
        payload.pop("object_id", None)
        kind = str(payload.get("kind") or ("TRANSFORM_RECORD" if "transform_mode" in payload else "CHECK_RESULT_FACT"))
        identifier = store.put_object(kind, payload)
        active.remove(key)
        cache[key] = identifier
        return identifier

    return {str(key): persist(str(key)) for key in graph}


_CORE_ROLES = {
    "scripts/finalize_verification.py": "stage_b_authority",
    "scripts/validate_receipt.py": "stage_a_decision_validator",
    "scripts/change_snapshot.py": "subject_snapshot",
    "schemas/verification-receipt.schema.json": "receipt_schema",
    "references/stage-b-live-verification.md": "normative_stage_b_contract",
}


def _safe_relative(path: str) -> bool:
    return bool(path) and not pathlib.PurePosixPath(path).is_absolute() and ".." not in pathlib.PurePosixPath(path).parts and "\\" not in path


def load_core_realization_manifest(skill_root: Optional[pathlib.Path] = None) -> tuple[Mapping[str, Any], bytes]:
    root = pathlib.Path(skill_root or pathlib.Path(__file__).resolve().parents[1]).resolve()
    path = root / "references" / "stage-b-core-realization.json"
    try:
        raw = path.read_bytes()
        manifest = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ProvenanceError("CORE_REALIZATION_MANIFEST_INVALID") from exc
    if manifest.get("format") != "ACV_STAGE_B_CORE_REALIZATION_MANIFEST_V1" or manifest.get("domain") != "ACV-STAGE-B-CORE-REALIZATION-v1" or not isinstance(manifest.get("files"), list) or len(manifest["files"]) != len(_CORE_ROLES):
        raise ProvenanceError("CORE_REALIZATION_MANIFEST_SHAPE_INVALID")
    seen: set[str] = set()
    for item in manifest["files"]:
        if not isinstance(item, Mapping) or set(item) != {"path", "role"} or not isinstance(item["path"], str) or not isinstance(item["role"], str) or not _safe_relative(item["path"]):
            raise ProvenanceError("CORE_REALIZATION_MANIFEST_PATH_INVALID")
        if item["path"] in seen or item["path"] not in _CORE_ROLES or item["role"] != _CORE_ROLES[item["path"]]:
            raise ProvenanceError("CORE_REALIZATION_MANIFEST_ROLE_INVALID")
        seen.add(item["path"])
        target = root / pathlib.PurePosixPath(item["path"])
        if not target.is_file() or target.resolve() != target:
            raise ProvenanceError("CORE_REALIZATION_FILE_INVALID")
    return manifest, raw


def derive_core_realization_id(skill_root: Optional[pathlib.Path] = None) -> str:
    root = pathlib.Path(skill_root or pathlib.Path(__file__).resolve().parents[1]).resolve()
    manifest, raw = load_core_realization_manifest(root)
    records = [{"path": item["path"], "role": item["role"], "sha256": _sha256((root / pathlib.PurePosixPath(item["path"])).read_bytes())} for item in manifest["files"]]
    return _sha256(b"ACV-STAGE-B-CORE-REALIZATION-v1\0" + raw + b"\0" + _canonical_bytes(records))


derive_decision_protocol_realization_id = derive_core_realization_id


def derive_policy_snapshot_id(policy_snapshot: Any) -> str:
    return _sha256(b"ACV-STAGE-B-POLICY-SNAPSHOT-v1\0" + _canonical_bytes(policy_snapshot or {}))


def _blocked(decision_key: str, status: str, readiness: str, *reasons: str) -> FinalizationResult:
    return FinalizationResult(decision_key, status, readiness, reason_codes=tuple(reasons), limitations=tuple(reasons))


def finalize_verification(receipt: Mapping[str, Any] | str | pathlib.Path, session: Optional[RunBoundEvidenceSession], decision_store: DecisionStore, authority_snapshot: Optional[AuthoritySnapshot], policy_snapshot: Optional[Any] = None, decision_protocol_realization_id: str = "", provenance_graph: Optional[Mapping[str, Any]] = None, current_context: Optional[Mapping[str, Any]] = None, expected_generation: int = 0, expected_head: Optional[str] = None, current_verifier: Any = None) -> FinalizationResult:
    del current_context
    value = _receipt_value(receipt)
    decision_key = derive_decision_key(value)
    _validate_stage_a_receipt(value)
    verification_contract_id = derive_verification_contract_id(value)
    if value.get("readiness", {}).get("state") != ReviewReadiness.READY_FOR_HUMAN_REVIEW.value:
        return _blocked(decision_key, VerificationRunStatus.INCOMPLETE.value, ReviewReadiness.BLOCKED_ON_MISSING_EVIDENCE.value, "STAGE_A_NOT_READY")
    if session is None or authority_snapshot is None:
        return _blocked(decision_key, VerificationRunStatus.INCOMPLETE.value, ReviewReadiness.BLOCKED_ON_MISSING_EVIDENCE.value, "TRUSTED_CAPTURE_OR_AUTHORITY_MISSING")
    subject = value["subject"]["subject_digest"]
    if session.subject_digest != subject or authority_snapshot.subject_digest != subject:
        return _blocked(decision_key, VerificationRunStatus.INVALID.value, ReviewReadiness.NOT_READY_FOR_HUMAN_REVIEW.value, "SUBJECT_MISMATCH")
    if session.verification_contract_id != verification_contract_id:
        return _blocked(decision_key, VerificationRunStatus.INVALID.value, ReviewReadiness.NOT_READY_FOR_HUMAN_REVIEW.value, "VERIFICATION_CONTRACT_MISMATCH")
    if not session.capabilities.qualifying:
        return _blocked(decision_key, VerificationRunStatus.INCOMPLETE.value, ReviewReadiness.BLOCKED_ON_MISSING_EVIDENCE.value, "PROVIDER_CAPABILITY_NOT_QUALIFYING")
    selected = [check for check in value.get("verification_plan", []) if check.get("selected") is True]
    if not session.reconcile_attempts(selected)["complete"]:
        return _blocked(decision_key, VerificationRunStatus.INCOMPLETE.value, ReviewReadiness.BLOCKED_ON_MISSING_EVIDENCE.value, "ATTEMPT_RECONCILIATION_INCOMPLETE")
    if not session.drain_complete:
        return _blocked(decision_key, VerificationRunStatus.INCOMPLETE.value, ReviewReadiness.BLOCKED_ON_MISSING_EVIDENCE.value, "ACQUISITION_DRAIN_INCOMPLETE")
    if not all(bool(item["input_bindings"]) for item in session.invocations.values()):
        return _blocked(decision_key, VerificationRunStatus.INCOMPLETE.value, ReviewReadiness.BLOCKED_ON_MISSING_EVIDENCE.value, "INVOCATION_INPUT_BINDING_MISSING")
    if not decision_protocol_realization_id:
        return _blocked(decision_key, VerificationRunStatus.INCOMPLETE.value, ReviewReadiness.NOT_READY_FOR_HUMAN_REVIEW.value, "DECISION_PROTOCOL_REALIZATION_MISSING")
    if not authority_snapshot.policy_snapshot_id or policy_snapshot is None:
        return _blocked(decision_key, VerificationRunStatus.INCOMPLETE.value, ReviewReadiness.NOT_READY_FOR_HUMAN_REVIEW.value, "CURRENT_POLICY_IDENTITY_MISSING")
    policy_id = derive_policy_snapshot_id(policy_snapshot)
    if policy_id != authority_snapshot.policy_snapshot_id:
        return _blocked(decision_key, VerificationRunStatus.INCOMPLETE.value, ReviewReadiness.NOT_READY_FOR_HUMAN_REVIEW.value, "POLICY_SNAPSHOT_MISMATCH")
    if not authority_snapshot.accepts(session.provider_realization_id):
        return _blocked(decision_key, VerificationRunStatus.INCOMPLETE.value, ReviewReadiness.NOT_READY_FOR_HUMAN_REVIEW.value, "AUTHORITY_NOT_ACCEPTED")
    if not session.acquisition_id or session.acquisition_epoch is None or not session.operation_id:
        return _blocked(decision_key, VerificationRunStatus.INCOMPLETE.value, ReviewReadiness.NOT_READY_FOR_HUMAN_REVIEW.value, "ACQUISITION_NOT_DURABLY_REGISTERED")
    try:
        decision_store.validate_registered_acquisition(
            acquisition_id=session.acquisition_id,
            decision_key=decision_key,
            operation_id=session.operation_id,
            subject_digest=session.subject_digest,
            provider_realization_id=session.provider_realization_id,
            provider_profile_id=session.provider_capability_profile_id,
            epoch=session.acquisition_epoch,
            verification_contract_id=verification_contract_id,
        )
    except StageBError as exc:
        return _blocked(decision_key, VerificationRunStatus.INCOMPLETE.value, ReviewReadiness.NOT_READY_FOR_HUMAN_REVIEW.value, str(exc))
    try:
        decision_store.validate_session_journal(session)
    except StageBError as exc:
        return _blocked(decision_key, VerificationRunStatus.INVALID.value, ReviewReadiness.NOT_READY_FOR_HUMAN_REVIEW.value, str(exc))
    roots = derive_required_fact_roots(value, current_verifier)
    graph = provenance_graph or {}
    try:
        root_ids = _persist_graph(decision_store, graph)
        bindings = {root: root_ids[root] for root in roots}
        closure = derive_decision_trust_closure(roots, graph, authority_snapshot)
    except (KeyError, ProvenanceError) as exc:
        return _blocked(decision_key, VerificationRunStatus.INVALID.value, ReviewReadiness.NOT_READY_FOR_HUMAN_REVIEW.value, str(exc))
    operation_id = session.operation_id or session.session_id
    payload = {
        "decision_key": decision_key, "subject_digest": subject, "subject_snapshot_id": session.post_execution_snapshot_id or subject,
        "operation_id": operation_id, "acquisition_epoch": session.acquisition_epoch,
        "verification_contract_id": verification_contract_id,
        "provider_realization_ids": [session.provider_realization_id], "provider_capability_profile_ids": [session.provider_capability_profile_id],
        "policy_snapshot_id": policy_id, "authority_snapshot_id": authority_snapshot.snapshot_id,
        "required_fact_roots": list(roots), "required_fact_root_bindings": bindings, "trust_closure_digest": closure["digest"],
        "required_trust_realization_ids": [session.provider_realization_id, decision_protocol_realization_id, decision_store.profile_id],
        "store_profile_id": decision_store.profile_id, "decision_protocol_realization_id": decision_protocol_realization_id,
        "verification_run_status": VerificationRunStatus.COMPLETE.value, "review_readiness": ReviewReadiness.READY_FOR_HUMAN_REVIEW.value,
        "reason_codes": [], "lineage_blockers": [], "limitations": ["currentness is point-in-time and requires fresh post-finalization observation"],
    }
    try:
        committed = decision_store.finalize_epoch(decision_key, int(session.acquisition_epoch), selected, payload, expected_generation, expected_head, authority_snapshot)
    except StageBError as exc:
        if str(exc) == "STALE_EXPECTED_HEAD":
            raise
        return _blocked(decision_key, VerificationRunStatus.INCOMPLETE.value, ReviewReadiness.NOT_READY_FOR_HUMAN_REVIEW.value, str(exc))
    final_payload = committed["payload"]
    blockers = tuple(final_payload.get("lineage_blockers", ()))
    readiness = final_payload["review_readiness"]
    return FinalizationResult(decision_key, VerificationRunStatus.COMPLETE.value, readiness, committed["finalization_id"], committed["generation"], roots, final_payload["trust_closure_digest"], decision_protocol_realization_id, tuple(final_payload.get("limitations", ())), blockers, True, subject, policy_id, authority_snapshot.snapshot_id, (session.provider_realization_id,), (session.provider_capability_profile_id,), decision_store.profile_id, session.acquisition_epoch, operation_id, final_payload["attempt_lineage_digest"], blockers, session.post_execution_snapshot_id or subject, verification_contract_id)


def _evaluate_current_readiness_unlocked(finalization: Any = None, decision_store: Optional[DecisionStore] = None, current_subject_digest: str = "", authority_snapshot: Optional[AuthoritySnapshot] = None, current_decision_protocol_realization_id: str = "", current_provider_realization_id: Optional[str] = None, *, decision_key: Optional[str] = None, current_authority_snapshot: Optional[AuthoritySnapshot] = None, current_policy_snapshot_id: Optional[str] = None, expected_finalization_id: Optional[str] = None, current_subject_snapshot_id: Optional[str] = None) -> CurrentReadinessResult:
    store = decision_store
    authority = current_authority_snapshot or authority_snapshot
    key = decision_key or _get(finalization, "decision_key")
    requested = expected_finalization_id or _get(finalization, "finalization_id")
    if store is None or not key:
        return CurrentReadinessResult("REVERIFY_REQUIRED", ("CURRENTNESS_CONTEXT_MISSING",), requested, False)
    head = store.current_head(key)
    if head is None or (requested and requested != head["finalization_id"]):
        return CurrentReadinessResult("NOT_CURRENT_READY", ("CANONICAL_HEAD_CHANGED",), requested, False, head["generation"] if head else None)
    runtime = store.runtime_state(key)
    if store.has_active_acquisition(key):
        return CurrentReadinessResult("NOT_CURRENT_READY", ("ACQUISITION_IN_PROGRESS",), head["finalization_id"], False, head["generation"], runtime["acquisition_epoch"])
    if runtime["acquisition_epoch"] > runtime["finalized_epoch"]:
        return CurrentReadinessResult("NOT_CURRENT_READY", ("NEW_ACQUISITION_REQUIRES_FRESH_FINALIZATION",), head["finalization_id"], False, head["generation"], runtime["acquisition_epoch"])
    try:
        canonical = store.get_finalization(head["finalization_id"])
    except ProvenanceError:
        return CurrentReadinessResult("REVERIFY_REQUIRED", ("LEGACY_PROVENANCE_INCOMPLETE",), head["finalization_id"], False, head["generation"])
    required = ("subject_digest", "subject_snapshot_id", "policy_snapshot_id", "authority_snapshot_id", "decision_protocol_realization_id", "store_profile_id", "verification_contract_id", "required_fact_root_bindings", "trust_closure_digest", "required_trust_realization_ids", "attempt_lineage_digest", "lineage_blockers", "review_readiness")
    if any(field not in canonical for field in required):
        return CurrentReadinessResult("REVERIFY_REQUIRED", ("LEGACY_STAGE_B_SEMANTICS",), head["finalization_id"], False, head["generation"])
    if current_subject_digest != canonical["subject_digest"]:
        return CurrentReadinessResult("NOT_CURRENT_READY", ("CURRENT_SUBJECT_CHANGED",), head["finalization_id"], False, head["generation"], runtime["acquisition_epoch"], canonical["subject_snapshot_id"])
    observed_snapshot = current_subject_snapshot_id or current_subject_digest
    if observed_snapshot != canonical["subject_snapshot_id"]:
        return CurrentReadinessResult("NOT_CURRENT_READY", ("CURRENT_SUBJECT_CHANGED",), head["finalization_id"], False, head["generation"], runtime["acquisition_epoch"], observed_snapshot)
    if authority is None or authority.subject_digest != current_subject_digest or not authority.complete or not authority.topology_complete or authority.self_authorized:
        return CurrentReadinessResult("REVERIFY_REQUIRED", ("CURRENT_AUTHORITY_NOT_ACCEPTED",), head["finalization_id"], False, head["generation"])
    if not authority.policy_snapshot_id:
        return CurrentReadinessResult("REVERIFY_REQUIRED", ("CURRENT_POLICY_IDENTITY_MISSING",), head["finalization_id"], False, head["generation"])
    if canonical["policy_snapshot_id"] != authority.policy_snapshot_id or (current_policy_snapshot_id is not None and canonical["policy_snapshot_id"] != current_policy_snapshot_id):
        return CurrentReadinessResult("REFINALIZE_REQUIRED", ("POLICY_SNAPSHOT_CHANGED",), head["finalization_id"], False, head["generation"])
    if canonical["decision_protocol_realization_id"] != current_decision_protocol_realization_id:
        return CurrentReadinessResult("REFINALIZE_REQUIRED", ("DECISION_PROTOCOL_CHANGED",), head["finalization_id"], False, head["generation"])
    if canonical["store_profile_id"] != store.profile_id:
        return CurrentReadinessResult("REVERIFY_REQUIRED", ("STORE_PROFILE_CHANGED",), head["finalization_id"], False, head["generation"])
    try:
        closure = store._persisted_closure_locked(canonical["required_fact_root_bindings"], authority)
    except ProvenanceError as exc:
        return CurrentReadinessResult("REVERIFY_REQUIRED", (str(exc),), head["finalization_id"], False, head["generation"])
    if not authority.accepts_required(set(canonical["required_trust_realization_ids"]) | set(closure["producers"])):
        return CurrentReadinessResult("REVERIFY_REQUIRED", ("TRUST_REALIZATION_NOT_CURRENT",), head["finalization_id"], False, head["generation"])
    if closure["digest"] != canonical["trust_closure_digest"]:
        return CurrentReadinessResult("REVERIFY_REQUIRED", ("PROVENANCE_OBJECT_DIGEST_MISMATCH",), head["finalization_id"], False, head["generation"])
    if canonical["lineage_blockers"]:
        blockers = tuple(canonical["lineage_blockers"])
        if any(item in {"TRUSTED_MECHANICAL_FAIL", "TRUSTED_TERMINAL_FAILURE", "TRUSTED_ATTEMPT_CONFLICT"} for item in blockers):
            blockers = tuple(sorted(set(blockers) | {"PRIOR_TRUSTED_FAILURE_UNRESOLVED"}))
        return CurrentReadinessResult("NOT_CURRENT_READY", blockers, head["finalization_id"], False, head["generation"])
    if canonical["review_readiness"] != ReviewReadiness.READY_FOR_HUMAN_REVIEW.value:
        return CurrentReadinessResult("NOT_CURRENT_READY", ("FINALIZATION_NOT_REVIEW_READY",), head["finalization_id"], False, head["generation"])
    return CurrentReadinessResult("CURRENT_READY", (), head["finalization_id"], True, head["generation"], runtime["acquisition_epoch"], observed_snapshot, canonical["policy_snapshot_id"], canonical["authority_snapshot_id"], canonical["decision_protocol_realization_id"], canonical["store_profile_id"], canonical["trust_closure_digest"])


def evaluate_current_readiness(finalization: Any = None, decision_store: Optional[DecisionStore] = None, current_subject_digest: str = "", authority_snapshot: Optional[AuthoritySnapshot] = None, current_decision_protocol_realization_id: str = "", current_provider_realization_id: Optional[str] = None, *, decision_key: Optional[str] = None, current_authority_snapshot: Optional[AuthoritySnapshot] = None, current_policy_snapshot_id: Optional[str] = None, expected_finalization_id: Optional[str] = None, current_subject_snapshot_id: Optional[str] = None) -> CurrentReadinessResult:
    """Evaluate all currentness reads from one explicit deferred SQLite snapshot."""
    if decision_store is None:
        return _evaluate_current_readiness_unlocked(finalization, decision_store, current_subject_digest, authority_snapshot, current_decision_protocol_realization_id, current_provider_realization_id, decision_key=decision_key, current_authority_snapshot=current_authority_snapshot, current_policy_snapshot_id=current_policy_snapshot_id, expected_finalization_id=expected_finalization_id, current_subject_snapshot_id=current_subject_snapshot_id)
    started = False
    try:
        if not decision_store.connection.in_transaction:
            decision_store.connection.execute("BEGIN")
            started = True
        hook = getattr(decision_store, "currentness_hook", None)
        if hook is not None:
            hook()
        result = _evaluate_current_readiness_unlocked(finalization, decision_store, current_subject_digest, authority_snapshot, current_decision_protocol_realization_id, current_provider_realization_id, decision_key=decision_key, current_authority_snapshot=current_authority_snapshot, current_policy_snapshot_id=current_policy_snapshot_id, expected_finalization_id=expected_finalization_id, current_subject_snapshot_id=current_subject_snapshot_id)
        if started:
            decision_store.connection.execute("COMMIT")
        return result
    except BaseException:
        if started and decision_store.connection.in_transaction:
            decision_store.connection.execute("ROLLBACK")
        raise
