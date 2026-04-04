"""
Bedrock Converse analysis service — C-1.

Thin wrapper around the bedrock-runtime boto3 client.
Implements the AnalysisProvider contract defined in app/schemas/analysis_contract.py.

Public surface:
  BedrockAnalysisService — the service class; callers use analyze()
  BedrockServiceError    — raised on any SDK failure, response shape error, or parse failure

Raw Bedrock response shapes are never exposed to callers.
All prompt construction and response parsing happen inside this module.
"""

import json
import os
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from pydantic import ValidationError

from app.schemas.analysis_contract import AnalysisProvider
from app.schemas.analysis_models import AnalysisOutput
from app.schemas.retrieval_models import EvidenceChunk

# Keys the model must return in its JSON response.
_REQUIRED_JSON_KEYS = {"severity", "category", "summary", "recommendations"}

# Conservative default; operators should pin this via BEDROCK_MODEL_ID.
_DEFAULT_MODEL_ID = "anthropic.claude-3-haiku-20240307-v1:0"


class BedrockServiceError(Exception):
    """Raised when the Bedrock Converse call or response parsing fails."""


class BedrockAnalysisService:
    """
    Analysis service backed by the Amazon Bedrock Converse API.

    Satisfies the AnalysisProvider protocol — callers interact only through
    analyze(document_id, evidence_chunks) → AnalysisOutput.

    All configuration is read from environment variables at instantiation time.
    Explicit constructor overrides are accepted so the service remains
    testable without live AWS credentials.

    Required configuration:
      AWS_REGION        — AWS region (default: us-east-1)
      BEDROCK_MODEL_ID  — model identifier (default: claude-3-haiku)
    """

    def __init__(
        self,
        *,
        model_id: str | None = None,
        region: str | None = None,
        client: Any = None,
    ) -> None:
        self._model_id = model_id or os.getenv("BEDROCK_MODEL_ID", _DEFAULT_MODEL_ID)
        self._client = client or boto3.client(
            "bedrock-runtime",
            region_name=region or os.getenv("AWS_REGION", "us-east-1"),
        )

    # ── public interface ───────────────────────────────────────────────────────

    def analyze(
        self,
        document_id: str,
        evidence_chunks: list[EvidenceChunk],
    ) -> AnalysisOutput:
        """
        Invoke the Bedrock Converse API with grounded evidence and return a typed AnalysisOutput.

        Raises BedrockServiceError on any SDK failure, unexpected response shape,
        or model output that cannot be parsed into AnalysisOutput.
        """
        system_prompt = _build_system_prompt()
        user_message = _build_user_message(document_id, evidence_chunks)
        raw_text = self._call_converse(system_prompt, user_message)
        return _parse_analysis_output(document_id, raw_text)

    # ── private helpers ────────────────────────────────────────────────────────

    def _call_converse(self, system_prompt: str, user_message: str) -> str:
        """
        Invoke the Converse API and return the raw text content of the model's reply.

        Raises BedrockServiceError on any SDK-level failure so boto3 exceptions
        never propagate to callers.
        """
        try:
            response = self._client.converse(
                modelId=self._model_id,
                system=[{"text": system_prompt}],
                messages=[
                    {"role": "user", "content": [{"text": user_message}]}
                ],
            )
        except (BotoCoreError, ClientError) as exc:
            raise BedrockServiceError(
                f"Bedrock Converse API call failed: {exc}"
            ) from exc

        try:
            return response["output"]["message"]["content"][0]["text"]
        except (KeyError, IndexError, TypeError) as exc:
            raise BedrockServiceError(
                f"Unexpected Bedrock Converse response shape: {exc}"
            ) from exc


# ── prompt construction ─────────────────────────────────────────────────────────
#
# Both functions are module-level (not methods) so they can be unit-tested
# independently and remain accessible to tests that import them directly.


def _build_system_prompt() -> str:
    return (
        "You are a document analysis agent. "
        "Your task is to analyze the evidence chunks provided and classify the document.\n\n"
        "Rules:\n"
        "- Use ONLY the provided evidence chunks. "
        "Do not introduce information from outside the evidence.\n"
        "- Respond with a JSON object and nothing else — "
        "no markdown, no explanation, no preamble.\n"
        "- The JSON must contain exactly these four keys:\n"
        '  "severity": one of exactly "Critical", "High", "Medium", or "Low"\n'
        '  "category": a short, descriptive label '
        '(e.g. "Regulatory / Manufacturing Deficiency")\n'
        '  "summary": a concise one-paragraph summary of the key findings from the evidence\n'
        '  "recommendations": a JSON array of concrete, actionable recommendation strings\n'
        "- If the evidence does not support a specific finding, "
        "state that clearly in the summary.\n"
        "- Do not include any other keys or text outside the JSON object."
    )


def _build_user_message(document_id: str, evidence_chunks: list[EvidenceChunk]) -> str:
    lines = [f"Document ID: {document_id}", "", "Evidence chunks:"]
    for i, chunk in enumerate(evidence_chunks, start=1):
        lines.append(f"[{i}] (source: {chunk.source_label})")
        lines.append(chunk.text)
        lines.append("")
    lines.append("Analyze the evidence above and respond with JSON only.")
    return "\n".join(lines)


# ── response parsing ────────────────────────────────────────────────────────────
#
# These functions translate raw model output into the typed AnalysisOutput contract.
# All provider-specific parsing logic is contained here.


def _parse_analysis_output(document_id: str, raw_text: str) -> AnalysisOutput:
    """
    Extract and validate the model's JSON response into an AnalysisOutput.

    Raises BedrockServiceError if the text is not valid JSON, is missing required
    keys, or fails Pydantic validation.  document_id is injected by the service —
    the model is not asked to repeat it back.
    """
    json_text = _extract_json(raw_text)

    try:
        data = json.loads(json_text)
    except json.JSONDecodeError as exc:
        raise BedrockServiceError(
            f"Model response is not valid JSON: {exc}\nRaw text: {raw_text!r}"
        ) from exc

    if not isinstance(data, dict):
        raise BedrockServiceError(
            f"Model response parsed as {type(data).__name__}, expected a JSON object. "
            f"Raw text: {raw_text!r}"
        )

    missing = _REQUIRED_JSON_KEYS - data.keys()
    if missing:
        raise BedrockServiceError(
            f"Model response is missing required keys: {sorted(missing)}. "
            f"Raw text: {raw_text!r}"
        )

    try:
        return AnalysisOutput(
            document_id=document_id,
            severity=data["severity"],
            category=data["category"],
            summary=data["summary"],
            recommendations=data["recommendations"],
        )
    except ValidationError as exc:
        raise BedrockServiceError(
            f"Model response failed AnalysisOutput validation: {exc}"
        ) from exc


def _extract_json(text: str) -> str:
    """
    Extract a JSON object from raw model output.

    Models sometimes wrap JSON in markdown code fences even when instructed not to.
    This strips common fence patterns before parsing so the downstream json.loads
    call receives clean input.
    """
    stripped = text.strip()
    if stripped.startswith("```"):
        end = stripped.rfind("```")
        if end > 3:
            inner = stripped[3:end].strip()
            # Remove optional language tag on the opening fence line (e.g. ```json)
            if inner.startswith("json"):
                inner = inner[4:].strip()
            return inner
    return stripped


# Enforce protocol satisfaction at import time.
# A failure here means BedrockAnalysisService has drifted from the AnalysisProvider contract.
assert isinstance(BedrockAnalysisService.__new__(BedrockAnalysisService), AnalysisProvider), (
    "BedrockAnalysisService does not satisfy the AnalysisProvider protocol"
)
