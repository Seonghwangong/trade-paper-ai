from __future__ import annotations

import re
from typing import Any, Iterable

from app.storage import DuplicateIdentifierError, StorageValidationError


class DataValidationError(StorageValidationError):
    def __init__(self, field: str, reason: str, correction: str):
        self.field = str(field or "Data")
        self.reason = str(reason or "The submitted value is invalid.")
        self.correction = str(correction or "Review the value and try again.")
        super().__init__(self.reason)


def require_text(field: str, value: Any, correction: str | None = None) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise DataValidationError(
            field,
            f"Please enter {field} before saving.",
            correction or f"Enter {field}, then save again.",
        )
    return normalized


def require_items(items: Iterable[Any], field: str = "Items") -> list[Any]:
    values = list(items or [])
    named = [
        item for item in values
        if (
            isinstance(item, dict) and str(item.get("name", "") or "").strip()
        ) or (not isinstance(item, dict) and str(item or "").strip())
    ]
    if not named:
        raise DataValidationError(field, "Please add at least one item before saving.", "Add an item name, then save again.")
    return named


def require_allowed_value(field: str, value: Any, allowed: Iterable[str]) -> str:
    normalized = require_text(field, value)
    allowed_values = list(allowed)
    if normalized not in allowed_values:
        raise DataValidationError(
            field,
            f"{field} is not an allowed value.",
            f"Choose one of: {', '.join(allowed_values)}.",
        )
    return normalized


def _find_reference(records: Iterable[Any], key: str, value: Any):
    normalized = str(value or "").strip()
    if not normalized:
        return None
    return next(
        (
            record for record in records
            if isinstance(record, dict) and str(record.get(key, "") or "").strip() == normalized
        ),
        None,
    )


def require_existing_reference(
    field: str,
    value: Any,
    records: Iterable[Any],
    key: str,
    *,
    required: bool = False,
):
    normalized = str(value or "").strip()
    if not normalized:
        if required:
            article = "an" if str(field or "").strip().lower().startswith(tuple("aeiou")) else "a"
            raise DataValidationError(field, f"Please select {article} {field} before saving.", f"Select an existing {field}, then save again.")
        return None
    record = _find_reference(records, key, normalized)
    if record is None:
        raise DataValidationError(
            field,
            f"The selected {field} is no longer available.",
            f"Select an existing {field} from the list, then save again.",
        )
    return record


def require_at_least_one_reference(*references: tuple[str, Any, Iterable[Any], str]):
    valid = []
    for field, value, records, key in references:
        if str(value or "").strip():
            valid.append(require_existing_reference(field, value, records, key, required=False))
    if not valid:
        labels = " or ".join(reference[0] for reference in references)
        raise DataValidationError(labels, f"Please select {labels} before saving.", f"Select an existing {labels}, then save again.")
    return valid


def require_consistent_reference(field: str, actual: Any, expected: Any, source: str = "linked record") -> None:
    actual_value = str(actual or "").strip()
    expected_value = str(expected or "").strip()
    if actual_value and expected_value and actual_value != expected_value:
        raise DataValidationError(
            field,
            f"{field} does not match the {source}.",
            f"Use {expected_value} or select a consistent {source}.",
        )


def validate_identifier(field: str, value: Any, prefix: str) -> str:
    normalized = require_text(field, value)
    if not re.fullmatch(rf"{re.escape(prefix)}-\d{{3,}}", normalized):
        raise DataValidationError(
            field,
            f"{field} has an invalid format.",
            f"Use the existing {prefix}-### numbering format.",
        )
    return normalized


def ensure_unique_name_casefold(
    records: Iterable[Any],
    field: str,
    value: Any,
    *,
    exclude_index: int | None = None,
) -> str:
    normalized = require_text(field, value)
    folded = normalized.casefold()
    for index, record in enumerate(records):
        if exclude_index is not None and index == exclude_index:
            continue
        if isinstance(record, dict) and str(record.get(field, "") or "").strip().casefold() == folded:
            raise DuplicateIdentifierError(f"{field} already exists.")
    return normalized
