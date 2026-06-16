"""Numeric value parsing utilities.

This module converts raw Excel/text values into normalized Python float values
before they are passed to ORM population services.
"""

from __future__ import annotations

import math
import re
import unicodedata
from typing import Any


class NumericValueParser:
    """Parse raw numeric values into normalized Python float values."""

    EMPTY_VALUES = {
        "",
        "-",
        "--",
        "nan",
        "none",
        "null",
        "nat",
        "s/i",
        "sin informacion",
        "sin información",
        "n/a",
        "na",
    }

    @classmethod
    def parse_float(cls, value: Any) -> float | None:
        """Parse a raw value into a float.

        Handles:
        - Excel int/float values.
        - Comma decimal separator.
        - Dot decimal separator.
        - Thousand separators.
        - Values with units like MW, MWh, kW, kWh, GW, GWh.
        - Text values containing one numeric token.

        Returns None if no reliable number can be extracted.
        """
        if value is None:
            return None

        if isinstance(value, float):
            if math.isnan(value):
                return None
            return float(value)

        if isinstance(value, int):
            return float(value)

        text = str(value).strip()

        if not text:
            return None

        if cls._normalize_text(text) in cls.EMPTY_VALUES:
            return None

        text = cls._remove_units(text)

        match = re.search(r"-?\d[\d.,]*", text)
        if not match:
            return None

        number_text = cls._normalize_number_text(match.group(0))

        try:
            return float(number_text)
        except ValueError:
            return None

    @staticmethod
    def _remove_units(value: str) -> str:
        """Remove common power and energy units without altering numbers."""
        text = value.strip()

        units = (
            "MWh",
            "MW",
            "kWh",
            "kW",
            "GWh",
            "GW",
            "mwh",
            "mw",
            "kwh",
            "kw",
            "gwh",
            "gw",
        )

        for unit in units:
            text = text.replace(unit, "")

        return text.strip()

    @staticmethod
    def _normalize_number_text(value: str) -> str:
        """Normalize decimal and thousand separators."""
        text = value.strip()

        # 1.234,56 -> 1234.56
        if "." in text and "," in text:
            if text.rfind(",") > text.rfind("."):
                return text.replace(".", "").replace(",", ".")

            # 1,234.56 -> 1234.56
            return text.replace(",", "")

        # 123,45 -> 123.45
        if "," in text:
            return text.replace(",", ".")

        return text

    @staticmethod
    def _normalize_text(value: str) -> str:
        """Normalize text for empty-value detection."""
        text = str(value or "").strip().lower()
        text = unicodedata.normalize("NFKD", text)
        text = "".join(char for char in text if not unicodedata.combining(char))
        return " ".join(text.split())
