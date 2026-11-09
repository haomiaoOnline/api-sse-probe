"""Minimal public report-writer contract for future backends."""
from dataclasses import dataclass
from typing import Any, Dict, Protocol


@dataclass(frozen=True)
class OutputContext:
    """Non-secret run output settings, independent from underlying provider."""
    root: Any
    reports: Dict[str, Any]


class ReportOutput(Protocol):
    def publish(self, document: Dict[str, Any], context: OutputContext) -> dict:
        """Return receipt {type, status, location...}; raise if not saved."""
        ...