/**
 * LANE_COLORS — the single categorical branch palette.
 *
 * 两处消费者共用同一份：DAG lane 描边（lib/runtime-bridge/dag）、右栏
 * branches 面板的兜底色。以前各存一份副本，index 4
 * 曾漂成红色和错误描边撞色。
 *
 * 取值是主题 token（--lane-0 … --lane-17，定义在
 * app/styles/design/color.css），这里只给出 var() 引用：消费者都把它写进
 * CSS（style、SVG 的 fill / stroke 属性、--tone），由浏览器解析。
 *
 * 这是**叶子模块**：不 import 任何东西。dag/types.ts 在模块作用域就取
 * `window`，SSR 下直接崩，所以调色板必须放在它之外。
 *
 * Index 0 是主干色；1..N-1 给侧分支，按 leaf id 哈希取。色相均匀铺开，
 * 相邻分支不会读成同一个颜色。所有主题共用（categorical，不随明暗切换）。
 */
export const LANE_COUNT = 18;

export const LANE_COLORS: readonly string[] = Array.from(
  { length: LANE_COUNT },
  (_, index) => `var(--lane-${index})`,
);
