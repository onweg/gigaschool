import { AnimatePresence, motion } from "framer-motion";
import { useRef, useState } from "react";
import { api, type ParsedReference } from "../lib/api";
import { SPRING } from "./Motion";

const ACCEPT = ".docx,.pdf,.txt,.md";

/**
 * Приём референсов методиста: прошлая программа, профстандарт, шаблон.
 * Файл разбирается сразу при добавлении — методист видит, что из него
 * извлекли, до того как агент начнёт собирать программу.
 */
export function References({
  items,
  onChange,
  disabled,
}: {
  items: ParsedReference[];
  onChange: (next: ParsedReference[]) => void;
  disabled?: boolean;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  const [busy, setBusy] = useState<string[]>([]);
  const [error, setError] = useState("");

  async function add(files: FileList | null) {
    if (!files?.length || disabled) return;
    setError("");
    const list = Array.from(files);
    setBusy((prev) => [...prev, ...list.map((f) => f.name)]);
    const parsed: ParsedReference[] = [];
    for (const file of list) {
      try {
        parsed.push(await api.parseReference(file));
      } catch (exc) {
        setError(`${file.name}: ${(exc as Error).message}`);
      } finally {
        setBusy((prev) => prev.filter((name) => name !== file.name));
      }
    }
    if (parsed.length) onChange([...items, ...parsed]);
  }

  return (
    <div>
      <motion.div
        className={`dropzone ${over ? "over" : ""}`}
        onDragOver={(event) => {
          event.preventDefault();
          if (!disabled) setOver(true);
        }}
        onDragLeave={() => setOver(false)}
        onDrop={(event) => {
          event.preventDefault();
          setOver(false);
          void add(event.dataTransfer.files);
        }}
        onClick={() => !disabled && inputRef.current?.click()}
        animate={{ scale: over ? 1.012 : 1 }}
        transition={SPRING}
      >
        <input
          ref={inputRef}
          type="file"
          accept={ACCEPT}
          multiple
          hidden
          onChange={(event) => {
            void add(event.target.files);
            event.target.value = "";
          }}
        />
        <span className="dz-icon">＋</span>
        <span>
          Референсы: прошлая программа, профстандарт, шаблон
          <span className="dz-hint"> · .docx .pdf .txt — перетащите или нажмите</span>
        </span>
      </motion.div>

      <AnimatePresence initial={false}>
        {busy.map((name) => (
          <motion.div
            key={`busy-${name}`}
            className="ref-item"
            initial={{ opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0 }}
          >
            <span className="ref-name">{name}</span>
            <span className="hint">разбираю…</span>
          </motion.div>
        ))}
        {items.map((item, i) => (
          <motion.div
            key={`${item.filename}-${i}`}
            className="ref-item"
            initial={{ opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, height: 0 }}
            transition={SPRING}
            title={item.preview}
          >
            <span className="ref-name">{item.filename}</span>
            <span className="hint">структура · {item.chars.toLocaleString("ru")} симв.</span>
            {!disabled && (
              <button
                className="tiny ghost"
                onClick={() => onChange(items.filter((_, index) => index !== i))}
                title="Убрать референс"
              >
                ✕
              </button>
            )}
          </motion.div>
        ))}
      </AnimatePresence>

      {error && (
        <motion.div
          className="ref-error"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
        >
          {error}
        </motion.div>
      )}
    </div>
  );
}
