# 输出风格

输出风格是一段追加到系统提示里的文字，用来描述回复该**怎么写**。它影响语气、篇幅和结构，不改变 agent 能做什么、有哪些工具、用哪个模型。

同一时刻只有一个风格生效。它作用于所有经过提示装配器的模型调用：对话轮次、agentic 函数体、后台 agent。

## 切换风格

TUI 里：

```
/style              # 列出全部风格，标记当前生效的那个
/style concise      # 切换
/style default      # 回到不附加任何文字
```

命令行里：

```bash
openprogram config get agent.output_style
openprogram config set agent.output_style concise
```

Web 设置页在 Agent 分组下把同一个设置渲染成下拉框。

风格是全局偏好，存在 `~/.openprogram/config.json` 的 `agent.output_style` 里，跨会话和重启都保留。改动从下一轮开始生效。

## 内置风格

| 名称 | 效果 |
|------|------|
| `default` | 不附加任何文字，等同于没有输出风格这个机制。 |
| `concise` | 用问题允许的最少字数回答，结论在前，不写铺垫和收尾总结。 |
| `explanatory` | 先给答案，再讲推理、权衡过的取舍，以及改动如何嵌进周围代码。 |
| `direct` | 结论不加缓冲词，略去不影响决策的免责说明，前提错了就直接纠正。 |
| `detailed` | 覆盖边界情况、失败模式和依赖的假设，长回答用标题或列表组织。 |

## 自定义风格

自定义风格就是一个 markdown 文件，**文件名即风格名**，正文即追加进提示的文字。发现方式和技能一致，按优先级从低到高：

1. 内置，见上表
2. 用户级，`~/.openprogram/output-styles/<name>.md`
3. 项目级，`<cwd>/output-styles/<name>.md`

重名时后者覆盖前者，所以项目里的 `concise.md` 会替换掉同名内置风格，用户级文件在没有项目级覆盖的地方生效。

```bash
mkdir -p ~/.openprogram/output-styles
cat > ~/.openprogram/output-styles/lab-notes.md <<'EOF'
## Output style: lab notes

Report work as a lab notebook entry. State what was tried, what was observed,
and what it implies. Record negative results as plainly as positive ones.
EOF

openprogram config set agent.output_style lab-notes
```

文件带 YAML frontmatter 时会被剥掉，所以可以写 `description:` 自用而不会进入模型。正文为空的文件直接忽略，不会注册成空风格。

## 文字落在哪一层

输出风格是注册的上下文组件（`output_style`，L0 层），文字经由 `openprogram/context/components.py` 里唯一的装配器进入提示，不是拼在用户消息上。

在 L0 层内部，风格排在身份和工具使用指引之后、agent 自己的 inline 系统提示**之前**。因此 agent 的专属指令位置更靠后，两者冲突时以 agent 指令为准。

由于 `default` 产出空字符串而空组件会被丢弃，默认配置装配出的提示与完全没有注册风格组件时逐字节一致。

## 相关

- [配置](config.zh.md)：完整设置注册表与 `openprogram config`
- [上下文组装](design/context/composition.md)：拥有提示的分层装配器
