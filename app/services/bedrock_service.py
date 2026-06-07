"""
Bedrock Converse service layer — C-1 (analysis) + C-2 (validation).

Thin wrappers around the bedrock-runtime boto3 client.
Implements AnalysisProvider (analysis_contract.py) and ValidationProvider (validation_contract.py).

Public surface:
  BedrockAnalysisService    — callers use analyze()
  BedrockValidationService  — callers use validate()
  BedrockServiceError       — raised on any SDK failure, response shape error, or parse failure

Raw Bedrock response shapes are never exposed to callers.
All prompt construction and response parsing happen inside this module.

Prompt caching (I-0):
  Both services accept an optional ``caching_config`` constructor parameter.
  When supplied and enabled, system blocks are passed through
  ``apply_prompt_caching`` before the Converse call so Bedrock can cache the
  system prompt across repeated invocations.  When absent or disabled, the
  request is constructed identically to the pre-I-0 behaviour.

Prompt routing (I-1):
  Both services accept an optional ``routing_config`` constructor parameter.
  When supplied and routing is enabled, the effective model ID is resolved at
  construction time via ``resolve_model_id`` from ``prompt_router``.  The
  analysis service always resolves using the "analysis" route; the validation
  service uses "validation".  When absent or disabled, model_id resolution is
  identical to pre-I-1 behaviour — no routing logic is applied.
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
from app.schemas.validation_contract import ValidationProvider
from app.schemas.validation_models import ValidationOutput
from app.services.prompt_cache import apply_prompt_caching
from app.services.prompt_router import resolve_model_id
from app.utils.config import PromptCachingConfig, PromptRoutingConfig

# Keys the model must return in its JSON response.
_REQUIRED_JSON_KEYS = {
    "severity",
    "category",
    "summary",
    "recommendations",
    "grounded_claims",
}
_VALIDATION_REQUIRED_JSON_KEYS = {
    "confidence_score",
    "unsupported_claims",
    "validation_status",
    "claim_validations",
}

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

    Optional:
      caching_config    — when supplied and enabled, injects a cachePoint into
                          the system block before each Converse call (I-0).
                          When absent or disabled, behaviour is unchanged.
      routing_config    — when supplied and routing is enabled, the effective
                          model ID is resolved via the "analysis" route at
                          construction time (I-1).  When absent or disabled,
                          model_id resolution is unchanged from pre-I-1.
    """

    def __init__(
        self,
        *,
        model_id: str | None = None,
        region: str | None = None,
        client: Any = None,
        caching_config: PromptCachingConfig | None = None,
        routing_config: PromptRoutingConfig | None = None,
    ) -> None:
        base_model_id = model_id or os.getenv("BEDROCK_MODEL_ID", _DEFAULT_MODEL_ID)
        self._model_id = (
            resolve_model_id("analysis", routing_config, base_model_id)
            if routing_config is not None
            else base_model_id
        )
        self._client = client or boto3.client(
            "bedrock-runtime",
            region_name=region or os.getenv("AWS_REGION", "us-east-1"),
        )
        self._caching_config = caching_config

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
        return _parse_analysis_output(document_id, raw_text, evidence_chunks)

    # ── private helpers ────────────────────────────────────────────────────────

    def _call_converse(self, system_prompt: str, user_message: str) -> str:
        """
        Invoke the Converse API and return the raw text content of the model's reply.

        System blocks are passed through apply_prompt_caching before the call;
        when caching is disabled the blocks are returned unchanged so there is
        no observable difference in the request for callers without caching.

        Raises BedrockServiceError on any SDK-level failure so boto3 exceptions
        never propagate to callers.
        """
        system_blocks: list[dict[str, Any]] = [{"text": system_prompt}]
        if self._caching_config is not None:
            system_blocks = apply_prompt_caching(system_blocks, self._caching_config)

        try:
            response = self._client.converse(
                modelId=self._model_id,
                system=system_blocks,
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
        "- The JSON must contain exactly these five keys:\n"
        '  "severity": one of exactly "Critical", "High", "Medium", or "Low"\n'
        '  "category": a short, descriptive label '
        '(e.g. "Regulatory / Manufacturing Deficiency")\n'
        '  "summary": a concise one-paragraph summary of the key findings from the evidence\n'
        '  "recommendations": a JSON array of concrete, actionable recommendation strings\n'
        '  "grounded_claims": a JSON array of claim objects, where each object has '
        '"claim_id", "claim_type", "text", and "supporting_chunk_ids"\n'
        '- "claim_type" must be exactly "finding" or "recommendation".\n'
        "- Every material finding in the summary and every recommendation must appear "
        "as a grounded_claim.\n"
        "- supporting_chunk_ids must use the exact chunk_id values shown in the evidence "
        "chunks. Do not use source labels, source IDs, or bracket numbers as chunk IDs.\n"
        "- Every grounded_claim must include at least one supporting_chunk_id.\n"
        "- If the evidence does not support a specific finding, "
        "state that clearly in the summary.\n"
        "- Do not include any other keys or text outside the JSON object."
    )


def _build_user_message(document_id: str, evidence_chunks: list[EvidenceChunk]) -> str:
    lines = [f"Document ID: {document_id}", "", "Evidence chunks:"]
    for i, chunk in enumerate(evidence_chunks, start=1):
        lines.append(f"[{i}] chunk_id: {chunk.chunk_id} (source: {chunk.source_label})")
        lines.append(chunk.text)
        lines.append("")
    lines.append("Analyze the evidence above and respond with JSON only.")
    return "\n".join(lines)


# ── response parsing ────────────────────────────────────────────────────────────
#
# These functions translate raw model output into the typed AnalysisOutput contract.
# All provider-specific parsing logic is contained here.


def _parse_analysis_output(
    document_id: str,
    raw_text: str,
    evidence_chunks: list[EvidenceChunk] | None = None,
) -> AnalysisOutput:
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
        output = AnalysisOutput(
            document_id=document_id,
            severity=data["severity"],
            category=data["category"],
            summary=data["summary"],
            recommendations=data["recommendations"],
            grounded_claims=data["grounded_claims"],
        )
    except ValidationError as exc:
        raise BedrockServiceError(
            f"Model response failed AnalysisOutput validation: {exc}"
        ) from exc

    if evidence_chunks is not None:
        _validate_claim_chunk_ids(
            context="AnalysisOutput.grounded_claims",
            referenced_chunk_ids=[
                chunk_id
                for claim in output.grounded_claims
                for chunk_id in claim.supporting_chunk_ids
            ],
            evidence_chunks=evidence_chunks,
        )

    return output


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


def _validate_claim_chunk_ids(
    *,
    context: str,
    referenced_chunk_ids: list[str],
    evidence_chunks: list[EvidenceChunk],
) -> None:
    """Reject claim references to chunks outside the retrieved evidence set."""
    valid_chunk_ids = {chunk.chunk_id for chunk in evidence_chunks}
    unknown = sorted(
        {
            chunk_id
            for chunk_id in referenced_chunk_ids
            if chunk_id not in valid_chunk_ids
        }
    )
    if unknown:
        raise BedrockServiceError(
            f"{context} references unknown evidence chunk IDs: {unknown}. "
            f"Known chunk IDs: {sorted(valid_chunk_ids)}"
        )


# ── validation service ──────────────────────────────────────────────────────────


class BedrockValidationService:
    """
    Validation service backed by the Amazon Bedrock Converse API.

    Satisfies the ValidationProvider protocol — callers interact only through
    validate(document_id, analysis_output, evidence_chunks) → ValidationOutput.

    Constructor parameters mirror BedrockAnalysisService so both services can be
    instantiated the same way in tests and in the agent layer.

    Required configuration:
      AWS_REGION        — AWS region (default: us-east-1)
      BEDROCK_MODEL_ID  — model identifier (default: claude-3-haiku)

    Optional:
      caching_config    — see BedrockAnalysisService for details (I-0).
      routing_config    — when supplied and routing is enabled, the effective
                          model ID is resolved via the "validation" route at
                          construction time (I-1).  When absent or disabled,
                          model_id resolution is unchanged from pre-I-1.
    """

    def __init__(
        self,
        *,
        model_id: str | None = None,
        region: str | None = None,
        client: Any = None,
        caching_config: PromptCachingConfig | None = None,
        routing_config: PromptRoutingConfig | None = None,
    ) -> None:
        base_model_id = model_id or os.getenv("BEDROCK_MODEL_ID", _DEFAULT_MODEL_ID)
        self._model_id = (
            resolve_model_id("validation", routing_config, base_model_id)
            if routing_config is not None
            else base_model_id
        )
        self._client = client or boto3.client(
            "bedrock-runtime",
            region_name=region or os.getenv("AWS_REGION", "us-east-1"),
        )
        self._caching_config = caching_config

    # ── public interface ───────────────────────────────────────────────────────

    def validate(
        self,
        document_id: str,
        analysis_output: AnalysisOutput,
        evidence_chunks: list[EvidenceChunk],
    ) -> ValidationOutput:
        """
        Invoke the Bedrock Converse API to audit an AnalysisOutput against evidence chunks.

        Raises BedrockServiceError on any SDK failure, unexpected response shape,
        or model output that cannot be parsed into ValidationOutput.
        """
        system_prompt = _build_validation_system_prompt()
        user_message = _build_validation_user_message(document_id, analysis_output, evidence_chunks)
        raw_text = self._call_converse(system_prompt, user_message)
        return _parse_validation_output(
            document_id,
            raw_text,
            analysis_output,
            evidence_chunks,
        )

    # ── private helpers ────────────────────────────────────────────────────────

    def _call_converse(self, system_prompt: str, user_message: str) -> str:
        """
        Invoke the Converse API and return the raw text content of the model's reply.

        System blocks are passed through apply_prompt_caching before the call;
        when caching is disabled the blocks are returned unchanged.

        Raises BedrockServiceError on any SDK-level failure so boto3 exceptions
        never propagate to callers.
        """
        system_blocks: list[dict[str, Any]] = [{"text": system_prompt}]
        if self._caching_config is not None:
            system_blocks = apply_prompt_caching(system_blocks, self._caching_config)

        try:
            response = self._client.converse(
                modelId=self._model_id,
                system=system_blocks,
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


# ── validation prompt construction ──────────────────────────────────────────────
#
# Module-level functions so they can be unit-tested independently.


def _build_validation_system_prompt() -> str:
    return (
        "You are a critic agent. Your task is to audit an analysis output strictly "
        "against the provided evidence chunks.\n\n"
        "Rules:\n"
        "- Do NOT rewrite the analysis.\n"
        "- Do NOT add new recommendations.\n"
        "- Do NOT invent supporting evidence that is not in the provided chunks.\n"
        "- For each grounded_claim, determine whether the claim text is supported by "
        "the cited supporting_chunk_ids.\n"
        "- Do not use uncited chunks to rescue a claim. If a claim is true only when "
        "uncited chunks are considered, mark it unsupported and explain the citation gap.\n"
        "- Also audit the legacy summary and recommendations for unsupported claims.\n"
        "- Respond with a JSON object and nothing else — "
        "no markdown, no explanation, no preamble.\n"
        "- The JSON must contain these four required keys and may include a warning key:\n"
        '  "confidence_score": a float between 0.0 and 1.0 representing overall grounding '
        "confidence (1.0 = all claims fully supported, 0.0 = no claims supported)\n"
        '  "unsupported_claims": a JSON array of strings, each naming a specific claim '
        "not supported by any evidence chunk; use an empty array if all claims are grounded\n"
        '  "validation_status": one of exactly "pass", "warning", or "fail"\n'
        '  "claim_validations": a JSON array with one object per grounded_claim, where each '
        'object has "claim_id", "supported", "supporting_chunk_ids", and '
        '"unsupported_reason"\n'
        "- For supported claim_validations, supporting_chunk_ids must include at least "
        "one chunk ID that supports the claim and unsupported_reason must be null.\n"
        "- For unsupported claim_validations, supported must be false and "
        "unsupported_reason must explain the gap.\n"
        '    - "pass": all claims grounded and confidence_score >= 0.8\n'
        '    - "warning": minor gaps or confidence_score between 0.5 and 0.8\n'
        '    - "fail": one or more claims unsupported or confidence_score < 0.5\n'
        "- Do not include any other keys or text outside the JSON object."
    )


def _build_validation_user_message(
    document_id: str,
    analysis_output: AnalysisOutput,
    evidence_chunks: list[EvidenceChunk],
) -> str:
    lines = [
        f"Document ID: {document_id}",
        "",
        "Analysis to audit:",
        f"Severity: {analysis_output.severity}",
        f"Category: {analysis_output.category}",
        f"Summary: {analysis_output.summary}",
        "Recommendations:",
    ]
    for i, rec in enumerate(analysis_output.recommendations, start=1):
        lines.append(f"  {i}. {rec}")
    lines.append("")
    lines.append("Grounded claims to audit:")
    if analysis_output.grounded_claims:
        for claim in analysis_output.grounded_claims:
            chunk_ids = ", ".join(claim.supporting_chunk_ids)
            lines.append(
                f"  - claim_id={claim.claim_id}; claim_type={claim.claim_type}; "
                f"supporting_chunk_ids=[{chunk_ids}]"
            )
            lines.append(f"    text: {claim.text}")
    else:
        lines.append("  (none provided)")
    lines.append("")
    lines.append("Evidence chunks available:")
    for i, chunk in enumerate(evidence_chunks, start=1):
        lines.append(f"[{i}] chunk_id: {chunk.chunk_id} (source: {chunk.source_label})")
        lines.append(chunk.text)
        lines.append("")
    lines.append(
        "Audit every claim in the analysis strictly against the evidence above. "
        "Respond with JSON only."
    )
    return "\n".join(lines)


# ── validation response parsing ─────────────────────────────────────────────────


def _parse_validation_output(
    document_id: str,
    raw_text: str,
    analysis_output: AnalysisOutput | None = None,
    evidence_chunks: list[EvidenceChunk] | None = None,
) -> ValidationOutput:
    """
    Extract and validate the model's JSON response into a ValidationOutput.

    Raises BedrockServiceError if the text is not valid JSON, is missing required
    keys, or fails Pydantic validation.  document_id is injected by the service.
    """
    json_text = _extract_json(raw_text)

    try:
        data = json.loads(json_text)
    except json.JSONDecodeError as exc:
        raise BedrockServiceError(
            f"Validation model response is not valid JSON: {exc}\nRaw text: {raw_text!r}"
        ) from exc

    if not isinstance(data, dict):
        raise BedrockServiceError(
            f"Validation model response parsed as {type(data).__name__}, expected a JSON object. "
            f"Raw text: {raw_text!r}"
        )

    missing = _VALIDATION_REQUIRED_JSON_KEYS - data.keys()
    if missing:
        raise BedrockServiceError(
            f"Validation model response is missing required keys: {sorted(missing)}. "
            f"Raw text: {raw_text!r}"
        )

    try:
        output = ValidationOutput(
            document_id=document_id,
            confidence_score=data["confidence_score"],
            unsupported_claims=data["unsupported_claims"],
            validation_status=data["validation_status"],
            claim_validations=data["claim_validations"],
            warning=data.get("warning"),
        )
    except ValidationError as exc:
        raise BedrockServiceError(
            f"Model response failed ValidationOutput validation: {exc}"
        ) from exc

    if analysis_output is not None:
        _validate_claim_validation_ids(output, analysis_output)

    if evidence_chunks is not None:
        _validate_claim_chunk_ids(
            context="ValidationOutput.claim_validations",
            referenced_chunk_ids=[
                chunk_id
                for claim_validation in output.claim_validations
                for chunk_id in claim_validation.supporting_chunk_ids
            ],
            evidence_chunks=evidence_chunks,
        )

    return output


def _validate_claim_validation_ids(
    validation_output: ValidationOutput,
    analysis_output: AnalysisOutput,
) -> None:
    expected_claim_ids = {claim.claim_id for claim in analysis_output.grounded_claims}
    actual_claim_ids = {
        claim_validation.claim_id
        for claim_validation in validation_output.claim_validations
    }

    if not expected_claim_ids and actual_claim_ids:
        raise BedrockServiceError(
            "ValidationOutput.claim_validations were returned even though "
            "AnalysisOutput.grounded_claims is empty."
        )

    unknown_claim_ids = sorted(actual_claim_ids - expected_claim_ids)
    if unknown_claim_ids:
        raise BedrockServiceError(
            f"ValidationOutput.claim_validations reference unknown claim IDs: "
            f"{unknown_claim_ids}. Known claim IDs: {sorted(expected_claim_ids)}"
        )

    missing_claim_ids = sorted(expected_claim_ids - actual_claim_ids)
    if missing_claim_ids:
        raise BedrockServiceError(
            f"ValidationOutput.claim_validations missing required claim IDs: "
            f"{missing_claim_ids}"
        )


# Enforce protocol satisfaction at import time.
# A failure here means BedrockAnalysisService has drifted from the AnalysisProvider contract.
assert isinstance(BedrockAnalysisService.__new__(BedrockAnalysisService), AnalysisProvider), (
    "BedrockAnalysisService does not satisfy the AnalysisProvider protocol"
)

# A failure here means BedrockValidationService has drifted from the ValidationProvider contract.
assert isinstance(BedrockValidationService.__new__(BedrockValidationService), ValidationProvider), (
    "BedrockValidationService does not satisfy the ValidationProvider protocol"
)
