# cne_ingestion_review_agent.py
"""
Rule-based review agent for CNE Excel ingestion previews.

This module does not write to the database and does not call external services.
It receives three deterministic inputs:
    - parser_report from ProjectParser.get_report()
    - project_counts from ProjectParser.get_project_counts()
    - population_preview from DatabasePopulator.preview_all(parser)

Its role is to transform technical ingestion data into a clear operational
review that can be shown in the UI before the user confirms database writing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


PROJECT_TYPE_LABELS = {
    "transmission": "Transmission",
    "generation": "Generation",
    "der": "DER / PMGD",
    "bess": "BESS",
}


@dataclass
class ReviewIssue:
    """Represents a single finding produced by the review agent."""

    severity: str
    title: str
    detail: str
    recommendation: str = ""

    def as_dict(self) -> Dict[str, str]:
        return {
            "severity": self.severity,
            "title": self.title,
            "detail": self.detail,
            "recommendation": self.recommendation,
        }


@dataclass
class ReviewResult:
    """Structured review returned by CNEIngestionReviewAgent."""

    status: str
    can_continue: bool
    recommendation: str
    parser_summary: Dict[str, Any] = field(default_factory=dict)
    population_summary: Dict[str, Any] = field(default_factory=dict)
    issues: List[ReviewIssue] = field(default_factory=list)
    markdown: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "can_continue": self.can_continue,
            "recommendation": self.recommendation,
            "parser_summary": self.parser_summary,
            "population_summary": self.population_summary,
            "issues": [issue.as_dict() for issue in self.issues],
            "markdown": self.markdown,
        }


class CNEIngestionReviewAgent:
    """Rule-based agent for reviewing CNE ingestion preview results."""

    def __init__(self, max_display_issues: int = 12):
        self.max_display_issues = max_display_issues

    def review(
        self,
        parser_report: Dict[str, Any],
        project_counts: Dict[str, int],
        population_preview: Dict[str, Any],
    ) -> ReviewResult:
        """Return an operational review of a CNE ingestion preview."""
        issues: List[ReviewIssue] = []

        parser_summary = self._build_parser_summary(parser_report, project_counts)
        population_summary = self._build_population_summary(population_preview)

        issues.extend(self._review_parser(parser_report, project_counts))
        issues.extend(self._review_population(population_preview))

        status = self._status_from_issues(issues)
        can_continue = status in {"ready", "review_recommended"}
        recommendation = self._build_recommendation(status, issues, population_summary)

        result = ReviewResult(
            status=status,
            can_continue=can_continue,
            recommendation=recommendation,
            parser_summary=parser_summary,
            population_summary=population_summary,
            issues=issues,
        )
        result.markdown = self._render_markdown(result)
        return result

    def _build_parser_summary(
        self, parser_report: Dict[str, Any], project_counts: Dict[str, int]
    ) -> Dict[str, Any]:
        return {
            "input_file": parser_report.get("input_file"),
            "available_sheets": len(parser_report.get("available_sheets", [])),
            "missing_sheets": parser_report.get("missing_sheets", []),
            "project_counts": project_counts,
            "invalid_dates": len(parser_report.get("invalid_dates", [])),
            "missing_columns_by_sheet": parser_report.get("missing_columns_by_sheet", {}),
            "dropped_rows_by_sheet": parser_report.get("dropped_rows_by_sheet", {}),
        }

    def _build_population_summary(self, population_preview: Dict[str, Any]) -> Dict[str, Any]:
        total = population_preview.get("total", {})
        return {
            "dry_run": population_preview.get("dry_run"),
            "processed": total.get("processed", 0),
            "created": total.get("created", 0),
            "updated": total.get("updated", 0),
            "unchanged": total.get("unchanged", 0),
            "errors": total.get("errors", 0),
            "legal_documents_created": total.get("legal_documents_created", 0),
            "legal_documents_linked": total.get("legal_documents_linked", 0),
            "relevant_dates_created": total.get("relevant_dates_created", 0),
            "relevant_dates_updated": total.get("relevant_dates_updated", 0),
            "field_changes": total.get("field_changes", {}),
        }

    def _review_parser(
        self, parser_report: Dict[str, Any], project_counts: Dict[str, int]
    ) -> List[ReviewIssue]:
        issues: List[ReviewIssue] = []

        if project_counts.get("total", 0) == 0:
            issues.append(
                ReviewIssue(
                    severity="critical",
                    title="No projects were parsed",
                    detail="The parser did not extract any project from the uploaded workbook.",
                    recommendation="Stop the ingestion and verify that the workbook corresponds to the CNE construction declaration format.",
                )
            )

        missing_sheets = parser_report.get("missing_sheets", []) or []
        if missing_sheets:
            issues.append(
                ReviewIssue(
                    severity="warning",
                    title="Expected sheets are missing",
                    detail=f"Missing sheets: {', '.join(missing_sheets)}.",
                    recommendation="Review whether the CNE workbook changed its structure or whether the file is incomplete.",
                )
            )

        invalid_dates = parser_report.get("invalid_dates", []) or []
        if invalid_dates:
            issues.append(
                ReviewIssue(
                    severity="critical",
                    title="Invalid dates were detected",
                    detail=f"The parser found {len(invalid_dates)} invalid date value(s).",
                    recommendation="Fix the date parser or review the affected rows before writing to the database.",
                )
            )

        missing_columns_by_sheet = parser_report.get("missing_columns_by_sheet", {}) or {}
        relevant_missing_columns = {
            sheet: columns
            for sheet, columns in missing_columns_by_sheet.items()
            if columns
        }
        if relevant_missing_columns:
            details = []
            for sheet, columns in relevant_missing_columns.items():
                details.append(f"{sheet}: {', '.join(columns)}")
            issues.append(
                ReviewIssue(
                    severity="warning",
                    title="Some expected columns were not mapped",
                    detail="; ".join(details),
                    recommendation="Review column names before confirming the ingestion, especially if these columns contain critical data.",
                )
            )

        dropped_rows_by_sheet = parser_report.get("dropped_rows_by_sheet", {}) or {}
        total_dropped = sum(int(value or 0) for value in dropped_rows_by_sheet.values())
        if total_dropped > 0:
            issues.append(
                ReviewIssue(
                    severity="info",
                    title="Rows were discarded during parsing",
                    detail=f"The parser discarded {total_dropped} row(s), usually because required fields were empty.",
                    recommendation="This is acceptable if the discarded rows are notes, footers, or incomplete CNE rows.",
                )
            )

        return issues

    def _review_population(self, population_preview: Dict[str, Any]) -> List[ReviewIssue]:
        issues: List[ReviewIssue] = []
        total = population_preview.get("total", {})

        if not population_preview.get("dry_run", False):
            issues.append(
                ReviewIssue(
                    severity="warning",
                    title="Preview was not executed in dry-run mode",
                    detail="The population summary does not indicate dry_run=True.",
                    recommendation="Use DatabasePopulator.preview_all(parser) before showing the review to the user.",
                )
            )

        if total.get("errors", 0) > 0:
            issues.append(
                ReviewIssue(
                    severity="critical",
                    title="Population preview reported errors",
                    detail=f"The preview reported {total.get('errors', 0)} error(s).",
                    recommendation="Do not confirm database writing until the errors are resolved.",
                )
            )

        processed = int(total.get("processed", 0) or 0)
        created = int(total.get("created", 0) or 0)
        updated = int(total.get("updated", 0) or 0)
        unchanged = int(total.get("unchanged", 0) or 0)

        if processed == 0:
            issues.append(
                ReviewIssue(
                    severity="critical",
                    title="No records were processed in preview",
                    detail="The database preview processed zero records.",
                    recommendation="Check the parser output and database preview service before continuing.",
                )
            )
        elif created + updated == 0 and unchanged == processed:
            issues.append(
                ReviewIssue(
                    severity="info",
                    title="No database changes are expected",
                    detail="All previewed projects already exist with the same tracked data.",
                    recommendation="Confirming the ingestion should not change project records, but the file can still be archived as reviewed.",
                )
            )

        for project_type, label in PROJECT_TYPE_LABELS.items():
            section = population_preview.get(project_type, {})
            if section.get("errors", 0) > 0:
                issues.append(
                    ReviewIssue(
                        severity="critical",
                        title=f"Errors in {label}",
                        detail=f"{label} reported {section.get('errors', 0)} error(s).",
                        recommendation="Review the detailed error list for this project family.",
                    )
                )

        field_changes = total.get("field_changes", {}) or {}
        if field_changes:
            sorted_changes = sorted(field_changes.items(), key=lambda item: item[1], reverse=True)
            top_changes = ", ".join(f"{field}: {count}" for field, count in sorted_changes[:6])
            issues.append(
                ReviewIssue(
                    severity="info",
                    title="Tracked fields would be updated",
                    detail=f"Most frequent field changes: {top_changes}.",
                    recommendation="Review whether these fields are expected to change from the latest CNE publication.",
                )
            )

        return issues

    def _status_from_issues(self, issues: List[ReviewIssue]) -> str:
        severities = {issue.severity for issue in issues}
        if "critical" in severities:
            return "blocked"
        if "warning" in severities:
            return "review_recommended"
        return "ready"

    def _build_recommendation(
        self,
        status: str,
        issues: List[ReviewIssue],
        population_summary: Dict[str, Any],
    ) -> str:
        if status == "blocked":
            return "Do not write to the database yet. Resolve the critical findings and run the preview again."
        if status == "review_recommended":
            return "The ingestion can continue after reviewing the warnings. Confirm only if the findings are expected."

        created = population_summary.get("created", 0)
        updated = population_summary.get("updated", 0)
        unchanged = population_summary.get("unchanged", 0)
        if created or updated:
            return "The preview is ready. The file can be confirmed for database population."
        if unchanged:
            return "The preview is ready, but no database changes are expected."
        return "The preview is ready."

    def _render_markdown(self, result: ReviewResult) -> str:
        parser_counts = result.parser_summary.get("project_counts", {})
        pop = result.population_summary

        lines = [
            "# CNE Ingestion Review",
            "",
            f"**Status:** {result.status}",
            f"**Can continue:** {'Yes' if result.can_continue else 'No'}",
            f"**Recommendation:** {result.recommendation}",
            "",
            "## Parser summary",
            "",
            f"- Input file: `{result.parser_summary.get('input_file')}`",
            f"- Parsed projects: {parser_counts.get('total', 0)}",
            f"- Transmission: {parser_counts.get('transmission', 0)}",
            f"- Generation: {parser_counts.get('generation', 0)}",
            f"- DER / PMGD: {parser_counts.get('der', 0)}",
            f"- BESS: {parser_counts.get('bess', 0)}",
            f"- Invalid dates: {result.parser_summary.get('invalid_dates', 0)}",
            "",
            "## Database preview",
            "",
            f"- Dry-run: {'Yes' if pop.get('dry_run') else 'No'}",
            f"- Processed: {pop.get('processed', 0)}",
            f"- Created: {pop.get('created', 0)}",
            f"- Updated: {pop.get('updated', 0)}",
            f"- Unchanged: {pop.get('unchanged', 0)}",
            f"- Errors: {pop.get('errors', 0)}",
            f"- Legal documents created: {pop.get('legal_documents_created', 0)}",
            f"- Legal documents linked: {pop.get('legal_documents_linked', 0)}",
            f"- Relevant dates created: {pop.get('relevant_dates_created', 0)}",
            f"- Relevant dates updated: {pop.get('relevant_dates_updated', 0)}",
            "",
            "## Findings",
            "",
        ]

        if not result.issues:
            lines.append("- No findings.")
        else:
            for issue in result.issues[: self.max_display_issues]:
                lines.append(f"- **{issue.severity.upper()} - {issue.title}:** {issue.detail}")
                if issue.recommendation:
                    lines.append(f"  Recommendation: {issue.recommendation}")
            remaining = len(result.issues) - self.max_display_issues
            if remaining > 0:
                lines.append(f"- {remaining} additional finding(s) not displayed.")

        return "\n".join(lines)
