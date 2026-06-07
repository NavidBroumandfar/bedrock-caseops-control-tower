"""
Runtime dependency factory shared by CLI and Lambda entry points.

The orchestration workflow itself accepts injected dependencies.  This module
owns the concrete Bedrock, Knowledge Base, and agent construction used by live
runtime surfaces so the CLI and Lambda handler stay aligned.
"""

from __future__ import annotations

from app.utils.config import (
    PipelineConfig,
    load_pipeline_config,
    load_prompt_caching_config,
    load_prompt_routing_config,
)


class RuntimeDependencyError(Exception):
    """Raised when live pipeline dependencies cannot be constructed."""


def build_pipeline_dependencies(
    *,
    runtime_config: PipelineConfig | None = None,
):
    """
    Build and wire all pipeline service dependencies.

    Returns a 4-tuple:
      (retrieval_provider, analysis_agent, validation_agent, tool_executor)

    Live connectivity is not checked here.  AWS failures surface when the
    pipeline first calls each service.
    """
    from app.agents.analysis_agent import AnalysisAgent
    from app.agents.tool_executor_agent import ToolExecutorAgent
    from app.agents.validation_agent import ValidationAgent
    from app.services.bedrock_service import (
        BedrockAnalysisService,
        BedrockValidationService,
    )
    from app.services.kb_service import BedrockKBService, RetrievalServiceError

    runtime_config = runtime_config or load_pipeline_config()
    caching_config = load_prompt_caching_config()
    routing_config = load_prompt_routing_config()

    try:
        retrieval_provider = BedrockKBService(
            kb_id=runtime_config.bedrock_kb_id,
            region=runtime_config.aws_region,
            max_results=runtime_config.retrieval_max_results,
        )
    except RetrievalServiceError as exc:
        raise RuntimeDependencyError(
            f"Knowledge Base configuration error: {exc}\n"
            "Ensure BEDROCK_KB_ID is set in your environment or .env file."
        ) from exc

    analysis_service = BedrockAnalysisService(
        model_id=runtime_config.bedrock_model_id,
        region=runtime_config.aws_region,
        caching_config=caching_config,
        routing_config=routing_config,
    )
    validation_service = BedrockValidationService(
        model_id=runtime_config.bedrock_model_id,
        region=runtime_config.aws_region,
        caching_config=caching_config,
        routing_config=routing_config,
    )

    return (
        retrieval_provider,
        AnalysisAgent(provider=analysis_service),
        ValidationAgent(provider=validation_service),
        ToolExecutorAgent(
            escalation_confidence_threshold=runtime_config.escalation_confidence_threshold
        ),
    )
