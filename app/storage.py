from __future__ import annotations

from copy import deepcopy
import json
import logging
import os
from pathlib import Path
import tempfile
import threading
from typing import Any, Callable, TypeVar


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("TRADE_PAPER_DATA_DIR", PROJECT_ROOT / "data")).expanduser().resolve()


class StorageError(Exception):
    """Base exception for safe JSON storage failures."""


class StorageNotFoundError(StorageError):
    """Raised when storage is missing and no default was supplied."""


class StorageCorruptionError(StorageError):
    """Raised when neither primary nor backup contains valid JSON."""


class StorageValidationError(StorageError):
    """Raised when stored or submitted data fails validation."""


class DuplicateIdentifierError(StorageValidationError):
    """Raised when a supposedly unique identifier already exists."""


_MISSING = object()
_LOCKS: dict[Path, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()
T = TypeVar("T")
logger = logging.getLogger("trade-paper-ai.storage")


def data_path(filename: str | Path) -> Path:
    candidate = (DATA_DIR / Path(filename)).resolve()
    try:
        candidate.relative_to(DATA_DIR.resolve())
    except ValueError as exc:
        raise StorageValidationError("Data path must remain inside the project data directory.") from exc
    return candidate


def path_lock(path: str | Path) -> threading.RLock:
    resolved = Path(path).resolve()
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(resolved, threading.RLock())


def backup_path(path: str | Path) -> Path:
    primary = Path(path)
    return primary.with_name(f"{primary.stem}.backup{primary.suffix}")


def _legacy_backup_path(path: str | Path) -> Path:
    """Keep recovery compatibility with backups created before Version 2.7."""
    primary = Path(path)
    return primary.with_name(f"{primary.name}.bak")


def _expected_type(default: Any, expected_type: type | tuple[type, ...] | None):
    if expected_type is not None:
        return expected_type
    if default is not _MISSING and isinstance(default, (list, dict)):
        return type(default)
    return None


def _validate_type(value: Any, expected_type: type | tuple[type, ...] | None, path: Path) -> Any:
    if expected_type is not None and not isinstance(value, expected_type):
        expected_name = (
            ", ".join(item.__name__ for item in expected_type)
            if isinstance(expected_type, tuple)
            else expected_type.__name__
        )
        logger.error("Storage validation failed: expected top-level %s", expected_name)
        raise StorageValidationError(
            f"JSON storage has the wrong top-level type; expected {expected_name}."
        )
    return value


def _decode_json_bytes(
    raw: bytes,
    path: Path,
    expected_type: type | tuple[type, ...] | None,
) -> Any:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StorageCorruptionError("JSON storage is malformed.") from exc
    return _validate_type(value, expected_type, path)


def _serialize_json(value: Any, expected_type: type | tuple[type, ...] | None, path: Path) -> bytes:
    _validate_type(value, expected_type, path)
    try:
        text = json.dumps(value, ensure_ascii=False, indent=4, allow_nan=False)
    except (TypeError, ValueError) as exc:
        logger.error("Storage validation failed: value is not valid JSON")
        raise StorageValidationError("Value cannot be serialized as valid JSON.") from exc
    return (text + "\n").encode("utf-8")


def _temporary_pattern(path: Path) -> str:
    return f".{path.name}.*.tmp"


def _temporary_candidates(path: Path) -> list[Path]:
    if not path.parent.exists():
        return []
    return sorted(
        path.parent.glob(_temporary_pattern(path)),
        key=lambda candidate: candidate.stat().st_mtime_ns,
        reverse=True,
    )


def _cleanup_temporaries(path: Path, keep: Path | None = None) -> None:
    for candidate in _temporary_candidates(path):
        if keep is not None and candidate == keep:
            continue
        try:
            candidate.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            # A stale artifact is safer than risking a valid primary file.
            continue


def _fsync_directory(directory: Path) -> None:
    descriptor = None
    try:
        descriptor = os.open(str(directory), os.O_RDONLY)
        os.fsync(descriptor)
    except OSError:
        # Directory fsync is not supported on every platform/filesystem.
        pass
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _write_temporary(path: Path, raw: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as file:
            file.write(raw)
            file.flush()
            os.fsync(file.fileno())
    except Exception:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise
    return temporary


def _replace_with_bytes(path: Path, raw: bytes) -> None:
    temporary = _write_temporary(path, raw)
    try:
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        if temporary.exists():
            try:
                temporary.unlink()
            except OSError:
                pass


def _recover_missing_primary_from_temporary(
    path: Path,
    expected_type: type | tuple[type, ...] | None,
) -> Any | object:
    for candidate in _temporary_candidates(path):
        try:
            raw = candidate.read_bytes()
            value = _decode_json_bytes(raw, candidate, expected_type)
        except (OSError, StorageError):
            continue
        try:
            os.replace(candidate, path)
            _fsync_directory(path.parent)
        except OSError as exc:
            raise StorageError("Could not recover interrupted JSON storage.") from exc
        _cleanup_temporaries(path)
        logger.warning("Recovered interrupted JSON storage from temporary file: %s", path.name)
        return value
    _cleanup_temporaries(path)
    return _MISSING


def _load_json_unlocked(
    path: Path,
    default: Any,
    expected_type: type | tuple[type, ...] | None,
) -> Any:
    inferred_type = _expected_type(default, expected_type)
    if not path.exists():
        recovered = _recover_missing_primary_from_temporary(path, inferred_type)
        if recovered is not _MISSING:
            return deepcopy(recovered)
        if default is _MISSING:
            raise StorageNotFoundError("JSON storage does not exist.")
        return deepcopy(_validate_type(default, inferred_type, path))

    try:
        value = _decode_json_bytes(path.read_bytes(), path, inferred_type)
    except OSError as exc:
        raise StorageError("JSON storage could not be read.") from exc
    except StorageCorruptionError as primary_error:
        backup_raw = None
        backup_value = None
        backup_error = primary_error
        for backup in (backup_path(path), _legacy_backup_path(path)):
            try:
                candidate_raw = backup.read_bytes()
                candidate_value = _decode_json_bytes(candidate_raw, backup, inferred_type)
            except (OSError, StorageError) as exc:
                backup_error = exc
                continue
            backup_raw = candidate_raw
            backup_value = candidate_value
            break
        if backup_raw is None:
            raise StorageCorruptionError(
                "JSON storage is malformed and no valid backup is available."
            ) from backup_error
        try:
            _replace_with_bytes(path, backup_raw)
        except OSError as exc:
            raise StorageError("Valid backup could not be restored.") from exc
        _cleanup_temporaries(path)
        logger.warning("Recovered malformed JSON storage from validated backup: %s", path.name)
        return deepcopy(backup_value)

    _cleanup_temporaries(path)
    return deepcopy(value)


def load_json_strict(
    path: str | Path,
    default: Any = _MISSING,
    expected_type: type | tuple[type, ...] | None = None,
) -> Any:
    primary = Path(path).resolve()
    with path_lock(primary):
        return _load_json_unlocked(primary, default, expected_type)


def _atomic_write_json_unlocked(
    path: Path,
    value: Any,
    expected_type: type | tuple[type, ...] | None,
) -> None:
    raw = _serialize_json(value, expected_type, path)
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.exists():
        try:
            primary_raw = path.read_bytes()
            _decode_json_bytes(primary_raw, path, expected_type)
        except OSError as exc:
            raise StorageError("Existing JSON storage could not be read safely.") from exc
        except StorageError as exc:
            raise StorageCorruptionError(
                "Refusing to overwrite malformed JSON storage."
            ) from exc

        backup = backup_path(path)
        try:
            _replace_with_bytes(backup, primary_raw)
            verified_backup = backup.read_bytes()
            _decode_json_bytes(verified_backup, backup, expected_type)
        except (OSError, StorageError) as exc:
            raise StorageError("A validated backup could not be created.") from exc
        logger.info("Created validated JSON backup: %s", backup.name)

    temporary = None
    try:
        temporary = _write_temporary(path, raw)
        os.replace(temporary, path)
        temporary = None
        _fsync_directory(path.parent)
        logger.info("Saved JSON storage atomically: %s", path.name)
    except StorageError:
        raise
    except Exception as exc:
        raise StorageError("JSON storage could not be written atomically.") from exc
    finally:
        if temporary is not None and temporary.exists():
            try:
                temporary.unlink()
            except OSError:
                pass
        _cleanup_temporaries(path)


def atomic_write_json(
    path: str | Path,
    value: Any,
    expected_type: type | tuple[type, ...] | None = None,
) -> None:
    primary = Path(path).resolve()
    with path_lock(primary):
        _atomic_write_json_unlocked(primary, value, expected_type)


def locked_json_mutation(
    path: str | Path,
    default: Any,
    callback: Callable[[Any], T],
    expected_type: type | tuple[type, ...] | None = None,
) -> T:
    primary = Path(path).resolve()
    inferred_type = _expected_type(default, expected_type)
    with path_lock(primary):
        current = _load_json_unlocked(primary, default, inferred_type)
        working_copy = deepcopy(current)
        callback_result = callback(working_copy)
        _serialize_json(working_copy, inferred_type, primary)
        _atomic_write_json_unlocked(primary, working_copy, inferred_type)
        return callback_result


def ensure_unique_identifier(
    records: list[dict[str, Any]],
    field: str,
    value: Any,
    exclude_value: Any = _MISSING,
) -> None:
    normalized = str(value or "").strip()
    if not normalized:
        raise StorageValidationError(f"{field} is required.")
    for record in records:
        if not isinstance(record, dict):
            continue
        existing = str(record.get(field, "") or "").strip()
        if exclude_value is not _MISSING and existing == str(exclude_value or "").strip():
            continue
        if existing == normalized:
            raise DuplicateIdentifierError(f"{field} already exists.")


def next_identifier(records: list[Any], field: str, prefix: str) -> str:
    highest = 0
    marker = f"{prefix}-"
    for record in records:
        if not isinstance(record, dict):
            continue
        value = str(record.get(field, "") or "").strip()
        if not value.startswith(marker):
            continue
        suffix = value[len(marker):]
        if suffix.isdigit():
            highest = max(highest, int(suffix))
    candidate = f"{prefix}-{highest + 1:03d}"
    ensure_unique_identifier(records, field, candidate)
    return candidate
