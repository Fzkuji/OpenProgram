/** FileTree row presentation primitives. */
import { useEffect, useRef, useState } from "react";
import { FileTypeIcon } from "./file-type-icon";
import styles from "./files-panel.module.css";

export function FileGlyph({ name }: { name: string }) {
  return <FileTypeIcon name={name} className={styles.treeIcon} />;
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
