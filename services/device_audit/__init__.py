"""Read-only AULA F75 Max device and configurator audit service."""

from .audit import collect_audit, recommend_compatibility

__all__ = ["collect_audit", "recommend_compatibility"]
