# agents/cne_file_structure_agent.py

from __future__ import annotations

import json
from typing import Any, Dict

from openai import OpenAI

from config import Config


class CNEFileStructureAgent:
    """OpenAI-only agent that validates CNE workbook structure."""

    RESPONSE_SCHEMA = {
        "name": "cne_file_structure_validation",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["valid", "review_required", "invalid"],
                },
                "is_cne_file": {"type": "boolean"},
                "is_parse_ready": {"type": "boolean"},
                "confidence": {"type": "number"},
                "summary": {"type": "string"},
                "structural_changes": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "blocking_issues": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "warnings": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "parser_impact": {"type": "string"},
                "recommendation": {"type": "string"},
            },
            "required": [
                "status",
                "is_cne_file",
                "is_parse_ready",
                "confidence",
                "summary",
                "structural_changes",
                "blocking_issues",
                "warnings",
                "parser_impact",
                "recommendation",
            ],
        },
    }

    def __init__(self, model: str | None = None):
        Config.validate_openai()
        self.client = OpenAI(api_key=Config.OPENAI_API_KEY)
        self.model = model or Config.OPENAI_MODEL

    def validate(self, structure_report: Dict[str, Any]) -> Dict[str, Any]:
        """Validate whether the workbook is CNE-like and parser-ready."""
        prompt = self._build_prompt(structure_report)

        response = self.client.responses.create(
            model=self.model,
            input=[
                {
                    "role": "system",
                    "content": (
                        "You are a data ingestion validation agent for a Chilean "
                        "energy project database. Your job is to inspect an Excel "
                        "workbook structure report and decide whether it appears to "
                        "be a CNE Declaración de Construcción workbook and whether "
                        "it is structurally ready for the deterministic parser. "
                        "Do not invent missing data. Focus on sheets, columns, "
                        "recognized fields, row counts, and structural changes."
                    ),
                },
                {
                    "role": "user",
                    "content": prompt,
                },
            ],
            text={
                "format": {
                    "type": "json_schema",
                    "name": self.RESPONSE_SCHEMA["name"],
                    "strict": True,
                    "schema": self.RESPONSE_SCHEMA["schema"],
                }
            },
        )

        return json.loads(response.output_text)

    @staticmethod
    def _build_prompt(structure_report: Dict[str, Any]) -> str:
        return (
            "Evaluate this Excel workbook structure report.\n\n"
            "Validation goals:\n"
            "1. Decide if the file appears to be a CNE Declaración de Construcción workbook.\n"
            "2. Decide if it is structurally ready for the deterministic ProjectParser.\n"
            "3. Identify structural changes, missing sheets, missing fields, suspicious row counts, or header shifts.\n"
            "4. Return valid if it can continue to ProjectParser, review_required if a human should review first, or invalid if it should be blocked.\n\n"
            "Structure report JSON:\n"
            f"{json.dumps(structure_report, ensure_ascii=False, indent=2)}"
        )
