# Database Models - ORM Definition
"""
SQLAlchemy ORM models for energy projects database
Normalized design with central Project table and specializations
"""

from sqlalchemy import (
    Column,
    Integer,
    String,
    ForeignKey,
    Float,
    DateTime,
    Boolean,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship, declarative_base

Base = declarative_base()


# ==================== LOOKUP TABLES ====================


class ProjectState(Base):
    """NonStarted, UnderConstruction, OnHold, InService"""

    __tablename__ = "ProjectState"

    StateID = Column(Integer, primary_key=True)
    StateName = Column(String(100), nullable=False, unique=True)


class ProjectEntity(Base):
    """Companies, owners, responsables"""

    __tablename__ = "ProjectEntity"

    ProjectEntityID = Column(Integer, primary_key=True)
    ProjectEntityName = Column(String(255), nullable=False, unique=True)


class Bay(Base):
    """Electrical bays/Substations"""

    __tablename__ = "Bay"

    BayID = Column(Integer, primary_key=True)
    BayName = Column(String(100), nullable=False, unique=True)


class DocumentType(Base):
    """Act, ActAward, Resolution, ResolutionExempt"""

    __tablename__ = "DocumentType"

    DocumentTypeID = Column(Integer, primary_key=True)
    TypeName = Column(String(100), nullable=False, unique=True)


class MilestoneType(Base):
    """COD_Estimated, COD_Actual, Start_Construction, Commissioning_Estimated, Commissioning_Actual"""

    __tablename__ = "MilestoneType"

    MilestoneTypeID = Column(Integer, primary_key=True)
    MilestoneName = Column(String(100), nullable=False, unique=True)


class Source(Base):
    """CNE, PGP, SEO, User"""

    __tablename__ = "Source"

    SourceID = Column(Integer, primary_key=True)
    SourceName = Column(String(100), nullable=False, unique=True)


class Technology(Base):
    """Technology lookup table for generation, DER and BESS projects."""

    __tablename__ = "Technology"

    TechnologyID = Column(Integer, primary_key=True, autoincrement=True)
    TechnologyName = Column(String(120), nullable=False, unique=True)
    TechnologyGroup = Column(String(80), nullable=True)
    IsActive = Column(Boolean, nullable=False, default=True)

    generation_projects = relationship("GenerationProject", back_populates="technology")
    der_projects = relationship("DERProject", back_populates="technology")
    bess_projects = relationship("BESSProject", back_populates="technology")


# ==================== CORE PROJECT TABLE ====================


class Project(Base):
    """Central project table - all projects regardless of type"""

    __tablename__ = "Project"

    ProjectID = Column(Integer, primary_key=True)
    ProjectName = Column(String(500), nullable=False)
    StateID = Column(Integer, ForeignKey("ProjectState.StateID"), nullable=True)
    NUP = Column(Integer, nullable=True)  # Número Único de Proyecto
    ProjectEntityID = Column(
        Integer, ForeignKey("ProjectEntity.ProjectEntityID"), nullable=True
    )
    URL = Column(String(500), nullable=True)  # PGP URL

    # Polymorphic configuration
    project_discriminator = Column(String(50))

    # Relationships
    state = relationship("ProjectState", backref="projects")
    entity = relationship("ProjectEntity", backref="projects")

    __mapper_args__ = {
        "polymorphic_identity": "project",
        "polymorphic_on": project_discriminator,
    }


# ==================== SPECIALIZED PROJECT TABLES (Inherit from Project) ====================


class TransmissionProject(Project):
    """Transmission-specific data"""

    __tablename__ = "TransmissionProject"

    TransmissionProjectID = Column(Integer, primary_key=True)
    ProjectID = Column(Integer, ForeignKey("Project.ProjectID"), nullable=False)
    VoltageLevel = Column(String(50), nullable=True)
    TotalCapacity = Column(String(50), nullable=True)

    __mapper_args__ = {
        "polymorphic_identity": "transmission",
    }


class GenerationProject(Project):
    """Generation-specific data"""

    __tablename__ = "GenerationProject"

    GenerationProjectID = Column(Integer, primary_key=True)
    ProjectID = Column(Integer, ForeignKey("Project.ProjectID"), nullable=False)
    BayID = Column(Integer, ForeignKey("Bay.BayID"), nullable=True)

    TechnologyID = Column(Integer, ForeignKey("Technology.TechnologyID"), nullable=True)

    PowerCapacity = Column(Float, nullable=True)
    TotalCapacity = Column(Float, nullable=True)
    Location = Column(String(255), nullable=True)

    # Relationships
    bay = relationship("Bay", backref="generation_projects")
    technology = relationship("Technology", back_populates="generation_projects")

    __mapper_args__ = {
        "polymorphic_identity": "generation",
    }


class DERProject(Project):
    """DER-specific data"""

    __tablename__ = "DERProject"

    DERProjectID = Column(Integer, primary_key=True)
    ProjectID = Column(Integer, ForeignKey("Project.ProjectID"), nullable=False)
    BayID = Column(Integer, ForeignKey("Bay.BayID"), nullable=True)

    TechnologyID = Column(Integer, ForeignKey("Technology.TechnologyID"), nullable=True)

    PowerCapacity = Column(Float, nullable=True)
    TotalCapacity = Column(Float, nullable=True)
    Location = Column(String(255), nullable=True)

    # Relationships
    bay = relationship("Bay", backref="der_projects")
    technology = relationship("Technology", back_populates="der_projects")

    __mapper_args__ = {
        "polymorphic_identity": "der",
    }


class BESSProject(Project):
    """BESS-specific data"""

    __tablename__ = "BESSProject"

    BESSProjectID = Column(Integer, primary_key=True)
    ProjectID = Column(Integer, ForeignKey("Project.ProjectID"), nullable=False)
    BayID = Column(Integer, ForeignKey("Bay.BayID"), nullable=True)

    TechnologyID = Column(Integer, ForeignKey("Technology.TechnologyID"), nullable=True)

    PowerCapacity = Column(Float, nullable=True)
    StorageCapacity = Column(Float, nullable=True)
    Location = Column(String(255), nullable=True)

    # Relationships
    bay = relationship("Bay", backref="bess_projects")
    technology = relationship("Technology", back_populates="bess_projects")
    __mapper_args__ = {
        "polymorphic_identity": "bess",
    }


# ==================== LEGAL DOCUMENTS ====================


class LegalDocument(Base):
    """Legal documents (Acts, Resolutions, etc.)"""

    __tablename__ = "LegalDocument"

    DocumentID = Column(Integer, primary_key=True)
    DocumentName = Column(String(500), nullable=False)
    DocumentYear = Column(Integer, nullable=True)
    DocumentTypeID = Column(
        Integer, ForeignKey("DocumentType.DocumentTypeID"), nullable=False
    )

    # Relationships
    document_type = relationship("DocumentType", backref="documents")


class ProjectLegalDocument(Base):
    """Many-to-many: Projects <-> Legal Documents"""

    __tablename__ = "ProjectLegalDocument"

    ProjectLegalDocumentID = Column(Integer, primary_key=True)
    ProjectID = Column(Integer, ForeignKey("Project.ProjectID"), nullable=False)
    DocumentID = Column(Integer, ForeignKey("LegalDocument.DocumentID"), nullable=False)

    # Relationships
    project = relationship("Project", backref="legal_documents")
    legal_document = relationship("LegalDocument", backref="project_links")


# ==================== RELEVANT DATES ====================


class RelevantDate(Base):
    """Historical tracking of important project dates"""

    __tablename__ = "RelevantDate"

    RelevantDateID = Column(Integer, primary_key=True)
    ProjectID = Column(Integer, ForeignKey("Project.ProjectID"), nullable=False)
    MilestoneTypeID = Column(
        Integer, ForeignKey("MilestoneType.MilestoneTypeID"), nullable=False
    )
    SourceID = Column(Integer, ForeignKey("Source.SourceID"), nullable=True)
    DateValue = Column(DateTime, nullable=False)
    ExtractedAt = Column(DateTime, nullable=False)  # Timestamp de extracción

    # Relationships
    project = relationship("Project", backref="relevant_dates")
    milestone_type = relationship("MilestoneType", backref="dates")
    source = relationship("Source", backref="dates")


# ==================== Electrical Model ====================
DEFAULT_SOFTWARES = [
    "DIgSILENT PowerFactory",
    "PSS/E",
    "PSCAD",
    "EMTP-RV",
]


class Software(Base):
    __tablename__ = "Software"

    SoftwareID = Column(Integer, primary_key=True, autoincrement=True)
    SoftwareName = Column(String(120), nullable=False, unique=True)
    IsActive = Column(Boolean, nullable=False, default=True)


class ElectricalModel(Base):
    __tablename__ = "ElectricalModel"

    ElectricalModelID = Column(Integer, primary_key=True, autoincrement=True)
    ElectricalModelName = Column(String(255), nullable=False)
    SoftwareID = Column(Integer, ForeignKey("Software.SoftwareID"), nullable=False)
    SoftwareVersion = Column(String(80), nullable=True)
    Description = Column(String(500), nullable=True)
    IsActive = Column(Boolean, nullable=False, default=True)

    software = relationship("Software", backref="electrical_models")


class ProjectElectricalModel(Base):
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

    project = relationship("Project", backref="electrical_model_links")
    electrical_model = relationship("ElectricalModel", backref="project_links")

    __table_args__ = (
        UniqueConstraint(
            "ProjectID",
            "ElectricalModelID",
            name="UQ_ProjectElectricalModel_Project_Model",
        ),
    )
