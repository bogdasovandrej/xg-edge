"""Safe automation primitives for immutable evidence and PAPER challengers."""

from xgedge.automation.archive import (
    ARCHIVE_SCHEMA_VERSION,
    empty_archive,
    update_archive,
    validate_archive,
)

__all__ = [
    "ARCHIVE_SCHEMA_VERSION",
    "empty_archive",
    "update_archive",
    "validate_archive",
]
