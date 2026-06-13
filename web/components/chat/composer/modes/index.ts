/**
 * Composer mode 注册表 —— id → ComposerMode。
 *
 * 容器据此查表渲染当前 mode，而不是为每种形态写特例。加一种 mode = 在这里
 * 登记一项 + 加一个文件夹（实现 ComposerMode 接口），容器主体不动。
 *
 * 设计：docs/design/ui/composer-interaction-modes.md。
 *
 * 注：fn-form 因为字段状态/动画与 composer 的 ref、Send 按钮深度耦合，
 * 第一版仍由容器内联渲染（它本身就是"输入框变形"的范本）；question /
 * approval 这两种**新** mode 走本注册表。fn-form 正式纳入注册表作为最后
 * 一步收尾（落地顺序步 1 的尾声）。先把注册表脊梁立起来，新 mode 挂上去。
 */

import type { ComposerMode, ComposerModeId } from "./types";

const REGISTRY: Partial<Record<ComposerModeId, ComposerMode>> = {};

export function registerMode(mode: ComposerMode): void {
  REGISTRY[mode.id] = mode;
}

export function getMode(id: ComposerModeId): ComposerMode | undefined {
  return REGISTRY[id];
}

export type { ComposerMode, ComposerModeId } from "./types";
