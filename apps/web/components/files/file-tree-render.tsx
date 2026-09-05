/** FileTree row presentation primitives. */
import { useEffect, useRef, useState } from "react";
import { File, FileCode, FileImage, FileJson, FileText } from "lucide-react";
import styles from "./files-panel.module.css";

const ICON_BUCKETS: [Set<string>, typeof File, string | undefined][] = [
  [new Set(["ts", "tsx", "js", "jsx", "mjs", "cjs", "py", "rs", "go", "c", "cpp", "h", "hpp", "java", "sh"]), FileCode, "var(--accent-cyan)"],
  [new Set(["json", "yaml", "yml", "toml", "csv"]), FileJson, "var(--accent-yellow)"],
  [new Set(["md", "markdown", "txt", "rst", "log"]), FileText, undefined],
  [new Set(["png", "jpg", "jpeg", "gif", "svg", "webp", "ico"]), FileImage, "var(--accent-purple)"],
  [new Set(["pdf"]), FileText, "var(--accent-red)"],
];

export function FileGlyph({ name }: { name: string }) {
  const dot = name.lastIndexOf(".");
  const ext = dot > 0 ? name.slice(dot + 1).toLowerCase() : "";
  for (const [exts, Icon, color] of ICON_BUCKETS) {
    if (exts.has(ext)) {
      return <Icon size={15} className={styles.treeIcon} style={color ? { color } : undefined} />;
    }
  }
  return <File size={15} className={styles.treeIcon} />;
}
export function InlineNameInput({
  initial,
  onCommit,
  onCancel,
}: {
  initial: string;
  onCommit: (name: string) => void;
  onCancel: () => void;
}) {
  const [value, setValue] = useState(initial);
  const ref = useRef<HTMLInputElement>(null);
  const done = useRef(false);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.focus();
    const dot = initial.lastIndexOf(".");
    el.setSelectionRange(0, dot > 0 ? dot : initial.length);
  }, [initial]);

  const finish = (fn: () => void) => {
    if (done.current) return;
    done.current = true;
    fn();
  };

  return (
    <input
      ref={ref}
      className={styles.treeInput}
      value={value}
      spellCheck={false}
      onChange={(e) => setValue(e.target.value)}
      onClick={(e) => e.stopPropagation()}
      onBlur={() => finish(onCancel)}
      onKeyDown={(e) => {
        if (e.key === "Enter") {
          e.preventDefault();
          const v = value.trim();
          if (!v) return finish(onCancel);
          if (v.includes("/")) return;
          finish(() => onCommit(v));
        } else if (e.key === "Escape") {
          e.preventDefault();
          finish(onCancel);
        }
      }}
    />
  );
}
