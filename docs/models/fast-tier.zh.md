# 高速模式

点击模型旁的思考强度控件，再点击弹窗右上角的仪表图标。开启后指针保持转动后的状态，不增加边框或底色；悬停时有指针动画。高速模式不改变思考强度滑块。

设置按会话和 provider/model 保存，新选择的模型默认使用标准档。标准档显式覆盖 agent 的高速默认值。修改只影响下一次提交，不改变正在执行的请求。

## 支持的线路

- Codex 根据账号模型目录判断。
- Claude 保留已有的高速模型及原生 speed 参数。
- 官方 xAI API 的 Grok 4.6 支持 Priority Processing，开启发送 `service_tier: "priority"`，标准档发送 `"default"`。
- Grok 订阅线路的 Grok 4.6 走 CLI chat proxy，同样支持 `service_tier: "priority"` / `"default"`。其他 Grok 订阅模型及未知网关保持“尚未确认”，除非线路配置显式声明支持。图标保持可见，并提示原因。

高速可能增加用量或费用。xAI 优先处理的 token 单价为标准档的 2 倍，见 [xAI 定价](https://docs.x.ai/developers/pricing)。图标表示请求偏好，不保证实际速度。Completions 和 Responses 在响应 usage 中保留实际返回的档位；缺少元数据时不推定成功提速。xAI 返回的请求费用也单独保留。

模型配置可用 `fast: false` 显式禁用，或用 `fast: true` 声明线路支持。不根据模型名称推定订阅或网关支持；每次调用按实际选择的线路验证。
