import json

import pytest

from openprogram.providers.amazon_bedrock import amazon_bedrock
from openprogram.providers.api_registry import get_structured_output_capabilities
from openprogram.providers.structured_output import (
    StructuredOutputUnsupportedError,
    negotiate_structured_output,
    normalize_response_format,
)
from openprogram.providers.types import Model


SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "integer"}},
    "required": ["answer"],
    "additionalProperties": False,
}


def test_bedrock_serializes_schema_in_literal_output_config_without_mutation():
    output = normalize_response_format({
        "type": "json_schema",
        "name": "answer",
        "description": "An answer",
        "schema": SCHEMA,
    })

    config = amazon_bedrock._build_output_config(output)

    assert config == {
        "textFormat": {
            "type": "json_schema",
            "structure": {
                "jsonSchema": {
                    "name": "answer",
                    "description": "An answer",
                    "schema": json.dumps(
                        SCHEMA,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                }
            },
        }
    }
    assert json.loads(config["textFormat"]["structure"]["jsonSchema"]["schema"]) == SCHEMA
    assert output.schema == SCHEMA


def test_installed_botocore_accepts_documented_output_config_shape():
    import boto3
    from botocore.validate import validate_parameters

    client = boto3.Session(
        aws_access_key_id="test",
        aws_secret_access_key="test",
        region_name="us-east-1",
    ).client("bedrock-runtime")
    shape = client.meta.service_model.operation_model("ConverseStream").input_shape
    validate_parameters({
        "modelId": "anthropic.claude-test-v1:0",
        "messages": [{"role": "user", "content": [{"text": "answer"}]}],
        "outputConfig": {
            "textFormat": {
                "type": "json_schema",
                "structure": {
                    "jsonSchema": {
                        "name": "answer",
                        "schema": json.dumps(SCHEMA),
                    }
                },
            }
        },
    }, shape)


def test_bedrock_native_requires_explicit_model_support_metadata():
    capabilities = get_structured_output_capabilities("bedrock-converse-stream")
    base = Model(
        id="anthropic.claude-test-v1:0",
        name="Claude test",
        api="bedrock-converse-stream",
        provider="amazon-bedrock",
        base_url="https://bedrock-runtime.us-east-1.amazonaws.com",
    )
    output = normalize_response_format({
        "type": "json_schema",
        "schema": SCHEMA,
        "fallback": "none",
    })

    assert capabilities.native == "unknown"
    with pytest.raises(StructuredOutputUnsupportedError):
        negotiate_structured_output(base, capabilities, output, [])
    assert negotiate_structured_output(
        base.model_copy(update={"structured_output": True}),
        capabilities,
        output,
        [],
    ).mode == "native"
