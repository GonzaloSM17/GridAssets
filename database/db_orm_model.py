# db_orm_model.py
"""Database Models - ORM Definition.

SQLAlchemy ORM models for GridAssets.

Design:
- Central Project table with polymorphic specialized project tables.
- Lookup tables for states, entities, bays, technologies, documents, milestones and sources.
- Legal documents and relevant dates as normalized related entities.
- Electrical model management tables for future Streamlit model tracking.
"""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import declarative_base, relationship


Base = declarative_base()


# ============================================================
# LOOKUP TABLES
# ============================================================


class ProjectState(Base):
    """Project states such as NonStarted, UnderConstruction, OnHold, InService."""

    __tablename__ = "ProjectState"

    StateID = Column(Integer, primary_key=True, autoincrement=True)
    StateName = Column(String(100), nullable=False, unique=True)


class ProjectEntity(Base):
    """Project companies, owners or responsible entities."""

    __tablename__ = "ProjectEntity"

    ProjectEntityID = Column(Integer, primary_key=True, autoincrement=True)
    ProjectEntityName = Column(String(255), nullable=False, unique=True)


class Bay(Base):
    """Electrical bays or substations."""

    __tablename__ = "Bay"

    BayID = Column(Integer, primary_key=True, autoincrement=True)
    BayName = Column(String(100), nullable=False, unique=True)


class Technology(Base):
    """Normalized technology catalog."""

    __tablename__ = "Technology"

    TechnologyID = Column(Integer, primary_key=True, autoincrement=True)
    TechnologyName = Column(String(120), nullable=False, unique=True)
    TechnologyGroup = Column(String(80), nullable=True)
    IsActive = Column(Boolean, nullable=False, default=True)


class DocumentType(Base):
    """Legal document types such as Act, ActAward, Resolution, ResolutionExempt."""

    __tablename__ = "DocumentType"

    DocumentTypeID = Column(Integer, primary_key=True, autoincrement=True)
    TypeName = Column(String(100), nullable=False, unique=True)


class MilestoneType(Base):
    """Relevant date milestone types."""

    __tablename__ = "MilestoneType"

    MilestoneTypeID = Column(Integer, primary_key=True, autoincrement=True)
    MilestoneName = Column(String(100), nullable=False, unique=True)


class Source(Base):
    """Data sources such as CNE, PGP, SEO and User."""

    __tablename__ = "Source"

    SourceID = Column(Integer, primary_key=True, autoincrement=True)
    SourceName = Column(String(100), nullable=False, unique=True)


class Software(Base):
    """Electrical simulation software catalog."""

    __tablename__ = "Software"

    SoftwareID = Column(Integer, primary_key=True, autoincrement=True)
    SoftwareName = Column(String(120), nullable=False, unique=True)
    IsActive = Column(Boolean, nullable=False, default=True)


# ============================================================
# CORE PROJECT TABLE
# ============================================================


class Project(Base):
    """Central project table shared by all project families."""

    __tablename__ = "Project"

    ProjectID = Column(Integer, primary_key=True, autoincrement=True)
    ProjectName = Column(String(500), nullable=False)

    StateID = Column(Integer, ForeignKey("ProjectState.StateID"), nullable=True)
    NUP = Column(Integer, nullable=True)

    ProjectEntityID = Column(
        Integer,
        ForeignKey("ProjectEntity.ProjectEntityID"),
        nullable=True,
    )

    URL = Column(String(500), nullable=True)

    # SQLAlchemy polymorphic discriminator.
    project_discriminator = Column(String(50), nullable=True)

    # Relationships.
    state = relationship("ProjectState", backref="projects")
    entity = relationship("ProjectEntity", backref="projects")

    __mapper_args__ = {
        "polymorphic_identity": "project",
        "polymorphic_on": project_discriminator,
    }


# ============================================================
# SPECIALIZED PROJECT TABLES
# ============================================================


class TransmissionProject(Project):
    """Transmission-specific project data."""

    __tablename__ = "TransmissionProject"

    TransmissionProjectID = Column(Integer, primary_key=True, autoincrement=True)
    ProjectID = Column(Integer, ForeignKey("Project.ProjectID"), nullable=False)

    VoltageLevel = Column(String(50), nullable=True)
    TotalCapacity = Column(Float, nullable=True)

    __mapper_args__ = {
        "polymorphic_identity": "transmission",
    }


class GenerationProject(Project):
    """Generation-specific project data."""

    __tablename__ = "GenerationProject"

    GenerationProjectID = Column(Integer, primary_key=True, autoincrement=True)
    ProjectID = Column(Integer, ForeignKey("Project.ProjectID"), nullable=False)

    BayID = Column(Integer, ForeignKey("Bay.BayID"), nullable=True)
    TechnologyID = Column(Integer, ForeignKey("Technology.TechnologyID"), nullable=True)

    PowerCapacity = Column(Float, nullable=True)
    TotalCapacity = Column(Float, nullable=True)
    Location = Column(String(255), nullable=True)

    # Relationships.
    bay = relationship("Bay", backref="generation_projects")
    technology = relationship("Technology", backref="generation_projects")

    __mapper_args__ = {
        "polymorphic_identity": "generation",
    }


class DERProject(Project):
    """Distributed energy resource project data."""

    __tablename__ = "DERProject"

    DERProjectID = Column(Integer, primary_key=True, autoincrement=True)
    ProjectID = Column(Integer, ForeignKey("Project.ProjectID"), nullable=False)

    BayID = Column(Integer, ForeignKey("Bay.BayID"), nullable=True)
    TechnologyID = Column(Integer, ForeignKey("Technology.TechnologyID"), nullable=True)

    PowerCapacity = Column(Float, nullable=True)
    TotalCapacity = Column(Float, nullable=True)
    Location = Column(String(255), nullable=True)

    # Relationships.
    bay = relationship("Bay", backref="der_projects")
    technology = relationship("Technology", backref="der_projects")

    __mapper_args__ = {
        "polymorphic_identity": "der",
    }


class BESSProject(Project):
    """Battery energy storage system project data."""

    __tablename__ = "BESSProject"

    BESSProjectID = Column(Integer, primary_key=True, autoincrement=True)
    ProjectID = Column(Integer, ForeignKey("Project.ProjectID"), nullable=False)

    BayID = Column(Integer, ForeignKey("Bay.BayID"), nullable=True)
    TechnologyID = Column(Integer, ForeignKey("Technology.TechnologyID"), nullable=True)

    PowerCapacity = Column(Float, nullable=True)
    StorageCapacity = Column(Float, nullable=True)
    Location = Column(String(255), nullable=True)

    # Relationships.
    bay = relationship("Bay", backref="bess_projects")
    technology = relationship("Technology", backref="bess_projects")

    __mapper_args__ = {
        "polymorphic_identity": "bess",
    }


# ============================================================
# LEGAL DOCUMENTS
# ============================================================


class LegalDocument(Base):
    """Legal documents such as acts and resolutions."""

    __tablename__ = "LegalDocument"

    DocumentID = Column(Integer, primary_key=True, autoincrement=True)
    DocumentName = Column(String(500), nullable=False)
    DocumentYear = Column(Integer, nullable=True)

    DocumentTypeID = Column(
        Integer,
        ForeignKey("DocumentType.DocumentTypeID"),
        nullable=False,
    )

    # Relationships.
    document_type = relationship("DocumentType", backref="documents")


class ProjectLegalDocument(Base):
    """Many-to-many link between projects and legal documents."""

    __tablename__ = "ProjectLegalDocument"

    ProjectLegalDocumentID = Column(Integer, primary_key=True, autoincrement=True)
    ProjectID = Column(Integer, ForeignKey("Project.ProjectID"), nullable=False)
    DocumentID = Column(Integer, ForeignKey("LegalDocument.DocumentID"), nullable=False)

    # Relationships.
    project = relationship("Project", backref="legal_documents")
    legal_document = relationship("LegalDocument", backref="project_links")

    __table_args__ = (
        UniqueConstraint(
            "ProjectID",
            "DocumentID",
            name="UQ_ProjectLegalDocument_Project_Document",
        ),
    )


# ============================================================
# RELEVANT DATES
# ============================================================


class RelevantDate(Base):
    """Historical tracking of important project dates by source."""

    __tablename__ = "RelevantDate"

    RelevantDateID = Column(Integer, primary_key=True, autoincrement=True)
    ProjectID = Column(Integer, ForeignKey("Project.ProjectID"), nullable=False)

    MilestoneTypeID = Column(
        Integer,
        ForeignKey("MilestoneType.MilestoneTypeID"),
        nullable=False,
    )

    SourceID = Column(Integer, ForeignKey("Source.SourceID"), nullable=True)

    DateValue = Column(DateTime, nullable=False)
    ExtractedAt = Column(DateTime, nullable=False)

    # Relationships.
    project = relationship("Project", backref="relevant_dates")
    milestone_type = relationship("MilestoneType", backref="dates")
    source = relationship("Source", backref="dates")

    __table_args__ = (
        UniqueConstraint(
            "ProjectID",
            "MilestoneTypeID",
            "SourceID",
            name="UQ_RelevantDate_Project_Milestone_Source",
        ),
    )


# ============================================================
# ELECTRICAL MODEL MANAGEMENT
# ============================================================


class ElectricalModel(Base):
    """Electrical model managed in a specific simulation software."""

    __tablename__ = "ElectricalModel"

    ElectricalModelID = Column(Integer, primary_key=True, autoincrement=True)
    ElectricalModelName = Column(String(255), nullable=False)

    SoftwareID = Column(Integer, ForeignKey("Software.SoftwareID"), nullable=False)

    SoftwareVersion = Column(String(80), nullable=True)
    Description = Column(String(500), nullable=True)
    IsActive = Column(Boolean, nullable=False, default=True)

    # Relationships.
    software = relationship("Software", backref="electrical_models")

    __table_args__ = (
        UniqueConstraint(
            "ElectricalModelName",
            "SoftwareID",
            name="UQ_ElectricalModel_Name_Software",
        ),
    )


class ProjectElectricalModel(Base):
    """Project modeling status in a specific electrical model."""

    __tablename__ = "ProjectElectricalModel"

    ProjectElectricalModelID = Column(Integer, primary_key=True, autoincrement=True)

    ProjectID = Column(Integer, ForeignKey("Project.ProjectID"), nullable=False)
    ElectricalModelID = Column(
        Integer,
        ForeignKey("ElectricalModel.ElectricalModelID"),
        nullable=False,
    )

    IsModeled = Column(Boolean, nullable=False, default=False)
    Notes = Column(String(500), nullable=True)

    # Relationships.
    project = relationship("Project", backref="electrical_model_links")
    electrical_model = relationship("ElectricalModel", backref="project_links")

    __table_args__ = (
        UniqueConstraint(
            "ProjectID",
            "ElectricalModelID",
            name="UQ_ProjectElectricalModel_Project_Model",
        ),
    )
