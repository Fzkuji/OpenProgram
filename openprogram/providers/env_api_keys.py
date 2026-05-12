"""
Environment variable API key resolution — mirrors packages/ai/src/env-api-keys.ts
"""
from __future__ import annotations

import os
from pathlib import Path

# Maps provider name → environment variable name
PROVIDER_ENV_VARS: dict[str, str] = {
    "openai": "OPENAI_API_KEY",
    "google": "GEMINI_API_KEY",
    "gemini-subscription": "GEMINI_API_KEY",
    "groq": "GROQ_API_KEY",
    "xai": "XAI_API_KEY",
    "mistral": "MISTRAL_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "vercel-ai-gateway": "AI_GATEWAY_API_KEY",  # Changed from VERCEL_AI_GATEWAY_API_KEY
    "azure-openai-responses": "AZURE_OPENAI_API_KEY",
    "cerebras": "CEREBRAS_API_KEY",
    "zai": "ZAI_API_KEY",
    "huggingface": "HF_TOKEN",
    "minimax": "MINIMAX_API_KEY",
    "minimax-cn": "MINIMAX_CN_API_KEY",
    "opencode": "OPENCODE_API_KEY",
    "opencode-go": "OPENCODE_API_KEY",
    "kimi-coding": "KIMI_API_KEY",
    "kimi": "KIMI_API_KEY",
    "moonshot": "MOONSHOT_API_KEY",
}


def get_env_api_key(provider: str) -> str | None:
    """Resolve an API key from environment variables for the given provider."""
    
    # GitHub Copilot: check multiple tokens with priority
    if provider == "github-copilot":
        return (
            os.environ.get("COPILOT_GITHUB_TOKEN")
            or os.environ.get("GH_TOKEN")
            or os.environ.get("GITHUB_TOKEN")
        )
    
    # Anthropic: ANTHROPIC_OAUTH_TOKEN takes precedence over ANTHROPIC_API_KEY
    if provider == "anthropic":
        return os.environ.get("ANTHROPIC_OAUTH_TOKEN") or os.environ.get("ANTHROPIC_API_KEY")
    
    # Google Vertex: support GOOGLE_CLOUD_API_KEY or ADC
    if provider == "google-vertex":
        api_key = os.environ.get("GOOGLE_CLOUD_API_KEY")
        if api_key:
            return api_key
        
        # Check for ADC credentials
        gac_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
        if gac_path and Path(gac_path).exists():
            has_project = bool(os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get("GCLOUD_PROJECT"))
            has_location = bool(os.environ.get("GOOGLE_CLOUD_LOCATION"))
            if has_project and has_location:
                return "<authenticated>"
        
        # Check default ADC path
        home = Path.home()
        default_adc = home / ".config" / "gcloud" / "application_default_credentials.json"
        if default_adc.exists():
            has_project = bool(os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get("GCLOUD_PROJECT"))
            has_location = bool(os.environ.get("GOOGLE_CLOUD_LOCATION"))
            if has_project and has_location:
                return "<authenticated>"
        
        return None
    
    # Amazon Bedrock: multiple credential sources
    if provider == "amazon-bedrock":
        if (
            os.environ.get("AWS_PROFILE")
            or (os.environ.get("AWS_ACCESS_KEY_ID") and os.environ.get("AWS_SECRET_ACCESS_KEY"))
            or os.environ.get("AWS_BEARER_TOKEN_BEDROCK")
            or os.environ.get("AWS_CONTAINER_CREDENTIALS_RELATIVE_URI")
            or os.environ.get("AWS_CONTAINER_CREDENTIALS_FULL_URI")
            or os.environ.get("AWS_WEB_IDENTITY_TOKEN_FILE")
        ):
            return "<authenticated>"
        return None
    
    # Standard lookup
    env_var = PROVIDER_ENV_VARS.get(provider)
    if env_var:
        return os.environ.get(env_var)
    
    return None

