/**
 * LANE_COLORS — the single categorical branch/agent palette.
 *
 * 三处消费者共用同一份：DAG lane 描边（lib/runtime-bridge/dag）、右栏
 * branches 面板的兜底色、chat 头像哈希色。以前各存一份副本，index 4
 * 曾漂成红色和错误描边撞色。
 *
 * 这是**叶子模块**：不 import 任何东西。dag/types.ts 在模块作用域就取
 * `window`，SSR 下直接崩，所以调色板必须放在它之外。
 *
 * Index 0 是主干色；1..N-1 给侧分支，按 leaf id 哈希取。色相均匀铺开，
 * 相邻分支不会读成同一个颜色。主题无关（categorical，不随明暗切换）。
 */
export const LANE_COLORS = [
  "#4f8ef7", // blue        (trunk)
  "#5aad4e", // green
  "#d4843a", // orange
  "#9d6fe0", // purple
  // 不用红色相：错误描边是 #e5534b（render/nodes.ts），lane 色若也是红
  // 会和"这个节点出错了"撞色。改用深青，与 #2db3d5 / #35b89a / #52c4c4
  // 的明度和饱和度都拉开。
  "#1f8a8a", // deep teal
  "#2db3d5", // cyan
  "#e0b020", // gold
  "#35b89a", // teal
  "#e066b3", // magenta
  "#6b8dd6", // slate blue
  "#8fbf3f", // lime
  "#d9694f", // coral
  "#52c4c4", // aqua
  "#b08be0", // lavender
  "#c79a4a", // tan
  "#e08a3a", // amber
  "#6fae6f", // sage
  "#d05fa0", // rose
];
