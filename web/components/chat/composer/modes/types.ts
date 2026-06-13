/**
 * Composer interaction modes — 输入框作为"用户决定"统一承接点。
 *
 * 设计：docs/design/ui/composer-interaction-modes.md。
 *
 * composer 任一时刻处于一种 mode（idle 普通打字 / fn-form 填表单 /
 * question 回答 runtime.ask / approval 批准工具 / …）。每种 mode 是一个
 * 自包含单元，对容器暴露同一套契约：容器只认这个接口，加一种 mode = 加一个
 * 实现了它的文件夹，不改容器主体。
 *
 * 这一版先把概念定下来。fn-form 作为第一种 mode 纳入；question / approval
 * 后续按同一接口加进来。idle（打字）不是一个 ComposerMode 对象——它是"没有
 * mode 占据输入区"的缺省态，由容器直接渲染 ChatInputRow。
 */

import type { ReactNode } from "react";

/** mode 的唯一 id。idle 是缺省态（无 mode 对象），其余每种对应一个文件夹。 */
export type ComposerModeId = "fn-form" | "question" | "approval";

/** 主操作（占住 composer 的 Send 按钮位）。 */
export interface ModePrimaryAction {
  /** 按钮文案（fn-form="运行"、question 视情况、approval="允许"）。 */
  label: string;
  /** 当前是否不可点（必填项没填等）。 */
  disabled: boolean;
  /** 点击执行（提交表单 / 发 question_reply / 发批准）。 */
  run: () => void;
}

/** 次操作（取消 / 拒绝），点了就退出当前 mode。null = 无次操作。 */
export interface ModeSecondaryAction {
  label: string;
  run: () => void;
}

/**
 * 一种 composer mode 的契约。`TInput` 是进入这种 mode 需要的数据
 * （fn 定义 / 问题 envelope / 批准请求），由触发源塞进 store，容器读出后
 * 交给 mode；`TState` 是这种 mode 的局部 UI 状态（如 fn-form 的字段值）。
 *
 * 容器对每种 mode 的用法统一为：
 *   const state = mode.useState(input);
 *   <mode.Body state={state} input={input} />
 *   const primary = mode.primaryAction(state, input);
 *   const secondary = mode.secondaryAction(state, input);
 */
export interface ComposerMode<TInput = unknown, TState = unknown> {
  id: ComposerModeId;
  /** 这种 mode 的局部状态 hook（如 use-fn-form-state）。 */
  useState(input: TInput): TState;
  /** 输入区里渲染的主体（如 FunctionForm / 问题选项）。 */
  Body(props: { state: TState; input: TInput }): ReactNode;
  /** 主按钮（占 Send 位）的文案/可用/行为。 */
  primaryAction(state: TState, input: TInput): ModePrimaryAction;
  /** 次按钮（取消/拒绝），null 表示没有。 */
  secondaryAction(state: TState, input: TInput): ModeSecondaryAction | null;
}
