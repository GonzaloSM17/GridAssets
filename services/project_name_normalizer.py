"""Project name normalization utilities.

This module applies controlled normalization rules to CNE project names before
they are stored or used by downstream matching processes.

The normalizer improves technical writing consistency without changing the
project meaning.
"""

from __future__ import annotations

import re
from typing import Any


class ProjectNameNormalizer:
    """Normalize CNE project names into a cleaner technical Spanish format."""

    SPANISH_TERM_REPLACEMENTS = {
        "Ampliacion": "Ampliación",
        "ampliacion": "ampliación",
        "Conexion": "Conexión",
        "conexion": "conexión",
        "Linea": "Línea",
        "linea": "línea",
        "Subestacion": "Subestación",
        "subestacion": "subestación",
        "Seccionadora": "Seccionadora",
        "seccionadora": "seccionadora",
        "Seccionamiento": "Seccionamiento",
        "seccionamiento": "seccionamiento",
    }

    @classmethod
    def normalize(cls, value: Any) -> str | None:
        """Return a normalized technical project name."""
        if value is None:
            return None

        text = str(value).strip()

        if not text:
            return None

        text = cls._normalize_line_breaks(text)
        text = cls._collapse_spaces(text)
        text = cls._normalize_substation_abbreviation(text)
        text = cls._normalize_voltage_units(text)
        text = cls._normalize_voltage_multipliers(text)
        text = cls._normalize_separator_dashes(text)
        text = cls._normalize_spanish_terms(text)
        text = cls._collapse_spaces(text)

        return text or None

    @staticmethod
    def _normalize_line_breaks(value: str) -> str:
        """Replace line breaks and tabs with spaces."""
        return re.sub(r"[\r\n\t]+", " ", value)

    @staticmethod
    def _normalize_substation_abbreviation(value: str) -> str:
        """Normalize common substation abbreviations into S/E."""
        text = value

        text = re.sub(r"\bS\s*/\s*E\b", "S/E", text, flags=re.IGNORECASE)
        text = re.sub(r"\bS\s*\.\s*E\s*\.?\b", "S/E", text, flags=re.IGNORECASE)
        text = re.sub(r"\bS\s+/\s+E\b", "S/E", text, flags=re.IGNORECASE)
        text = re.sub(r"\bSE\b", "S/E", text, flags=re.IGNORECASE)

        return text

    @staticmethod
    def _normalize_voltage_units(value: str) -> str:
        """Normalize voltage units such as 220KV, 220kv or 220 k v into 220 kV."""
        text = value

        text = re.sub(
            r"\b(\d+(?:[.,]\d+)?)\s*k\s*v\b",
            r"\1 kV",
            text,
            flags=re.IGNORECASE,
        )

        return text

    @staticmethod
    def _normalize_voltage_multipliers(value: str) -> str:
        """Normalize voltage multipliers such as 2 x 220 kV into 2x220 kV."""
        text = value

        text = re.sub(
            r"\b(\d+)\s*x\s*(\d+(?:[.,]\d+)?)\s*kV\b",
            r"\1x\2 kV",
            text,
            flags=re.IGNORECASE,
        )

        return text

    @staticmethod
    def _normalize_separator_dashes(value: str) -> str:
        """Normalize separator dashes without touching technical codes like TR-01."""
        text = value

        # Normalize only dashes used as separators with spaces around them.
        text = re.sub(r"\s+[–—-]\s+", " – ", text)

        return text

    @classmethod
    def _normalize_spanish_terms(cls, value: str) -> str:
        """Fix accents in common Spanish technical terms."""
        text = value

        for source, target in cls.SPANISH_TERM_REPLACEMENTS.items():
            text = re.sub(rf"\b{source}\b", target, text)

        return text

    @staticmethod
    def _collapse_spaces(value: str) -> str:
        """Collapse repeated whitespace."""
        return re.sub(r"\s+", " ", value).strip()
