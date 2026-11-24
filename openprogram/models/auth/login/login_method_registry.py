"""One description per provider of how a person connects it.

Every surface that needs to know how a provider is connected reads it here
and nowhere else: the ways to connect (in the order they are offered, the
first one recommended), the environment variables that hold its key, how a
key is checked (which request, to which address) and the provider's own
pages for keys and billing, and the company it is one way of (``COMPANIES``). The catalogue listing, the key check, the
environment-key reader, the credentials API, the CLI and the sign-in dialog
all read these entries, so they cannot disagree about a provider (the gap
that once hid "Add key" for OpenCode while the accounts API offered it).

A provider without an entry is a plain API-key provider: one way in (paste
a key), its key variable from the catalogue (models.dev), checked with
``GET <base>/models`` (or the Anthropic form for an Anthropic-wire host).

Method ids (handlers live in ``auth/methods/`` and ``auth/login/``):
  pkce_oauth       browser PKCE OAuth, loopback callback
  device_code      browser device-code OAuth
  import_from_cli  read an existing vendor-CLI login
  paste_token      paste an access token
  api_key          paste a static API key

Check styles (``models/listing/credentials.py`` runs them):
  openai_bearer     ``GET <base>/models`` with ``Authorization: Bearer``
  openrouter_key    ``GET <base>/key`` (also reports the key's credit)
  anthropic_native  ``GET https://api.anthropic.com/v1/models`` with ``x-api-key``
  anthropic_compat  ``GET <base>/v1/models`` with ``x-api-key``
  google_query      ``GET https://generativelanguage.googleapis.com/v1beta/models?key=``
  login             the stored sign-in itself (refreshed, then its token read)
  cloud             a cloud credential chain (SigV4 / ADC), checked at first use
"""
from __future__ import annotations

import contextlib

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class LoginMethod:
    id: str
    label: str
    label_zh: str
    description: str
    description_zh: str
    #: The credential file an import reads, shown as a caption.
    path: str | None = None

    def payload(self) -> dict:
        """The method as the web reads it."""
        row = {
            "id": self.id, "label": self.label, "label_zh": self.label_zh,
            "description": self.description, "description_zh": self.description_zh,
        }
        if self.path:
            row["path"] = self.path
        return row


@dataclass(frozen=True)
class ProviderAuth:
    """How one provider is connected."""

    provider: str
    methods: tuple[LoginMethod, ...]
    #: Accepted key variables, first preferred. ``None``: the catalogue names it.
    env_vars: Optional[tuple[str, ...]] = None
    #: Check style (module docstring). ``None``: derived from the provider's wire.
    check: Optional[str] = None
    #: Path the check requests, relative to the provider's base address.
    check_path: str = "/models"
    keys_url: Optional[str] = None
    billing_url: Optional[str] = None
    #: What a sign-in of this provider is called ("ChatGPT" in "ChatGPT
    #: sign-in"). ``None``: the provider's name.
    signin: Optional[str] = None
    extra: dict = field(default_factory=dict)


API_KEY = LoginMethod(
    "api_key", "Paste an API key", "粘贴 API 密钥",
    "A key from the provider's console.", "使用在服务商控制台创建的密钥。",
)


def _entry(provider: str, methods=(API_KEY,), **kw) -> ProviderAuth:
    return ProviderAuth(provider=provider, methods=tuple(methods), **kw)


_ENTRIES: dict[str, ProviderAuth] = {e.provider: e for e in (
    _entry("openai", env_vars=("OPENAI_API_KEY",), check="openai_bearer",
           keys_url="https://platform.openai.com/api-keys",
           billing_url="https://platform.openai.com/settings/organization/billing/overview"),
    # Anthropic is reached only with an official API key (AGENTS.md).
    _entry("anthropic", env_vars=("ANTHROPIC_API_KEY",), check="anthropic_native",
           keys_url="https://console.anthropic.com/settings/keys",
           billing_url="https://console.anthropic.com/settings/billing"),
    _entry("claude-code", methods=(), env_vars=(), check="login"),
    _entry("google", env_vars=("GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_GENERATIVE_AI_API_KEY"),
           check="google_query", keys_url="https://aistudio.google.com/apikey"),
    _entry("gemini-subscription", methods=(LoginMethod(
        "import_from_cli", "Import existing login", "导入已有登录",
        "Uses the sign-in the Gemini CLI saved on this machine.",
        "使用 Gemini CLI 在本机保存的登录。",
        path="~/.gemini/oauth_creds.json",
    ),), env_vars=(), check="login", signin="Gemini CLI"),
    _entry("openai-codex", methods=(LoginMethod(
        "pkce_oauth", "Sign in with ChatGPT", "用 ChatGPT 登录",
        "Opens ChatGPT in your browser to approve.",
        "在浏览器打开 ChatGPT 完成授权。",
    ),), env_vars=(), check="login", signin="ChatGPT"),
    _entry("github-copilot", methods=(
        LoginMethod(
            "device_code", "Sign in with GitHub", "用 GitHub 登录",
            "Opens GitHub in your browser and shows a one-time code to enter there.",
            "在浏览器打开 GitHub，并给出一个一次性代码在那里输入。",
        ),
        LoginMethod(
            "paste_token", "Paste a GitHub token", "粘贴 GitHub 令牌",
            "A GitHub token of an account with Copilot.",
            "有 Copilot 的 GitHub 账号的令牌。",
        ),
    ), env_vars=("COPILOT_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"), check="login",
       billing_url="https://github.com/settings/copilot", signin="GitHub"),
    _entry("xai-subscription", methods=(
        LoginMethod(
            "pkce_oauth", "Sign in with Grok", "用 Grok 登录",
            "Opens Grok in your browser to approve.",
            "在浏览器打开 Grok 完成授权。",
        ),
        LoginMethod(
            "import_from_cli", "Import existing login", "导入已有登录",
            "Uses the sign-in the Grok CLI saved on this machine.",
            "使用 Grok CLI 在本机保存的登录。",
            path="~/.grok/auth.json",
        ),
        LoginMethod(
            "paste_token", "Paste an access token", "粘贴访问令牌",
            "For a Grok token copied from another machine or tool.",
            "用于从其他机器或工具复制来的 Grok 令牌。",
        ),
    ), env_vars=(), check="login", signin="Grok"),
    # OpenCode Zen and Go: keys from the OpenCode console, checked by listing
    # the models of the provider's own base address (opencode.ai/docs/zen,
    # opencode.ai/docs/go).
    _entry("opencode", env_vars=("OPENCODE_API_KEY",), check="openai_bearer",
           keys_url="https://opencode.ai/auth", billing_url="https://opencode.ai/console"),
    _entry("opencode-go", env_vars=("OPENCODE_API_KEY",), check="openai_bearer",
           keys_url="https://opencode.ai/auth", billing_url="https://opencode.ai/console/go"),
    _entry("openrouter", env_vars=("OPENROUTER_API_KEY",), check="openrouter_key",
           keys_url="https://openrouter.ai/settings/keys",
           billing_url="https://openrouter.ai/settings/credits"),
    _entry("xai", env_vars=("XAI_API_KEY",), keys_url="https://console.x.ai",
           billing_url="https://console.x.ai"),
    _entry("deepseek", env_vars=("DEEPSEEK_API_KEY",),
           keys_url="https://platform.deepseek.com/api_keys",
           billing_url="https://platform.deepseek.com/top_up"),
    _entry("moonshotai", env_vars=("MOONSHOT_API_KEY",),
           keys_url="https://platform.moonshot.ai/console/api-keys",
           billing_url="https://platform.moonshot.ai/console/pay"),
    _entry("moonshotai-cn", keys_url="https://platform.moonshot.cn/console/api-keys",
           billing_url="https://platform.moonshot.cn/console/pay"),
    _entry("moonshot", env_vars=("MOONSHOT_API_KEY",)),
    _entry("kimi", env_vars=("KIMI_API_KEY",)),
    _entry("kimi-coding", env_vars=("KIMI_API_KEY", "MOONSHOT_API_KEY")),
    _entry("zai", env_vars=("ZAI_API_KEY",), keys_url="https://z.ai/manage-apikey/apikey-list"),
    _entry("zai-coding-plan", keys_url="https://z.ai/manage-apikey/apikey-list"),
    _entry("groq", env_vars=("GROQ_API_KEY",), keys_url="https://console.groq.com/keys"),
    _entry("mistral", env_vars=("MISTRAL_API_KEY",), keys_url="https://console.mistral.ai/api-keys"),
    _entry("cerebras", env_vars=("CEREBRAS_API_KEY",), keys_url="https://cloud.cerebras.ai"),
    _entry("fireworks-ai", env_vars=("FIREWORKS_API_KEY",),
           keys_url="https://fireworks.ai/account/api-keys"),
    _entry("togetherai", env_vars=("TOGETHER_API_KEY",),
           keys_url="https://api.together.ai/settings/api-keys"),
    _entry("huggingface", env_vars=("HF_TOKEN",), keys_url="https://huggingface.co/settings/tokens"),
    # MiniMax's Token Plans use the region account's own key.
    _entry("minimax", env_vars=("MINIMAX_API_KEY",)),
    _entry("minimax-cn", env_vars=("MINIMAX_CN_API_KEY", "MINIMAX_API_KEY")),
    _entry("minimax-coding-plan", env_vars=("MINIMAX_API_KEY",)),
    _entry("minimax-cn-coding-plan", env_vars=("MINIMAX_CN_API_KEY", "MINIMAX_API_KEY")),
    _entry("vercel-ai-gateway", env_vars=("AI_GATEWAY_API_KEY",)),
    _entry("atlascloud", env_vars=("ATLASCLOUD_API_KEY",)),
    _entry("azure-openai-responses", env_vars=("AZURE_OPENAI_API_KEY",), check="cloud"),
    _entry("amazon-bedrock", methods=(), env_vars=(), check="cloud"),
    _entry("google-vertex", methods=(), env_vars=(), check="cloud"),
)}


def provider_auth(provider: str) -> ProviderAuth:
    """The entry for ``provider`` (a default API-key entry when none is listed)."""
    entry = _ENTRIES.get(provider)
    if entry is not None:
        return entry
    from openprogram.models.providers.catalog.local import is_local_provider
    if is_local_provider(provider):
        return _entry(provider, env_vars=(provider.upper().replace("-", "_") + "_API_KEY",),
                      check="openai_bearer")
    return _entry(provider)


def listed_providers() -> list[str]:
    """Providers with an entry of their own."""
    return list(_ENTRIES)


def login_method_list(provider: str) -> list[LoginMethod]:
    """Ordered ways to connect ``provider``; the first is the recommended one."""
    from openprogram.models.auth.provider_policy import UNSUPPORTED_PROVIDERS
    if provider in UNSUPPORTED_PROVIDERS:
        return []
    return list(provider_auth(provider).methods)


def login_methods(provider: str) -> list[tuple[str, str]]:
    """``(method id, title)`` pairs for terminal pickers; the first is the default."""
    return [(m.id, m.label) for m in login_method_list(provider)]


def login_method_payload(provider: str) -> list[dict]:
    """The methods as the web reads them (titles, descriptions, file paths)."""
    return [m.payload() for m in login_method_list(provider)]


def accepts_api_key(provider: str) -> bool:
    return any(m.id == "api_key" for m in login_method_list(provider))


def default_method(provider: str) -> str:
    """The method id a surface should pre-select for ``provider``."""
    methods = login_method_list(provider)
    if not methods:
        from openprogram.models.auth.types import AuthConfigError
        from openprogram.models.auth.provider_policy import API_KEY_REQUIRED
        raise AuthConfigError(API_KEY_REQUIRED)
    return methods[0].id


def env_vars(provider: str) -> list[str]:
    """The variables that hold ``provider``'s key, first preferred."""
    names = provider_auth(provider).env_vars
    if names is not None:
        return list(names)
    try:
        from openprogram.models.providers.catalog.metadata import catalogue_env_var
        name = catalogue_env_var(provider)
    except Exception:
        name = None
    return [name] if name else []


def check_style(provider: str) -> str:
    """How a key of ``provider`` is checked (module docstring)."""
    style = provider_auth(provider).check
    if style:
        return style
    with contextlib.suppress(Exception):
        from openprogram.models.providers.catalog.metadata import default_api_for
        if default_api_for(provider) == "anthropic-messages":
            return "anthropic_compat"
    return "openai_bearer"


def console_url(provider: str, page: str = "keys") -> Optional[str]:
    """The provider's own ``keys`` or ``billing`` page, or ``None``."""
    entry = provider_auth(provider)
    return entry.keys_url if page == "keys" else entry.billing_url if page == "billing" else None


# Sign-in providers the catalogue names awkwardly and that need no entry of their own.
_SIGNIN_NAMES = {"google-gemini-cli": "Gemini CLI"}


def signin_name(provider: str) -> str:
    """What a sign-in of ``provider`` is called: "ChatGPT" for OpenAI Codex,
    else the provider's own name."""
    name = provider_auth(provider).signin or _SIGNIN_NAMES.get(provider)
    if name:
        return name
    try:
        from openprogram.models.providers.catalog.metadata import label_for
        return label_for(provider) or provider
    except Exception:
        return provider


# Companies: one company, several ways to connect it.
#
# Several catalogue providers are one company reached in different ways (the
# ChatGPT sign-in and the OpenAI API key; OpenCode Zen and Go; a region or a
# coding plan of the same vendor). The table below is the one place that
# says so: the provider list draws one card per company with a section per
# way, the CLI prints the company over its ways, the model picker names a
# way "Company · Way", and Add provider offers a company's ways as tabs.
# Every surface still keys on the catalogue id; this only groups them.


@dataclass(frozen=True)
class Way:
    """One way to connect a company: a catalogue provider id and its names."""

    provider: str
    #: The section's title on a company card ("ChatGPT subscription").
    title: str
    title_zh: str
    #: The way in a few words, after the company ("OpenAI · ChatGPT").
    short: str
    short_zh: str
    #: Offered as a tab of the company in Add provider.
    offered: bool = False


@dataclass(frozen=True)
class Company:
    id: str
    label: str
    ways: tuple[Way, ...]


_PAYG, _PAYG_ZH = "pay as you go", "按量付费"


def _way(provider: str, title: str, title_zh: str, short: str, short_zh: str, *, offered: bool = False) -> Way:
    return Way(provider, title, title_zh, short, short_zh, offered)


def _api(provider: str, name: str, name_zh: str, short: str, short_zh: str, *, offered: bool = False) -> Way:
    """A pay-as-you-go way: "<name> · pay as you go"."""
    return _way(provider, f"{name} · {_PAYG}", f"{name_zh} · {_PAYG_ZH}", short, short_zh, offered=offered)


COMPANIES: tuple[Company, ...] = (
    Company("openai", "OpenAI", (
        _way("openai-codex", "ChatGPT subscription", "ChatGPT 订阅", "ChatGPT", "ChatGPT", offered=True),
        _api("openai", "API", "API", "API", "API", offered=True),
    )),
    Company("opencode", "OpenCode", (
        _api("opencode", "Zen", "Zen", "Zen", "Zen"),
        _way("opencode-go", "Go · monthly subscription", "Go · 月度订阅", "Go", "Go"),
    )),
    Company("perplexity", "Perplexity", (
        _api("perplexity", "Sonar API", "Sonar API", "Sonar", "Sonar"),
        _api("perplexity-agent", "Agent API", "Agent API", "Agent", "Agent"),
    )),
    Company("xai", "xAI", (
        _api("xai", "API", "API", "API", "API", offered=True),
        _way("xai-subscription", "Grok subscription", "Grok 订阅", "Grok", "Grok", offered=True),
    )),
    Company("google", "Google", (
        _api("google", "Gemini API", "Gemini API", "API", "API"),
        _way("gemini-subscription", "Gemini subscription", "Gemini 订阅", "Subscription", "订阅"),
        _way("google-gemini-cli", "Gemini CLI sign-in", "Gemini CLI 登录", "Gemini CLI", "Gemini CLI"),
        _api("google-vertex", "Vertex AI", "Vertex AI", "Vertex", "Vertex"),
        _api("google-vertex-anthropic", "Vertex AI (Anthropic models)", "Vertex AI（Anthropic 模型）",
             "Vertex Anthropic", "Vertex Anthropic"),
    )),
    Company("minimax", "MiniMax", (
        _api("minimax", "API (International)", "API（国际）", "API", "API"),
        _api("minimax-cn", "API (China)", "API（中国）", "API China", "API 中国"),
        _way("minimax-coding-plan", "Token Plan (International)", "Token Plan（国际）", "Token Plan", "Token Plan"),
        _way("minimax-cn-coding-plan", "Token Plan (China)", "Token Plan（中国）", "Token Plan China", "Token Plan 中国"),
    )),
    Company("moonshotai", "Moonshot AI", (
        _api("moonshotai", "Kimi API", "Kimi API", "API", "API", offered=True),
        _way("kimi-coding", "Kimi Coding plan", "Kimi Coding 套餐", "Kimi Coding", "Kimi Coding", offered=True),
        _api("moonshotai-cn", "Kimi API (China)", "Kimi API（中国）", "API China", "API 中国", offered=True),
        _way("kimi-code-plan-global", "Kimi For Coding (kimi.ai)", "Kimi For Coding（kimi.ai）",
             "For Coding", "For Coding"),
        _way("kimi-code-plan-cn", "Kimi For Coding (kimi.com)", "Kimi For Coding（kimi.com）",
             "For Coding China", "For Coding 中国"),
    )),
    Company("zai", "Z.ai · Zhipu", (
        _api("zai", "Z.ai API", "Z.ai API", "Z.ai API", "Z.ai API", offered=True),
        _way("zai-coding-plan", "Z.ai Coding Plan", "Z.ai Coding Plan", "Z.ai Coding", "Z.ai Coding", offered=True),
        _api("zhipuai", "Zhipu API (China)", "智谱 API（中国）", "Zhipu API", "智谱 API", offered=True),
        _way("zhipuai-coding-plan", "Zhipu Coding Plan (China)", "智谱 Coding Plan（中国）",
             "Zhipu Coding", "智谱 Coding"),
    )),
    Company("alibaba", "Alibaba Cloud", (
        _api("alibaba", "Model Studio API", "百炼 API", "API", "API"),
        _api("alibaba-cn", "Model Studio API (China)", "百炼 API（中国）", "API China", "API 中国"),
        _way("alibaba-coding-plan", "Coding Plan", "Coding Plan", "Coding Plan", "Coding Plan"),
        _way("alibaba-coding-plan-cn", "Coding Plan (China)", "Coding Plan（中国）", "Coding Plan China",
             "Coding Plan 中国"),
        _way("alibaba-token-plan", "Token Plan", "Token Plan", "Token Plan", "Token Plan"),
        _way("alibaba-token-plan-cn", "Token Plan (China)", "Token Plan（中国）", "Token Plan China",
             "Token Plan 中国"),
    )),
    Company("stepfun", "StepFun", (
        _api("stepfun-ai", "API (Global)", "API（国际）", "API", "API"),
        _api("stepfun", "API (China)", "API（中国）", "API China", "API 中国"),
        _way("stepfun-ai-step-plan", "Step Plan (Global)", "Step Plan（国际）", "Step Plan", "Step Plan"),
        _way("stepfun-step-plan", "Step Plan (China)", "Step Plan（中国）", "Step Plan China", "Step Plan 中国"),
    )),
    Company("xiaomi", "Xiaomi", (
        _api("xiaomi", "MiMo API", "MiMo API", "API", "API"),
        _way("xiaomi-token-plan-cn", "Token Plan (China)", "Token Plan（中国）", "Token Plan China",
             "Token Plan 中国"),
        _way("xiaomi-token-plan-sgp", "Token Plan (Singapore)", "Token Plan（新加坡）", "Token Plan Singapore",
             "Token Plan 新加坡"),
        _way("xiaomi-token-plan-ams", "Token Plan (Europe)", "Token Plan（欧洲）", "Token Plan Europe",
             "Token Plan 欧洲"),
    )),
    Company("tencent", "Tencent Cloud", (
        _api("tencent-tokenhub", "TokenHub", "TokenHub", "TokenHub", "TokenHub"),
        _way("tencent-token-plan", "Token Plan", "Token Plan", "Token Plan", "Token Plan"),
        _way("tencent-coding-plan", "Coding Plan (China)", "Coding Plan（中国）", "Coding Plan", "Coding Plan"),
    )),
    Company("volcengine", "ByteDance", (
        _api("volcengine", "Volcengine Ark", "火山方舟", "Volcengine", "火山引擎"),
        _way("volcengine-coding-plan", "Volcengine Ark Coding Plan", "火山方舟 Coding Plan",
             "Volcengine Coding", "火山 Coding"),
        _api("byteplus", "BytePlus ModelArk", "BytePlus ModelArk", "BytePlus", "BytePlus"),
    )),
    Company("siliconflow", "SiliconFlow", (
        _api("siliconflow", "API (Global)", "API（国际）", "API", "API"),
        _api("siliconflow-cn", "API (China)", "API（中国）", "API China", "API 中国"),
    )),
    Company("azure", "Microsoft Azure", (
        _api("azure-openai-responses", "Azure OpenAI", "Azure OpenAI", "OpenAI", "OpenAI"),
        _api("azure", "Azure AI Foundry", "Azure AI Foundry", "Foundry", "Foundry"),
        _api("azure-cognitive-services", "Cognitive Services", "Cognitive Services", "Cognitive Services",
             "Cognitive Services"),
    )),
    Company("cloudflare", "Cloudflare", (
        _api("cloudflare-workers-ai", "Workers AI", "Workers AI", "Workers AI", "Workers AI"),
        _api("cloudflare-ai-gateway", "AI Gateway", "AI Gateway", "AI Gateway", "AI Gateway"),
    )),
    Company("ollama", "Ollama", (
        _way("ollama", "Local server", "本地服务", "Local", "本地"),
        _api("ollama-cloud", "Cloud", "云端", "Cloud", "云端"),
    )),
)

_WAY_OF: dict[str, tuple[Company, Way]] = {
    way.provider: (company, way) for company in COMPANIES for way in company.ways
}


_placed_for: tuple = ()
_placed: tuple[tuple[Company, ...], dict[str, tuple[Company, Way]]] = (COMPANIES, _WAY_OF)


def _catalogue_entries() -> list[dict]:
    try:
        from openprogram.models.providers.catalog.sources import models_dev
        return [p for p in models_dev.list_providers() if p.get("id")]
    except Exception:
        return []


def _own_placement() -> dict[str, str]:
    """``config.json``'s ``provider_companies``: an unlisted entry's id to
    the company it belongs to, the person's correction of :func:`_place`
    (the entry's own id: a company of its own)."""
    try:
        from openprogram.state.config import read_config_value
        found = read_config_value("provider_companies")
    except Exception:
        return {}
    return {str(k): str(v) for k, v in found.items() if k and v} if isinstance(found, dict) else {}


def _place(entries: list[dict], own: Optional[dict[str, str]] = None,
           ) -> tuple[tuple[Company, ...], dict[str, tuple[Company, Way]]]:
    """The table with the catalogue's unlisted entries placed.

    The catalogue (models.dev) lists entries flat, with no company. An entry
    the table does not list joins the company whose id, or one of whose
    entries' ids, its own id extends at a hyphen (a new
    ``minimax-…`` is MiniMax's). Unlisted entries that read one key variable
    and extend one another's id are a company of their own, named after the
    shortest. Anything else stays a company of its own. ``own`` (entry id to
    company id) places an unlisted entry before any of that: on a company of
    the table, on another entry's, or, given its own id, by itself."""
    own = own or {}
    stems: dict[str, str] = {}
    for company in COMPANIES:
        stems[company.id] = company.id
        for way in company.ways:
            stems.setdefault(way.provider, company.id)
    extra: dict[str, list[Way]] = {}
    said: dict[str, list[Way]] = {}
    loose: list[dict] = []
    for entry in entries:
        pid = entry["id"]
        if pid in _WAY_OF:
            continue
        label = entry.get("label") or pid
        if pid in own:
            if own[pid] != pid:
                said.setdefault(own[pid], []).append(Way(pid, label, label, label, label))
            continue
        parts = pid.split("-")
        owner = next((stems[stem] for n in range(len(parts) - 1, 0, -1)
                      if (stem := "-".join(parts[:n])) in stems), None)
        if owner is None:
            loose.append(entry)
            continue
        extra.setdefault(owner, []).append(Way(pid, label, label, label, label))
    listed = {c.id for c in COMPANIES}
    for target in [t for t in said if t not in listed]:
        # Placed on another unlisted entry: the two are a company named after it.
        head = next((e for e in loose if e["id"] == target), None)
        if head is None:
            continue
        loose.remove(head)
        name = head.get("label") or target
        said[target].insert(0, Way(target, name, name, name, name))
    companies = [Company(c.id, c.label, c.ways + tuple(extra.get(c.id, ())) + tuple(said.get(c.id, ())))
                 for c in COMPANIES]
    by_id = {e["id"]: e for e in entries}
    companies += [Company(t, by_id[t].get("label") or t, tuple(ways))
                  for t, ways in said.items() if t not in listed and ways and ways[0].provider == t]
    by_var: dict[str, list[dict]] = {}
    for entry in loose:
        if entry.get("env_var"):
            by_var.setdefault(entry["env_var"], []).append(entry)
    for group in by_var.values():
        group.sort(key=lambda e: (len(e["id"]), e["id"]))
        head = group[0]
        rest = [e for e in group[1:] if e["id"].startswith(head["id"] + "-")]
        if not rest:
            continue
        ways = tuple(Way(e["id"], e.get("label") or e["id"], e.get("label") or e["id"],
                         e.get("label") or e["id"], e.get("label") or e["id"]) for e in [head, *rest])
        companies.append(Company(head["id"], head.get("label") or head["id"], ways))
    placed = tuple(companies)
    return placed, {way.provider: (company, way) for company in placed for way in company.ways}


def _current() -> tuple[tuple[Company, ...], dict[str, tuple[Company, Way]]]:
    global _placed_for, _placed
    entries = _catalogue_entries()
    own = _own_placement()
    key = (tuple(sorted((e["id"], e.get("env_var") or "") for e in entries)), tuple(sorted(own.items())))
    if key != _placed_for:
        _placed, _placed_for = _place(entries, own), key
    return _placed


def companies() -> tuple[Company, ...]:
    """Every company with its entries: the table, with the catalogue's
    unlisted entries placed by name (:func:`_place`)."""
    return _current()[0]


def company_of(provider: str) -> Optional[tuple[Company, Way]]:
    """The company ``provider`` is one way of, with that way; ``None`` for a
    provider that is a company of its own."""
    return _current()[1].get(provider)


def offered_ways(provider: str) -> list[str]:
    """The other ways of ``provider``'s company that Add provider offers
    beside it as tabs, in the table's order."""
    found = company_of(provider)
    if found is None:
        return []
    return [w.provider for w in found[0].ways if w.offered and w.provider != provider]


def provider_for_env_var(name: str) -> Optional[str]:
    """The provider a key variable belongs to (first listed owner wins)."""
    for entry in _ENTRIES.values():
        if entry.env_vars and name in entry.env_vars:
            return entry.provider
    return None


def describe(provider: str) -> dict:
    """The whole entry as the credentials API returns it."""
    return {
        "methods": login_method_payload(provider),
        "env_vars": env_vars(provider),
        "check": check_style(provider),
        "keys_url": console_url(provider, "keys"),
        "billing_url": console_url(provider, "billing"),
        "signin_name": signin_name(provider),
    }


__all__ = [
    "API_KEY", "COMPANIES", "Company", "LoginMethod", "ProviderAuth", "Way", "accepts_api_key",
    "check_style", "companies", "company_of", "console_url", "default_method", "describe", "env_vars",
    "listed_providers", "login_method_list", "login_method_payload", "login_methods",
    "offered_ways", "provider_auth", "provider_for_env_var", "signin_name",
]
