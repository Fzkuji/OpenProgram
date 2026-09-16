# Fast mode

Open the thinking-effort picker next to the model and click the gauge icon at the top right. Fast uses an advanced needle position without adding a border or background. Hovering animates the needle; an active gauge remains advanced after the pointer leaves. Fast does not change the thinking-effort slider.

The preference is saved per session and provider/model. A newly selected model starts at Standard. Standard explicitly overrides an agent's priority default. Changes apply to the next submitted turn, not a request already running.

## Supported connections

- Codex uses the account model catalogue.
- Claude retains its supported Fast models and native speed parameter.
- Official xAI API supports Grok 4.6 Priority Processing. It sends `service_tier: "priority"`; Standard sends `"default"`.
- Grok subscription supports Grok 4.6 on the CLI chat proxy (`service_tier: "priority"` / `"default"`). Other Grok subscription models and unknown gateways remain unverified unless their route configuration explicitly declares support. The gauge remains visible with an explanation.

Fast can increase usage or cost. xAI priority processing has a 2× token-price premium; see [xAI pricing](https://docs.x.ai/developers/pricing). The gauge represents the requested mode, not a speed guarantee. Completions and Responses retain the actual returned tier in response usage; absent metadata remains unconfirmed. xAI's reported request cost is retained separately when available.

Model configuration may explicitly set `fast: false` to disable Fast or `fast: true` to declare route support. Do not infer subscription or gateway support from a model's name. Calls are validated against their own selected route.
