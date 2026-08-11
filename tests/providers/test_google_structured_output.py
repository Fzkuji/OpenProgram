from openprogram.providers.google import google
from openprogram.providers.google.google import _build_config
from openprogram.providers.structured_output import normalize_response_format
from openprogram.providers.types import Context, Model, SimpleStreamOptions


SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "integer"}},
    "required": ["answer"],
    "additionalProperties": False,
}


def test_google_maps_schema_to_literal_generation_config_without_mutation():
    model = Model(
        id="gemini-test",
        name="Gemini test",
        api="google-generative-ai",
        provider="google",
        base_url="https://generativelanguage.googleapis.com/v1beta",
    )
    output = normalize_response_format(SCHEMA)

    config = _build_config(
        model,
        Context(),
        SimpleStreamOptions(response_format=output),
    )
    wire = config.model_dump(exclude_none=True, by_alias=True)

    assert wire["responseMimeType"] == "application/json"
    assert wire["responseJsonSchema"] == SCHEMA
    assert output.schema == SCHEMA


def test_google_preserves_incomplete_and_refusal_finish_reasons():
    assert google._map_finish_reason("MAX_TOKENS") == "length"
    assert google._map_finish_reason("SAFETY") == "error"
