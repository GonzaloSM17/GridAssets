PROJECTS_BASE_QUERY = """
SELECT
    p.ProjectID,
    p.ProjectName,
    p.NUP,
    ps.StateName,
    pe.ProjectEntityName,
    p.URL,
    p.project_discriminator
FROM Project p
LEFT JOIN ProjectState ps
    ON p.StateID = ps.StateID
LEFT JOIN ProjectEntity pe
    ON p.ProjectEntityID = pe.ProjectEntityID
ORDER BY p.ProjectID;
"""

PROJECTS_OVERVIEW_QUERY = """
WITH LastRelevantDate AS (
    SELECT
        rd.ProjectID,
        mt.MilestoneName,
        s.SourceName,
        rd.DateValue,
        ROW_NUMBER() OVER (
            PARTITION BY rd.ProjectID
            ORDER BY rd.DateValue DESC, rd.ExtractedAt DESC
        ) AS RowNumber
    FROM RelevantDate rd
    LEFT JOIN MilestoneType mt
        ON rd.MilestoneTypeID = mt.MilestoneTypeID
    LEFT JOIN Source s
        ON rd.SourceID = s.SourceID
)
SELECT
    p.ProjectID,
    p.ProjectName,
    p.NUP,
    pe.ProjectEntityName,
    lrd.MilestoneName AS LastMilestoneName,
    lrd.SourceName AS LastMilestoneSource,
    lrd.DateValue AS LastMilestoneDate,
    p.URL AS PGP_URL,
    CASE
        WHEN p.project_discriminator = 'transmission' AND p.NUP IS NOT NULL
        THEN 'https://seguimientoejecucionobras.coordinador.cl/'
        ELSE NULL
    END AS SEO_URL,
    p.project_discriminator
FROM Project p
LEFT JOIN ProjectEntity pe
    ON p.ProjectEntityID = pe.ProjectEntityID
LEFT JOIN LastRelevantDate lrd
    ON p.ProjectID = lrd.ProjectID
    AND lrd.RowNumber = 1
ORDER BY p.ProjectID;
"""

PROJECT_FEATURES_QUERY = """
SELECT
    p.ProjectID,
    p.project_discriminator AS ProjectType,
    tp.VoltageLevel,
    tp.TotalCapacity AS TransmissionTotalCapacity,
    NULL AS BayName,
    NULL AS Technology,
    NULL AS PowerCapacity,
    NULL AS GenerationTotalCapacity,
    NULL AS StorageCapacity,
    NULL AS Location
FROM Project p
INNER JOIN TransmissionProject tp
    ON p.ProjectID = tp.ProjectID

UNION ALL

SELECT
    p.ProjectID,
    p.project_discriminator AS ProjectType,
    NULL AS VoltageLevel,
    NULL AS TransmissionTotalCapacity,
    b.BayName,
    gp.Technology,
    gp.PowerCapacity,
    gp.TotalCapacity AS GenerationTotalCapacity,
    NULL AS StorageCapacity,
    gp.Location
FROM Project p
INNER JOIN GenerationProject gp
    ON p.ProjectID = gp.ProjectID
LEFT JOIN Bay b
    ON gp.BayID = b.BayID

UNION ALL

SELECT
    p.ProjectID,
    p.project_discriminator AS ProjectType,
    NULL AS VoltageLevel,
    NULL AS TransmissionTotalCapacity,
    b.BayName,
    dp.Technology,
    dp.PowerCapacity,
    dp.TotalCapacity AS GenerationTotalCapacity,
    NULL AS StorageCapacity,
    dp.Location
FROM Project p
INNER JOIN DERProject dp
    ON p.ProjectID = dp.ProjectID
LEFT JOIN Bay b
    ON dp.BayID = b.BayID

UNION ALL

SELECT
    p.ProjectID,
    p.project_discriminator AS ProjectType,
    NULL AS VoltageLevel,
    NULL AS TransmissionTotalCapacity,
    b.BayName,
    bp.Technology,
    bp.PowerCapacity,
    NULL AS GenerationTotalCapacity,
    bp.StorageCapacity,
    bp.Location
FROM Project p
INNER JOIN BESSProject bp
    ON p.ProjectID = bp.ProjectID
LEFT JOIN Bay b
    ON bp.BayID = b.BayID;
"""


PROJECT_DATES_QUERY = """
SELECT
    rd.ProjectID,
    mt.MilestoneName,
    s.SourceName,
    rd.DateValue,
    rd.ExtractedAt
FROM RelevantDate rd
LEFT JOIN MilestoneType mt
    ON rd.MilestoneTypeID = mt.MilestoneTypeID
LEFT JOIN Source s
    ON rd.SourceID = s.SourceID
ORDER BY
    rd.ProjectID,
    rd.DateValue DESC,
    rd.ExtractedAt DESC;
"""


PROJECT_LEGAL_DOCUMENTS_QUERY = """
SELECT
    pld.ProjectID,
    dt.TypeName AS DocumentType,
    ld.DocumentName,
    ld.DocumentYear
FROM ProjectLegalDocument pld
LEFT JOIN LegalDocument ld
    ON pld.DocumentID = ld.DocumentID
LEFT JOIN DocumentType dt
    ON ld.DocumentTypeID = dt.DocumentTypeID
ORDER BY
    pld.ProjectID,
    dt.TypeName,
    ld.DocumentYear;
"""
