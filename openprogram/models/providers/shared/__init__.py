"""Shared helpers used by multiple provider stream implementations.

Submodules:
  openai_responses — OpenAI Responses API message/tool conversion and stream processing
  google           — Google (Gemini) message/tool conversion
  simple_options   — Common option building/clamping for many providers
  transform_messages — Cross-provider message transform

Import submodules directly; this package level stays intentionally thin.
"""
