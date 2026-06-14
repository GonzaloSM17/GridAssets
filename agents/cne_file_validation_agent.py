"""AI-assisted CNE file validation agent.

The agent validates whether an uploaded Excel workbook is structurally suitable
for the deterministic CNE parser.

When an OpenAI API key is available, the agent uses a real LLM call to evaluate
metadata, sheet structures, matched columns, row counts, and samples. If the API
client is unavailable, it falls back to deterministic validation rules so the app
can still run in local development.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional
import json
import os

from services.cne_excel_structure_reader import ExcelStructureReport


@dataclass
class CNEFileValidationResult:
    status: str
    is_cne_file: bool
    is_parse_ready: bool
    confidence: float
    summary: str
    blocking_issues: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    detected_sheets: List[str] = field(default_factory=list)
    missing_expected_sheets: List[str] = field(default_factory=list)
    recommendation: str = ""
    validation_mode: str = "deterministic"
    raw_llm_response: Optional[Dict[str, Any]] = None

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_markdown(self) -> str:
        lines = [
            "# CNE file validation",
            "",
            f"**Status:** `{self.status}`",
            f"**CNE file:** `{self.is_cne_file}`",
            f"**Parse ready:** `{self.is_parse_ready}`",
            f"**Confidence:** `{self.confidence:.2f}`",
            f"**Validation mode:** `{self.validation_mode}`",
            "",
            "## Summary",
            self.summary or "No summary provided.",
            "",
        ]

        if self.blocking_issues:
            lines.extend(["## Blocking issues"])
            lines.extend([f"- {item}" for item in self.blocking_issues])
            lines.append("")

        if self.warnings:
            lines.extend(["## Warnings"])
            lines.extend([f"- {item}" for item in self.warnings])
            lines.append("")

        if self.missing_expected_sheets:
            lines.extend(["## Missing expected sheets"])
            lines.extend([f"- {item}" for item in self.missing_expected_sheets])
            lines.append("")

        lines.extend(
            [
                "## Recommendation",
                self.recommendation or "No recommendation provided.",
            ]
        )
        return "\n".join(lines)


class CNEFileValidationAgent:
    """Validate whether a CNE workbook is ready for deterministic parsing."""

    VALID_STATUSES = {"valid", "review_required", "invalid"}

    def __init__(
        self,
        model: Optional[str] = None,
        use_llm: bool = True,
        api_key_env: str = "OPENAI_API_KEY",
    ):
        self.model = model or os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
        self.use_llm = use_llm
        self.api_key_env = api_key_env

    def validate(
        self, structure_report: ExcelStructureReport
    ) -> CNEFileValidationResult:
        deterministic_result = self._deterministic_validate(structure_report)

        if not self.use_llm:
            return deterministic_result

        if not os.getenv(self.api_key_env):
            deterministic_result.warnings.append(
                "OpenAI API key not found. Used deterministic validation fallback."
            )
            return deterministic_result

        try:
            llm_result = self._llm_validate(structure_report, deterministic_result)
            return llm_result
        except Exception as exc:
            deterministic_result.warnings.append(
                f"LLM validation failed. Used deterministic fallback. Error: {exc}"
            )
            return deterministic_result

    def _deterministic_validate(
        self, structure_report: ExcelStructureReport
    ) -> CNEFileValidationResult:
        blocking_issues: List[str] = []
        warnings: List[str] = []

        if not structure_report.can_open:
            blocking_issues.append(
                structure_report.open_error or "Workbook cannot be opened."
            )

        if structure_report.extension not in {".xlsx", ".xlsm", ".xls"}:
            blocking_issues.append(
                f"Unsupported extension for CNE parser: {structure_report.extension}"
            )

        missing_sheets = list(structure_report.missing_expected_sheets)
        if missing_sheets:
            # Missing sheets are not always fatal, but many missing sheets indicate a
            # different source file or a CNE format change.
            if len(missing_sheets) >= 4:
                blocking_issues.append(
                    f"Too many expected parser sheets are missing: {', '.join(missing_sheets)}"
                )
            else:
                warnings.append(
                    f"Some expected sheets are missing: {', '.join(missing_sheets)}"
                )

        detected_candidates = 0
        sheets_with_read_errors = []
        sheets_with_missing_critical_fields = []
        for sheet_name, sheet in structure_report.sheets.items():
            if not sheet.exists:
                continue
            if sheet.read_error:
                sheets_with_read_errors.append(f"{sheet_name}: {sheet.read_error}")
                continue
            detected_candidates += sheet.row_count_parse_candidates
            critical_missing = [
                item for item in sheet.missing_required_fields if item in {"name"}
            ]
            if critical_missing:
                sheets_with_missing_critical_fields.append(
                    f"{sheet_name}: {', '.join(critical_missing)}"
                )

        if sheets_with_read_errors:
            warnings.extend(sheets_with_read_errors)

        if sheets_with_missing_critical_fields:
            blocking_issues.extend(
                [
                    "Missing critical parser fields in expected sheets:",
                    *sheets_with_missing_critical_fields,
                ]
            )

        if detected_candidates == 0 and structure_report.can_open:
            blocking_issues.append(
                "No parseable project rows were detected in expected sheets."
            )

        if blocking_issues:
            status = "invalid"
            is_parse_ready = False
            confidence = 0.20
            recommendation = (
                "Do not run ProjectParser until the blocking issues are fixed."
            )
        elif warnings:
            status = "review_required"
            is_parse_ready = True
            confidence = 0.75
            recommendation = "The file appears compatible, but review warnings before running ProjectParser."
        else:
            status = "valid"
            is_parse_ready = True
            confidence = 0.93
            recommendation = "Continue with deterministic ProjectParser."

        is_cne_file = bool(structure_report.can_open and detected_candidates > 0)
        summary = (
            f"Workbook contains {len(structure_report.sheet_names)} sheets and "
            f"{detected_candidates} parse candidate rows across expected CNE sheets."
        )

        return CNEFileValidationResult(
            status=status,
            is_cne_file=is_cne_file,
            is_parse_ready=is_parse_ready,
            confidence=confidence,
            summary=summary,
            blocking_issues=blocking_issues,
            warnings=warnings,
            detected_sheets=structure_report.sheet_names,
            missing_expected_sheets=missing_sheets,
            recommendation=recommendation,
            validation_mode="deterministic",
        )

    def _llm_validate(
        self,
        structure_report: ExcelStructureReport,
        deterministic_result: CNEFileValidationResult,
    ) -> CNEFileValidationResult:
        from openai import OpenAI

        client = OpenAI()
        payload = self._build_llm_payload(structure_report, deterministic_result)

        schema = {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["valid", "review_required", "invalid"],
                },
                "is_cne_file": {"type": "boolean"},
                "is_parse_ready": {"type": "boolean"},
                "confidence": {"type": "number"},
                "summary": {"type": "string"},
                "blocking_issues": {"type": "array", "items": {"type": "string"}},
                "warnings": {"type": "array", "items": {"type": "string"}},
                "detected_sheets": {"type": "array", "items": {"type": "string"}},
                "missing_expected_sheets": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "recommendation": {"type": "string"},
            },
            "required": [
                "status",
                "is_cne_file",
                "is_parse_ready",
                "confidence",
                "summary",
                "blocking_issues",
                "warnings",
                "detected_sheets",
                "missing_expected_sheets",
                "recommendation",
            ],
            "additionalProperties": False,
        }

        system_prompt = (
            "You are a validation agent for Chilean CNE monthly Excel files used "
            "to declare energy projects under construction. Your only task is to "
            "decide whether the uploaded workbook is structurally suitable for the "
            "deterministic parser. Do not invent data. Prefer caution: mark "
            "review_required when the file is probably usable but there are sheet, "
            "header, or row anomalies. Mark invalid only when parsing should be "
            "blocked. Respond in English JSON according to the schema."
        )

        user_prompt = (
            "Evaluate whether this Excel workbook is parse-ready for the deterministic "
            "CNE parser. Use the deterministic precheck as evidence but apply semantic "
            "judgment to sheet names, column matches, row counts, and samples.\n\n"
            f"STRUCTURE_REPORT_JSON:\n{json.dumps(payload, ensure_ascii=False)}"
        )

        try:
            response = client.responses.create(
                model=self.model,
                input=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                text={
                    "format": {
                        "type": "json_schema",
                        "name": "cne_file_validation_result",
                        "schema": schema,
                        "strict": True,
                    }
                },
            )
            raw_text = response.output_text
        except Exception:
            # Compatibility fallback for projects still using Chat Completions.
            response = client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                response_format={"type": "json_object"},
            )
            raw_text = response.choices[0].message.content

        data = json.loads(raw_text)
        return self._result_from_llm_data(data)

    def _build_llm_payload(
        self,
        structure_report: ExcelStructureReport,
        deterministic_result: CNEFileValidationResult,
    ) -> Dict[str, Any]:
        full_report = structure_report.as_dict()

        # Keep the prompt compact: samples are useful, but too many rows are not.
        compact_sheets: Dict[str, Dict[str, Any]] = {}
        for sheet_name, sheet in full_report.get("sheets", {}).items():
            compact_sheets[sheet_name] = {
                "exists": sheet.get("exists"),
                "parser_header_row": sheet.get("parser_header_row"),
                "parser_usecols": sheet.get("parser_usecols"),
                "columns": sheet.get("columns", []),
                "row_count_raw": sheet.get("row_count_raw", 0),
                "row_count_with_project": sheet.get("row_count_with_project", 0),
                "row_count_parse_candidates": sheet.get(
                    "row_count_parse_candidates", 0
                ),
                "missing_required_fields": sheet.get("missing_required_fields", []),
                "matched_fields": sheet.get("matched_fields", {}),
                "sample_rows": sheet.get("sample_rows", [])[:3],
                "read_error": sheet.get("read_error"),
            }

        return {
            "filename": full_report.get("filename"),
            "extension": full_report.get("extension"),
            "file_size_bytes": full_report.get("file_size_bytes"),
            "can_open": full_report.get("can_open"),
            "open_error": full_report.get("open_error"),
            "sheet_names": full_report.get("sheet_names", []),
            "expected_sheets": full_report.get("expected_sheets", []),
            "missing_expected_sheets": full_report.get("missing_expected_sheets", []),
            "unexpected_sheets": full_report.get("unexpected_sheets", []),
            "sheets": compact_sheets,
            "deterministic_precheck": deterministic_result.as_dict(),
        }

    def _result_from_llm_data(self, data: Dict[str, Any]) -> CNEFileValidationResult:
        status = data.get("status", "review_required")
        if status not in self.VALID_STATUSES:
            status = "review_required"

        confidence = data.get("confidence", 0.5)
        try:
            confidence = float(confidence)
        except (TypeError, ValueError):
            confidence = 0.5
        confidence = max(0.0, min(1.0, confidence))

        return CNEFileValidationResult(
            status=status,
            is_cne_file=bool(data.get("is_cne_file", False)),
            is_parse_ready=bool(data.get("is_parse_ready", False)),
            confidence=confidence,
            summary=str(data.get("summary", "")),
            blocking_issues=list(data.get("blocking_issues", [])),
            warnings=list(data.get("warnings", [])),
            detected_sheets=list(data.get("detected_sheets", [])),
            missing_expected_sheets=list(data.get("missing_expected_sheets", [])),
            recommendation=str(data.get("recommendation", "")),
            validation_mode="llm",
            raw_llm_response=data,
        )
