"use client";

/**
 * 统一的执行步骤图标（线性 SVG，stroke=currentColor）——替换原来的
 * 旧的花体字符，让 thinking / 函数调用 / 子代理三种行同一套图标
 * 语言（子代理行沿用 attach-card 的终端图标）。
 */

function IconBase({ children }: { children: React.ReactNode }) {
  return (
    <svg
      viewBox="0 0 24 24"
      width="15"
      height="15"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {children}
    </svg>
  );
}

/** 思考：四角星闪光。 */
export function ThinkingIcon() {
  return (
    <IconBase>
      <path d="M12 3.5l2 5.6 5.6 2-5.6 2-2 5.6-2-5.6-5.6-2 5.6-2 2-5.6z" />
    </IconBase>
  );
}

/** 函数调用：代码尖括号。 */
export function FunctionIcon() {
  return (
    <IconBase>
      <polyline points="15.5 18 21.5 12 15.5 6" />
      <polyline points="8.5 6 2.5 12 8.5 18" />
    </IconBase>
  );
}
