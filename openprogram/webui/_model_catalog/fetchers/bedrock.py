"""Amazon Bedrock ``list_foundation_models`` fetcher via boto3.

Uses the AWS credentials chain (env, profile, instance metadata, …)
so there's no API key to resolve here — Bedrock auth is the standard
SigV4 dance handled by boto3. We filter to text-in / text-out models;
image / embedding / video entries don't belong in the chat picker."""
from __future__ import annotations

from typing import Any


def _fetch_bedrock(provider_id: str, timeout: float) -> Any:
    try:
        import boto3
    except ImportError:
        return {"error": "boto3 not installed (pip install boto3)"}
    try:
        client = boto3.client("bedrock")
        resp = client.list_foundation_models()
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}
    out = []
    for m in resp.get("modelSummaries", []):
        mid = m.get("modelId")
        if not mid:
            continue
        # Filter to TEXT input/output models. Skip image/embedding.
        if "TEXT" not in (m.get("inputModalities") or []):
            continue
        if "TEXT" not in (m.get("outputModalities") or []):
            continue
        out.append({
            "id": mid,
            "name": m.get("modelName") or mid,
        })
    return out
