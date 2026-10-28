import { useEffect, useId, useRef, useState } from "react";
import type { ModelCatalog } from "../lib/api";
import { Icon } from "./Icon";

export function ModelPicker({ catalog, value, disabled, onChange, loadError, onRetry }: {
  catalog: ModelCatalog | null; value: string; disabled: boolean;
  onChange: (id: string) => Promise<void>; loadError: string; onRetry: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [error, setError] = useState("");
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const menuId = useId();
  const model = catalog?.models.find(m => m.id === value);
  useEffect(() => {
    const close = (e: PointerEvent) => { if (!root.current?.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("pointerdown", close);
    return () => document.removeEventListener("pointerdown", close);
  }, []);
  useEffect(() => { if (open) root.current?.querySelector<HTMLButtonElement>('[role="option"][aria-selected="true"]')?.focus(); }, [open]);
  useEffect(() => { if (disabled) setOpen(false); }, [disabled]);

  async function choose(id: string) {
    setError("");
    try { await onChange(id); setOpen(false); trigger.current?.focus(); }
    catch (e) { setError((e as Error).message); setOpen(true); }
  }
  return <div className="model-picker" ref={root} onKeyDown={e => {
    if (e.key === "Escape") { setOpen(false); trigger.current?.focus(); }
    if (open && ["ArrowDown", "ArrowUp", "Home", "End"].includes(e.key)) {
      e.preventDefault();
      const buttons = Array.from(root.current?.querySelectorAll<HTMLButtonElement>('[role="option"]') ?? []);
      const index = buttons.indexOf(document.activeElement as HTMLButtonElement);
      const next = e.key === "Home" ? 0 : e.key === "End" ? buttons.length - 1 : (index + (e.key === "ArrowDown" ? 1 : -1) + buttons.length) % buttons.length;
      buttons[next]?.focus();
    }
  }} onBlur={e => { if (!e.currentTarget.contains(e.relatedTarget as Node)) setOpen(false); }}>
    <button ref={trigger} className="model-picker-trigger" aria-label={`Выбрать модель. ${model?.name ?? "Модель сервера"}`} aria-expanded={open} aria-haspopup="listbox" aria-controls={menuId}
      disabled={disabled} onClick={() => setOpen(!open)} title={disabled ? "Дождитесь завершения текущей операции" : value}>
      <Icon name="spark" size={16} /><span>{model?.name ?? (loadError ? "Модель сервера" : "Загрузка моделей…")}</span><Icon name="chevron" size={13} />
    </button>
    {open && <div className="model-picker-popover">
      <div className="model-picker-caption">Модель для работы над программой</div>
      {loadError ? <div className="model-picker-error" role="alert">{loadError}<button onClick={onRetry}>Повторить загрузку</button></div> : (
        <div id={menuId} role="listbox" aria-label="Модели генерации">{catalog?.models.map(m => <button key={m.id} role="option" aria-selected={m.id === value} className="model-picker-option" disabled={disabled} onClick={() => void choose(m.id)}>
          <span><strong>{m.name}</strong><small>{m.description}</small></span><span className="model-picker-mark">{m.id === value ? <Icon name="check" size={17} /> : null}</span>
        </button>)}</div>
      )}
      {error && <p className="model-picker-error" role="alert">{error}</p>}
      <p className="model-picker-note">Выбор применяется к следующим действиям агента. Готовые материалы сохраняются.</p>
      {model?.reasoning && <p className="model-picker-note">Рассуждения требуют дополнительного времени и токенов.</p>}
      {catalog?.offline_stub ? <p className="model-picker-price">Офлайн-режим: выбор сохраняется, генерация остаётся тестовой.</p> : model?.input_per_million != null && <p className="model-picker-price">₽ / 1 млн токенов: {model.input_per_million.toLocaleString("ru-RU")} вход · {model.output_per_million?.toLocaleString("ru-RU")} выход</p>}
    </div>}
  </div>;
}
