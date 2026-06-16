"""Technology resolver for CNE project ingestion.

This service normalizes raw CNE technology labels into the Technology lookup
table used by the ORM.

Rules:
- PMG / PMGD prefixes are not treated as technologies.
- BESS projects are always normalized as "Almacenamiento".
- Unknown technologies are blocked instead of silently inserted.
"""

from __future__ import annotations

from dataclasses import dataclass
import unicodedata

from database.db_orm_model import Technology


@dataclass(frozen=True)
class TechnologyResolution:
    """Normalized technology result."""

    name: str
    group: str


class TechnologyResolver:
    """Resolve raw technology labels into normalized Technology records."""

    STORAGE_TECHNOLOGY = TechnologyResolution(
        name="Almacenamiento",
        group="Storage",
    )

    def resolve(
        self,
        raw_value: str | None,
        project_family: str,
    ) -> TechnologyResolution:
        """Resolve a raw CNE technology value into a normalized technology.

        Args:
            raw_value: Raw technology value from the CNE Excel file.
            project_family: Logical project family: generation, der, or bess.

        Returns:
            TechnologyResolution with normalized name and group.

        Raises:
            ValueError: If the technology cannot be resolved safely.
        """
        family = self._normalize(project_family)

        if family == "bess":
            return self.STORAGE_TECHNOLOGY

        raw_text = str(raw_value or "").strip()

        if not raw_text:
            raise ValueError(
                f"Missing technology value for project_family='{project_family}'."
            )

        normalized = self._normalize(raw_text)

        if self._is_solar_storage(normalized):
            return TechnologyResolution(
                name="Solar Fotovoltaica + Almacenamiento",
                group="Hybrid",
            )

        if self._contains_any(normalized, ("fotovoltaico", "fotovoltaica", "solar")):
            return TechnologyResolution(
                name="Solar Fotovoltaica",
                group="Solar",
            )

        if self._contains_any(normalized, ("eolico", "eolica")):
            return TechnologyResolution(
                name="Eólica",
                group="Wind",
            )

        if self._contains_any(normalized, ("hidro", "hidraulica", "hidroelectrica")):
            return TechnologyResolution(
                name="Hidro Pasada",
                group="Hydro",
            )

        if self._contains_any(normalized, ("diesel",)):
            return TechnologyResolution(
                name="Diésel",
                group="Thermal",
            )

        if self._contains_any(normalized, ("biogas",)):
            return TechnologyResolution(
                name="Biogás",
                group="Bioenergy",
            )

        if self._contains_any(normalized, ("gnl", "glp")):
            return TechnologyResolution(
                name="GNL/GLP",
                group="Thermal",
            )

        if self._contains_any(
            normalized, ("bess", "bateria", "baterias", "almacenamiento")
        ):
            return self.STORAGE_TECHNOLOGY

        raise ValueError(
            "Unknown technology value. "
            f"raw_value='{raw_text}', project_family='{project_family}'."
        )

    def get_or_create(
        self,
        session,
        raw_value: str | None,
        project_family: str,
    ) -> Technology:
        """Resolve and return a Technology ORM record.

        Creates the Technology row if it does not exist yet.
        """
        resolved = self.resolve(
            raw_value=raw_value,
            project_family=project_family,
        )

        technology = (
            session.query(Technology)
            .filter(Technology.TechnologyName == resolved.name)
            .one_or_none()
        )

        if technology:
            if not technology.TechnologyGroup and resolved.group:
                technology.TechnologyGroup = resolved.group
            return technology

        technology = Technology(
            TechnologyName=resolved.name,
            TechnologyGroup=resolved.group,
        )

        session.add(technology)
        session.flush()

        return technology

    @staticmethod
    def _is_solar_storage(normalized: str) -> bool:
        """Return whether the raw value represents solar plus storage."""
        has_solar = any(
            token in normalized for token in ("fotovoltaico", "fotovoltaica", "solar")
        )
        has_storage = any(
            token in normalized
            for token in ("bess", "bateria", "baterias", "almacenamiento")
        )
        return has_solar and has_storage

    @staticmethod
    def _contains_any(value: str, tokens: tuple[str, ...]) -> bool:
        """Return whether any token is contained in a normalized value."""
        return any(token in value for token in tokens)

    @staticmethod
    def _normalize(value: str) -> str:
        """Normalize text for accent-insensitive matching."""
        value = str(value or "").strip().lower()

        value = unicodedata.normalize("NFKD", value)
        value = "".join(char for char in value if not unicodedata.combining(char))

        replacements = {
            "–": "-",
            "—": "-",
            "_": " ",
            "/": " / ",
            "+": " + ",
        }

        for source, target in replacements.items():
            value = value.replace(source, target)

        return " ".join(value.split())


if __name__ == "__main__":
    resolver = TechnologyResolver()

    samples = [
        ("PMG Fotovoltaico", "generation"),
        ("Solar Fotovoltaico + BESS", "generation"),
        ("PMGD Eólico", "der"),
        ("PMGD Hidro - Pasada", "der"),
        ("PMGD Biogas", "der"),
        ("PMGD GNL/GLP", "der"),
        ("BESS", "bess"),
        ("PMGD Fotovoltaico + BESS", "bess"),
    ]

    for raw_value, family in samples:
        result = resolver.resolve(raw_value, family)
        print(raw_value, family, "->", result.name, result.group)
