"""
Explicit live AWS smoke test for Bedrock Knowledge Base retrieval.

This script is intentionally skipped unless CASEOPS_ENABLE_LIVE_AWS_SMOKE=true
or --force is passed. It validates only the external prerequisites needed before
the full CaseOps CLI run: AWS identity, Knowledge Base status, data sources, and
one direct Retrieve call.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from dotenv import find_dotenv, load_dotenv


DEFAULT_QUERY = (
    "FDA warning letter quality system deficiencies corrective preventive action "
    "cleaning disinfection validation"
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run an explicit live Bedrock Knowledge Base retrieval smoke test."
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Run live AWS calls even when CASEOPS_ENABLE_LIVE_AWS_SMOKE is not true.",
    )
    parser.add_argument(
        "--query",
        default=os.getenv("CASEOPS_SMOKE_QUERY", DEFAULT_QUERY),
        help="Retrieval query text.",
    )
    parser.add_argument(
        "--source-type",
        default=os.getenv("CASEOPS_SMOKE_SOURCE_TYPE", "FDA"),
        choices=["FDA", "CISA", "Incident", "Other"],
        help="Source type metadata value to filter on when filtering is enabled.",
    )
    args = parser.parse_args()

    load_dotenv(find_dotenv(usecwd=True), override=False)

    enabled = os.getenv("CASEOPS_ENABLE_LIVE_AWS_SMOKE", "").lower() == "true"
    if not enabled and not args.force:
        print(
            "[skip] Live AWS smoke test disabled. Set "
            "CASEOPS_ENABLE_LIVE_AWS_SMOKE=true or pass --force."
        )
        return 0

    region = os.getenv("AWS_REGION", "").strip()
    kb_id = os.getenv("BEDROCK_KB_ID", "").strip()
    max_results = int(os.getenv("RETRIEVAL_MAX_RESULTS", "5"))

    missing = [name for name, value in {"AWS_REGION": region, "BEDROCK_KB_ID": kb_id}.items() if not value]
    if missing:
        print(f"[fail] Missing required environment variables: {', '.join(missing)}", file=sys.stderr)
        return 1

    try:
        summary = run_smoke(
            region=region,
            kb_id=kb_id,
            query=args.query,
            max_results=max_results,
            source_type=args.source_type,
            enable_source_type_filter=_read_bool_env(
                "CASEOPS_ENABLE_SOURCE_TYPE_FILTER",
                default=False,
            ),
        )
    except (BotoCoreError, ClientError, ValueError) as exc:
        print(f"[fail] Live KB smoke test failed: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(summary, indent=2, sort_keys=True, default=str))
    if summary["retrieved_count"] < 1:
        print("[fail] Retrieve returned zero results.", file=sys.stderr)
        return 1

    print("[ok] Live KB smoke test passed.")
    return 0


def run_smoke(
    *,
    region: str,
    kb_id: str,
    query: str,
    max_results: int,
    source_type: str,
    enable_source_type_filter: bool,
) -> dict[str, Any]:
    if max_results < 1:
        raise ValueError("RETRIEVAL_MAX_RESULTS must be a positive integer")

    sts = boto3.client("sts", region_name=region)
    bedrock_agent = boto3.client("bedrock-agent", region_name=region)
    bedrock_runtime = boto3.client("bedrock-agent-runtime", region_name=region)

    started = time.perf_counter()
    identity = sts.get_caller_identity()
    kb = bedrock_agent.get_knowledge_base(knowledgeBaseId=kb_id)["knowledgeBase"]
    data_sources = bedrock_agent.list_data_sources(knowledgeBaseId=kb_id)[
        "dataSourceSummaries"
    ]

    vector_search_config: dict[str, Any] = {"numberOfResults": max_results}
    if enable_source_type_filter:
        vector_search_config["filter"] = {
            "equals": {
                "key": "source_type",
                "value": source_type,
            }
        }

    response = bedrock_runtime.retrieve(
        knowledgeBaseId=kb_id,
        retrievalQuery={"text": query},
        retrievalConfiguration={"vectorSearchConfiguration": vector_search_config},
    )
    elapsed_ms = round((time.perf_counter() - started) * 1000)
    results = response.get("retrievalResults", [])

    return {
        "account": identity.get("Account"),
        "caller_arn": identity.get("Arn"),
        "region": region,
        "knowledge_base_id": kb_id,
        "knowledge_base_name": kb.get("name"),
        "knowledge_base_status": kb.get("status"),
        "data_source_count": len(data_sources),
        "data_sources": [
            {
                "data_source_id": item.get("dataSourceId"),
                "name": item.get("name"),
                "status": item.get("status"),
            }
            for item in data_sources
        ],
        "query": query,
        "source_type_filter_enabled": enable_source_type_filter,
        "source_type": source_type if enable_source_type_filter else None,
        "retrieved_count": len(results),
        "top_sources": [
            _source_uri(item)
            for item in results[: min(3, len(results))]
        ],
        "elapsed_ms": elapsed_ms,
    }


def _source_uri(item: dict[str, Any]) -> str | None:
    location = item.get("location", {})
    if location.get("type") == "S3":
        return location.get("s3Location", {}).get("uri")
    return None


def _read_bool_env(name: str, *, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default

    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean value, got {raw!r}")


if __name__ == "__main__":
    raise SystemExit(main())
