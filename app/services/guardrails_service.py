"""
H-1 Bedrock Guardrails service wrapper.

Thin wrapper around the bedrock-runtime ApplyGuardrail API.
Accepts plain text plus a Guardrail identifier and returns a normalized
GuardrailAssessmentResult — the raw AWS response shape never escapes this module.

Public surface:
  GuardrailsService        — callers use assess_text()
  GuardrailsServiceError   — raised on any SDK failure or unexpected response shape

Design constraints:
  - No business-policy logic; decisions belong in the H-1 adapter layer.
  - boto3 client is injected via constructor so tests can mock it without
    live AWS credentials.
  - Response normalisation extracts finding_types from all supported
    assessment sub-policies (topic, content, word, sensitive-info,
    contextual-grounding).
  - Trace payload is attached when include_trace=True; omitted otherwise.
"""

from __future__ import annotations

import os
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from app.schemas.guardrail_models import GuardrailAssessmentResult, GuardrailSource

# Raw action string returned by the API when the Guardrail intervened.
_ACTION_INTERVENED = "GUARDRAIL_INTERVENED"


class GuardrailsServiceError(Exception):
    """Raised when the ApplyGuardrail call or response normalisation fails."""


class GuardrailsService:
    """
    Thin wrapper around the Bedrock ApplyGuardrail API.

    Callers interact only through assess_text(); all AWS-specific request
    construction and response normalisation are contained here.

    Constructor parameters
    ----------------------
    region       : AWS region; defaults to AWS_REGION env var or "us-east-1".
    client       : boto3 bedrock-runtime client; injected for testability.
                   When omitted a real boto3 client is created at instantiation.

    Usage
    -----
    service = GuardrailsService()
    result = service.assess_text(
        text="Some user input to check",
        guardrail_id="my-guardrail-id",
        guardrail_version="1",
        source=GuardrailSource.INPUT,
    )
    """

    def __init__(
        self,
        *,
        region: str | None = None,
        client: Any = None,
    ) -> None:
        self._client = client or boto3.client(
            "bedrock-runtime",
            region_name=region or os.getenv("AWS_REGION", "us-east-1"),
        )

    # ── public interface ───────────────────────────────────────────────────────

    def assess_text(
        self,
        text: str,
        guardrail_id: str,
        guardrail_version: str,
        source: GuardrailSource,
        *,
        include_trace: bool = False,
    ) -> GuardrailAssessmentResult:
        """
        Apply a Bedrock Guardrail to the supplied text and return a normalized result.

        Parameters
        ----------
        text              : plain text to be assessed.
        guardrail_id      : Bedrock Guardrail identifier.
        guardrail_version : Guardrail version string (e.g. "1", "DRAFT").
        source            : GuardrailSource.INPUT or GuardrailSource.OUTPUT.
        include_trace     : when True, the raw assessments payload from the API
                            response is attached to the result's trace field.

        Returns
        -------
        GuardrailAssessmentResult with all finding types extracted.

        Raises
        ------
        GuardrailsServiceError on any SDK-level failure or unexpected response shape.
        """
        raw_response = self._call_apply_guardrail(
            text=text,
            guardrail_id=guardrail_id,
            guardrail_version=guardrail_version,
            source=source,
        )
        return _normalize_response(
            raw=raw_response,
            guardrail_id=guardrail_id,
            guardrail_version=guardrail_version,
            source=source,
            include_trace=include_trace,
        )

    # ── private helpers ────────────────────────────────────────────────────────

    def _call_apply_guardrail(
        self,
        text: str,
        guardrail_id: str,
        guardrail_version: str,
        source: GuardrailSource,
    ) -> dict[str, Any]:
        """
        Invoke ApplyGuardrail and return the raw response dict.

        Raises GuardrailsServiceError on any SDK failure so boto3 exceptions
        never propagate to callers.
        """
        try:
            return self._client.apply_guardrail(
                guardrailIdentifier=guardrail_id,
                guardrailVersion=guardrail_version,
                source=source.value.upper(),
                content=[{"text": {"text": text}}],
            )
        except (BotoCoreError, ClientError) as exc:
            raise GuardrailsServiceError(
                f"ApplyGuardrail API call failed: {exc}"
            ) from exc


# ── response normalisation ────────────────────────────────────────────────────
#
# Module-level functions so they can be unit-tested independently.


def _normalize_response(
    raw: dict[str, Any],
    guardrail_id: str,
    guardrail_version: str,
    source: GuardrailSource,
    include_trace: bool,
) -> GuardrailAssessmentResult:
    """
    Translate a raw ApplyGuardrail response dict into a GuardrailAssessmentResult.

    Raises GuardrailsServiceError when the response is missing the 'action' key.
    """
    try:
        action = raw["action"]
    except (KeyError, TypeError) as exc:
        raise GuardrailsServiceError(
            f"Unexpected ApplyGuardrail response shape — missing 'action': {exc}"
        ) from exc

    intervened = action == _ACTION_INTERVENED
    blocked = intervened

    output_text = _extract_output_text(raw)
    finding_types = _extract_finding_types(raw)
    trace: dict | None = (
        {"assessments": raw.get("assessments")} if include_trace else None
    )

    return GuardrailAssessmentResult(
        guardrail_id=guardrail_id,
        guardrail_version=guardrail_version,
        source=source,
        intervened=intervened,
        action=action,
        output_text=output_text,
        blocked=blocked,
        finding_types=finding_types,
        trace=trace,
    )


def _extract_output_text(raw: dict[str, Any]) -> str | None:
    """
    Pull the first output text string from the 'outputs' list, if present.

    The ApplyGuardrail API returns modified text in:
      response["outputs"][0]["text"]
    """
    outputs = raw.get("outputs") or []
    if outputs and isinstance(outputs[0], dict):
        return outputs[0].get("text")
    return None


def _extract_finding_types(raw: dict[str, Any]) -> list[str]:
    """
    Collect human-readable finding labels from all assessment sub-policies.

    Supported sub-policies:
      topicPolicy.topics              — topic name strings
      contentPolicy.filters           — filter type strings
      wordPolicy.customWords          — custom word match strings
      wordPolicy.managedWordLists     — managed word list type strings
      sensitiveInformationPolicy.piiEntities  — PII entity type strings
      sensitiveInformationPolicy.regexes      — regex name strings
      contextualGroundingPolicy.filters       — grounding filter type strings

    Only entries with action != "NONE" are included so non-triggering
    checks do not pollute the finding list.
    """
    findings: list[str] = []
    assessments = raw.get("assessments") or []

    for assessment in assessments:
        if not isinstance(assessment, dict):
            continue
        findings.extend(_topic_findings(assessment))
        findings.extend(_content_filter_findings(assessment))
        findings.extend(_word_policy_findings(assessment))
        findings.extend(_sensitive_info_findings(assessment))
        findings.extend(_contextual_grounding_findings(assessment))

    return findings


def _topic_findings(assessment: dict[str, Any]) -> list[str]:
    topics = (assessment.get("topicPolicy") or {}).get("topics") or []
    return [
        t["name"]
        for t in topics
        if isinstance(t, dict) and t.get("action", "NONE") != "NONE"
    ]


def _content_filter_findings(assessment: dict[str, Any]) -> list[str]:
    filters = (assessment.get("contentPolicy") or {}).get("filters") or []
    return [
        f["type"]
        for f in filters
        if isinstance(f, dict) and f.get("action", "NONE") != "NONE"
    ]


def _word_policy_findings(assessment: dict[str, Any]) -> list[str]:
    word_policy = assessment.get("wordPolicy") or {}
    custom = word_policy.get("customWords") or []
    managed = word_policy.get("managedWordLists") or []
    results: list[str] = []
    results.extend(
        w["match"]
        for w in custom
        if isinstance(w, dict) and w.get("action", "NONE") != "NONE"
    )
    results.extend(
        w["type"]
        for w in managed
        if isinstance(w, dict) and w.get("action", "NONE") != "NONE"
    )
    return results


def _sensitive_info_findings(assessment: dict[str, Any]) -> list[str]:
    sip = assessment.get("sensitiveInformationPolicy") or {}
    pii = sip.get("piiEntities") or []
    regexes = sip.get("regexes") or []
    results: list[str] = []
    results.extend(
        p["type"]
        for p in pii
        if isinstance(p, dict) and p.get("action", "NONE") != "NONE"
    )
    results.extend(
        r["name"]
        for r in regexes
        if isinstance(r, dict) and r.get("action", "NONE") != "NONE"
    )
    return results


def _contextual_grounding_findings(assessment: dict[str, Any]) -> list[str]:
    cg = assessment.get("contextualGroundingPolicy") or {}
    filters = cg.get("filters") or []
    return [
        f["type"]
        for f in filters
        if isinstance(f, dict) and f.get("action", "NONE") != "NONE"
    ]
