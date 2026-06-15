# db_populate.py
"""
Database population service for CNE parsed projects.

This module converts parsed CNE Excel data into ORM records. It keeps the
original incremental behavior, but now returns a structured ingestion summary
and supports dry-run execution for preview screens and agent review.

Expected flow:
    ProjectParser -> DatabasePopulator.populate_all(parser, dry_run=True)
    ProjectParser -> DatabasePopulator.populate_all(parser, dry_run=False)
"""

from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime
from typing import Any, Dict, Iterable, Optional, Tuple

from sqlalchemy.orm import sessionmaker

# Import database configuration
from database.db_connection import get_sqlserver_engine, get_connection_string

# Import parser dataclasses
from parsers.db_projects_parse import ProjectParser

# Import ORM models
from database.db_orm_model import (
    ProjectState,
    ProjectEntity,
    Bay,
    DocumentType,
    MilestoneType,
    Source,
    TransmissionProject as ORMTransmission,
    GenerationProject as ORMGeneration,
    DERProject as ORMDER,
    BESSProject as ORMBESS,
    LegalDocument,
    ProjectLegalDocument,
    RelevantDate,
)

PROJECT_TYPE_KEYS = ("transmission", "generation", "der", "bess")


def _empty_project_summary() -> Dict[str, Any]:
    """Return counters for a single project family."""
    return {
        "processed": 0,
        "created": 0,
        "updated": 0,
        "unchanged": 0,
        "errors": 0,
        "legal_documents_created": 0,
        "legal_documents_linked": 0,
        "relevant_dates_created": 0,
        "relevant_dates_updated": 0,
        "field_changes": {},
        "warnings": [],
        "error_details": [],
    }


def _empty_summary(dry_run: bool, extraction_time: datetime) -> Dict[str, Any]:
    """Return the full ingestion summary structure."""
    summary = {
        "dry_run": dry_run,
        "extraction_time": extraction_time.isoformat(timespec="seconds"),
        "database": get_connection_string(),
    }
    for key in PROJECT_TYPE_KEYS:
        summary[key] = _empty_project_summary()
    summary["total"] = _empty_project_summary()
    return summary


class DatabasePopulator:
    """Populate the database from parsed CNE Excel data."""

    def __init__(self, echo: bool = False, verbose: bool = True):
        self.engine = get_sqlserver_engine(echo=echo)
        self.Session = sessionmaker(bind=self.engine)
        self.extraction_time = datetime.now()
        self.verbose = verbose
        self.summary = _empty_summary(False, self.extraction_time)
        self._dry_run = False

        if self.verbose:
            print(f"Using database: {get_connection_string()}\n")

    # ------------------------------------------------------------------
    # Summary helpers
    # ------------------------------------------------------------------

    def _reset_summary(self, dry_run: bool) -> None:
        self.extraction_time = datetime.now()
        self._dry_run = dry_run
        self.summary = _empty_summary(dry_run, self.extraction_time)

    def _increment(self, project_type: str, key: str, amount: int = 1) -> None:
        self.summary[project_type][key] += amount
        self.summary["total"][key] += amount

    def _increment_field_change(self, project_type: str, field_name: str) -> None:
        project_fields = self.summary[project_type]["field_changes"]
        total_fields = self.summary["total"]["field_changes"]
        project_fields[field_name] = project_fields.get(field_name, 0) + 1
        total_fields[field_name] = total_fields.get(field_name, 0) + 1

    def _add_warning(self, project_type: str, message: str) -> None:
        self.summary[project_type]["warnings"].append(message)
        self.summary["total"]["warnings"].append(message)

    def _add_error(
        self, project_type: str, project_name: str, error: Exception
    ) -> None:
        detail = {"project_name": project_name, "error": str(error)}
        self.summary[project_type]["error_details"].append(detail)
        self.summary["total"]["error_details"].append(detail)
        self._increment(project_type, "errors")

    # ------------------------------------------------------------------
    # Data helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _has_value(value: Any) -> bool:
        if value is None:
            return False
        if isinstance(value, str):
            return bool(value.strip())
        return True

    @staticmethod
    def _to_text_or_none(value: Any) -> Optional[str]:
        if value is None:
            return None
        text = str(value).strip()
        return text if text else None

    @staticmethod
    def _to_datetime(value: Any) -> Optional[datetime]:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value
        if isinstance(value, date):
            return datetime.combine(value, datetime.min.time())
        return None

    def _set_if_changed(
        self,
        orm_obj: Any,
        attr_name: str,
        new_value: Any,
        project_type: str,
    ) -> bool:
        """Set an ORM field only when the new value is useful and different."""
        if not self._has_value(new_value):
            return False

        current_value = getattr(orm_obj, attr_name)
        if current_value == new_value:
            return False

        setattr(orm_obj, attr_name, new_value)
        self._increment_field_change(project_type, attr_name)
        return True

    # ------------------------------------------------------------------
    # Lookup and related entities
    # ------------------------------------------------------------------

    def _ensure_lookup(self, session, model_class, value: str):
        """Get or create a lookup entry."""
        if not self._has_value(value):
            return None

        value = str(value).strip()
        columns = list(model_class.__table__.columns.keys())
        name_column = columns[1]

        obj = (
            session.query(model_class)
            .filter(getattr(model_class, name_column) == value)
            .one_or_none()
        )

        if obj:
            return obj

        obj = model_class()
        setattr(obj, name_column, value)
        session.add(obj)
        session.flush()
        return obj

    def _populate_lookups(self, session) -> None:
        """Populate initial lookup tables."""
        for state in ["NonStarted", "UnderConstruction", "OnHold", "InService"]:
            self._ensure_lookup(session, ProjectState, state)
        for doc_type in ["Act", "ActAward", "Resolution", "ResolutionExempt"]:
            self._ensure_lookup(session, DocumentType, doc_type)
        for milestone in [
            "COD_Estimated",
            "COD_Actual",
            "Start_Construction",
            "Commissioning_Estimated",
            "Commissioning_Actual",
        ]:
            self._ensure_lookup(session, MilestoneType, milestone)
        for source in ["CNE", "PGP", "SEO", "User"]:
            self._ensure_lookup(session, Source, source)

    def _add_legal_document(
        self,
        session,
        project_id: int,
        doc_type_name: str,
        doc_value: Any,
        project_type: str,
    ) -> Dict[str, bool]:
        """Create and link a legal document if needed."""
        result = {"document_created": False, "link_created": False}
        if not self._has_value(doc_value):
            return result

        doc_name = str(doc_value).strip()
        if " 00:00:00" in doc_name or "Timestamp" in str(type(doc_value).__name__):
            self._add_warning(
                project_type,
                f"Skipped suspicious legal document value for ProjectID {project_id}: {doc_name}",
            )
            return result

        doc_year = None
        if "/" in doc_name:
            try:
                year_str = doc_name.split("/")[-1].strip()
                doc_year = int(year_str) if year_str.isdigit() else None
            except (ValueError, IndexError):
                doc_year = None

        doc_type = self._ensure_lookup(session, DocumentType, doc_type_name)

        doc = (
            session.query(LegalDocument)
            .filter(
                LegalDocument.DocumentName == doc_name,
                LegalDocument.DocumentTypeID == doc_type.DocumentTypeID,
            )
            .one_or_none()
        )

        if not doc:
            doc = LegalDocument(
                DocumentName=doc_name,
                DocumentYear=doc_year,
                DocumentTypeID=doc_type.DocumentTypeID,
            )
            session.add(doc)
            session.flush()
            result["document_created"] = True
            self._increment(project_type, "legal_documents_created")

        link_exists = (
            session.query(ProjectLegalDocument)
            .filter(
                ProjectLegalDocument.ProjectID == project_id,
                ProjectLegalDocument.DocumentID == doc.DocumentID,
            )
            .one_or_none()
        )

        if not link_exists:
            link = ProjectLegalDocument(ProjectID=project_id, DocumentID=doc.DocumentID)
            session.add(link)
            result["link_created"] = True
            self._increment(project_type, "legal_documents_linked")

        return result

    def _add_relevant_date(
        self,
        session,
        project_id: int,
        milestone_name: str,
        date_value: Any,
        project_type: str,
        source_name: str = "CNE",
    ) -> Dict[str, bool]:
        """Add or update a relevant date with extraction timestamp."""
        result = {"date_created": False, "date_updated": False}
        if not self._has_value(date_value):
            return result

        dt_value = self._to_datetime(date_value)
        if not dt_value:
            self._add_warning(
                project_type,
                f"Skipped invalid relevant date for ProjectID {project_id}: {date_value}",
            )
            return result

        milestone = self._ensure_lookup(session, MilestoneType, milestone_name)
        source = self._ensure_lookup(session, Source, source_name)

        existing = (
            session.query(RelevantDate)
            .filter(
                RelevantDate.ProjectID == project_id,
                RelevantDate.MilestoneTypeID == milestone.MilestoneTypeID,
                RelevantDate.SourceID == source.SourceID,
            )
            .one_or_none()
        )

        if existing:
            if existing.DateValue != dt_value:
                existing.DateValue = dt_value
                existing.ExtractedAt = self.extraction_time
                result["date_updated"] = True
                self._increment(project_type, "relevant_dates_updated")
            return result

        relevant_date = RelevantDate(
            ProjectID=project_id,
            MilestoneTypeID=milestone.MilestoneTypeID,
            SourceID=source.SourceID,
            DateValue=dt_value,
            ExtractedAt=self.extraction_time,
        )
        session.add(relevant_date)
        result["date_created"] = True
        self._increment(project_type, "relevant_dates_created")
        return result

    # ------------------------------------------------------------------
    # Generic population helpers
    # ------------------------------------------------------------------

    def _get_or_create_project(
        self,
        session,
        orm_class,
        project_type: str,
        project_name: str,
        create_kwargs: Dict[str, Any],
    ) -> Tuple[Any, bool]:
        """Return an existing project or create a new ORM object."""
        orm_proj = (
            session.query(orm_class)
            .filter(orm_class.ProjectName == project_name)
            .one_or_none()
        )

        if orm_proj:
            return orm_proj, False

        orm_proj = orm_class(ProjectName=project_name, **create_kwargs)
        session.add(orm_proj)
        self._increment(project_type, "created")
        return orm_proj, True

    def _finish_project_state(
        self,
        project_type: str,
        created: bool,
        changed: bool,
    ) -> None:
        """Update project-level summary counters."""
        if created:
            return
        if changed:
            self._increment(project_type, "updated")
        else:
            self._increment(project_type, "unchanged")

    # ------------------------------------------------------------------
    # Project family population methods
    # ------------------------------------------------------------------

    def populate_transmission(self, session, dataclass_projects: Iterable[Any]) -> None:
        """Convert transmission dataclasses to ORM records."""
        project_type = "transmission"
        for dc_proj in dataclass_projects:
            self._increment(project_type, "processed")
            try:
                entity = (
                    self._ensure_lookup(session, ProjectEntity, dc_proj.project_entity)
                    if dc_proj.project_entity
                    else None
                )

                create_kwargs = {
                    "ProjectEntityID": entity.ProjectEntityID if entity else None,
                    "VoltageLevel": dc_proj.voltage_level,
                    "TotalCapacity": self._to_text_or_none(dc_proj.total_capacity),
                }
                orm_proj, created = self._get_or_create_project(
                    session, ORMTransmission, project_type, dc_proj.name, create_kwargs
                )
                session.flush()

                changed = False
                if not created:
                    changed |= self._set_if_changed(
                        orm_proj,
                        "ProjectEntityID",
                        entity.ProjectEntityID if entity else None,
                        project_type,
                    )
                    changed |= self._set_if_changed(
                        orm_proj, "VoltageLevel", dc_proj.voltage_level, project_type
                    )
                    changed |= self._set_if_changed(
                        orm_proj,
                        "TotalCapacity",
                        self._to_text_or_none(dc_proj.total_capacity),
                        project_type,
                    )

                if dc_proj.act:
                    doc_result = self._add_legal_document(
                        session, orm_proj.ProjectID, "Act", dc_proj.act, project_type
                    )
                    changed |= any(doc_result.values())
                if dc_proj.act_award:
                    doc_result = self._add_legal_document(
                        session,
                        orm_proj.ProjectID,
                        "ActAward",
                        dc_proj.act_award,
                        project_type,
                    )
                    changed |= any(doc_result.values())
                if dc_proj.resolution_exempt:
                    doc_result = self._add_legal_document(
                        session,
                        orm_proj.ProjectID,
                        "ResolutionExempt",
                        dc_proj.resolution_exempt,
                        project_type,
                    )
                    changed |= any(doc_result.values())

                if dc_proj.cod:
                    date_result = self._add_relevant_date(
                        session,
                        orm_proj.ProjectID,
                        "COD_Estimated",
                        dc_proj.cod,
                        project_type,
                    )
                    changed |= any(date_result.values())

                self._finish_project_state(project_type, created, changed)
            except Exception as exc:
                self._add_error(project_type, getattr(dc_proj, "name", None), exc)
                raise

    def populate_generation(self, session, dataclass_projects: Iterable[Any]) -> None:
        """Convert generation dataclasses to ORM records."""
        self._populate_grid_scale_family(
            session=session,
            dataclass_projects=dataclass_projects,
            project_type="generation",
            orm_class=ORMGeneration,
            capacity_field="TotalCapacity",
            capacity_attr="total_capacity",
        )

    def populate_der(self, session, dataclass_projects: Iterable[Any]) -> None:
        """Convert DER dataclasses to ORM records."""
        self._populate_grid_scale_family(
            session=session,
            dataclass_projects=dataclass_projects,
            project_type="der",
            orm_class=ORMDER,
            capacity_field="TotalCapacity",
            capacity_attr="total_capacity",
        )

    def populate_bess(self, session, dataclass_projects: Iterable[Any]) -> None:
        """Convert BESS dataclasses to ORM records."""
        self._populate_grid_scale_family(
            session=session,
            dataclass_projects=dataclass_projects,
            project_type="bess",
            orm_class=ORMBESS,
            capacity_field="StorageCapacity",
            capacity_attr="storage_capacity",
        )

    def _populate_grid_scale_family(
        self,
        session,
        dataclass_projects: Iterable[Any],
        project_type: str,
        orm_class,
        capacity_field: str,
        capacity_attr: str,
    ) -> None:
        """Populate generation, DER, and BESS project families."""
        for dc_proj in dataclass_projects:
            self._increment(project_type, "processed")
            try:
                entity = (
                    self._ensure_lookup(session, ProjectEntity, dc_proj.project_entity)
                    if dc_proj.project_entity
                    else None
                )
                bay = (
                    self._ensure_lookup(session, Bay, dc_proj.bay)
                    if dc_proj.bay
                    else None
                )
                capacity_value = self._to_text_or_none(
                    getattr(dc_proj, capacity_attr, None)
                )

                create_kwargs = {
                    "ProjectEntityID": entity.ProjectEntityID if entity else None,
                    "BayID": bay.BayID if bay else None,
                    "Technology": dc_proj.technology,
                    "PowerCapacity": dc_proj.power_capacity,
                    capacity_field: capacity_value,
                    "Location": dc_proj.location,
                }
                orm_proj, created = self._get_or_create_project(
                    session, orm_class, project_type, dc_proj.name, create_kwargs
                )
                session.flush()

                changed = False
                if not created:
                    changed |= self._set_if_changed(
                        orm_proj,
                        "ProjectEntityID",
                        entity.ProjectEntityID if entity else None,
                        project_type,
                    )
                    changed |= self._set_if_changed(
                        orm_proj, "BayID", bay.BayID if bay else None, project_type
                    )
                    changed |= self._set_if_changed(
                        orm_proj, "Technology", dc_proj.technology, project_type
                    )
                    changed |= self._set_if_changed(
                        orm_proj, "PowerCapacity", dc_proj.power_capacity, project_type
                    )
                    changed |= self._set_if_changed(
                        orm_proj, capacity_field, capacity_value, project_type
                    )
                    changed |= self._set_if_changed(
                        orm_proj, "Location", dc_proj.location, project_type
                    )

                if dc_proj.resolution:
                    doc_result = self._add_legal_document(
                        session,
                        orm_proj.ProjectID,
                        "Resolution",
                        dc_proj.resolution,
                        project_type,
                    )
                    changed |= any(doc_result.values())

                if dc_proj.cod:
                    date_result = self._add_relevant_date(
                        session,
                        orm_proj.ProjectID,
                        "COD_Estimated",
                        dc_proj.cod,
                        project_type,
                    )
                    changed |= any(date_result.values())

                self._finish_project_state(project_type, created, changed)
            except Exception as exc:
                self._add_error(project_type, getattr(dc_proj, "name", None), exc)
                raise

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def populate_all(
        self, parser: ProjectParser, dry_run: bool = False
    ) -> Dict[str, Any]:
        """Populate all project families and return a structured summary.

        If dry_run=True, all database writes are rolled back at the end. This is
        useful for preview screens and agent-based review before confirmation.
        """
        self._reset_summary(dry_run=dry_run)
        session = self.Session()
        try:
            self._populate_lookups(session)
            self.populate_transmission(session, parser.transmission_projects)
            self.populate_generation(session, parser.generation_projects)
            self.populate_der(session, parser.der_projects)
            self.populate_bess(session, parser.bess_projects)

            if dry_run:
                session.rollback()
            else:
                session.commit()

            if self.verbose:
                action = "Preview completed" if dry_run else "Database populated"
                print(action)

            return deepcopy(self.summary)
        except Exception as exc:
            session.rollback()
            if self.verbose:
                print(f"Error: {exc}")
            raise
        finally:
            session.close()

    def preview_all(self, parser: ProjectParser) -> Dict[str, Any]:
        """Return the population summary without committing database changes."""
        return self.populate_all(parser=parser, dry_run=True)


if __name__ == "__main__":
    filename = "Tablas-Declaracion-Construccion-Enero-2026.xlsx"

    print("=" * 60)
    print("Database Population")
    print("=" * 60)
    print()

    parser = ProjectParser(filename=filename)
    populator = DatabasePopulator(verbose=True)

    preview = populator.preview_all(parser)
    print("Preview summary:")
    print(preview["total"])

    # To write changes, uncomment the following lines:
    # result = populator.populate_all(parser, dry_run=False)
    # print("Population summary:")
    # print(result["total"])

    print("=" * 60)
    print("Population script completed")
    print("=" * 60)
