import { useState } from "react";
import type { ReviewItem } from "../lib/review";
import { Icon } from "./Icon";

const STATUS = { todo: "Ждёт решения", reviewed: "Согласовано", deferred: "Отложено", needs_changes: "Есть замечание", stale: "Изменено после проверки" };

/** Обзор без раскрытого текста: согласовать весь набор, тему или выбранные пункты. */
export function ReviewChecklist({ items, allItems, checked, onChecked, onOpen, onApprove, disabled }: {
  items: ReviewItem[];
  allItems: ReviewItem[];
  checked: string[];
  onChecked: (ids: string[]) => void;
  onOpen: (id: string) => void;
  onApprove: (items: ReviewItem[]) => void;
  disabled: boolean;
}) {
  const [opened, setOpened] = useState<string[]>([]);
  const groups = Array.from(new Set(items.map(i => i.topic_id)));
  function toggle(ids: string[], selected: boolean) {
    onChecked(selected ? Array.from(new Set([...checked, ...ids])) : checked.filter(id => !ids.includes(id)));
  }
  return <div className="review-checklist">
    <div className="review-fast-start">
      <div><h2>Согласуйте в удобном темпе</h2><p>Весь комплект одним действием или только выбранные пункты. Для замечаний и правок откройте материал.</p></div>
      <button className="btn primary" disabled={disabled || allItems.every(i => i.status === "reviewed")} onClick={() => onApprove(allItems)}><Icon name="check" size={17} />Согласовать весь комплект · {allItems.length}</button>
      <small>Пакетное согласование — ваше решение. Замечания агента и предложения правок сохраняются отдельно.</small>
    </div>
    <div className="review-checklist-heading"><span>{groups.length} тем и разделов</span><button className="review-text-button" onClick={() => setOpened(opened.length ? [] : groups)}>{opened.length ? "Свернуть список" : "Развернуть список"}</button></div>
    {groups.map(group => {
      const list = items.filter(i => i.topic_id === group);
      const approved = list.filter(i => i.status === "reviewed").length;
      const concerns = list.filter(i => i.status === "needs_changes" || i.status === "stale" || i.findings.some(f => !f.dismissed)).length;
      const open = opened.includes(group);
      return <section className="review-check-group" key={group}>
        <div className="review-check-group-head">
          <input type="checkbox" aria-label={`Выбрать тему: ${list[0].topic_label}`} checked={list.every(i => checked.includes(i.id))}
            ref={el => { if (el) el.indeterminate = list.some(i => checked.includes(i.id)) && !list.every(i => checked.includes(i.id)); }}
            disabled={disabled} onChange={e => toggle(list.map(i => i.id), e.target.checked)} />
          <button className="review-group-toggle" aria-expanded={open} onClick={() => setOpened(open ? opened.filter(g => g !== group) : [...opened, group])}>
            <div><strong>{list[0].topic_label}</strong><span>{approved} / {list.length} согласовано{concerns ? ` · Требуют внимания: ${concerns}` : ""}</span></div><Icon name="chevron" size={17} />
          </button>
          <button className="btn" disabled={disabled || approved === list.length} onClick={() => onApprove(list)}>Согласовать тему</button>
        </div>
        {open && <div className="review-check-rows">{list.map(i => <div className="review-check-row" key={i.id}>
          <input type="checkbox" aria-label={`Выбрать материал: ${i.title}`} checked={checked.includes(i.id)} disabled={disabled} onChange={e => toggle([i.id], e.target.checked)} />
          <button className="review-row-title" disabled={disabled} onClick={() => onOpen(i.id)}><small>{i.label}</small><span>{i.title}</span></button>
          <span className={`review-badge ${i.status}`}>{STATUS[i.status]}</span>
          <button className="review-text-button" disabled={disabled} onClick={() => onOpen(i.id)} aria-label={`Открыть: ${i.title}`}>Открыть</button>
          <button className="review-approve-one" disabled={disabled || i.status === "reviewed"} onClick={() => onApprove([i])} aria-label={`Согласовать материал: ${i.title}`} title="Согласовать этот материал"><Icon name="check" size={18} /></button>
        </div>)}</div>}
      </section>;
    })}
    {checked.length > 0 && <div className="review-selection-bar"><span>Выбрано: <b>{checked.length}</b></span><button className="review-text-button" disabled={disabled} onClick={() => onChecked([])}>Снять выбор</button><button className="btn primary" disabled={disabled} onClick={() => onApprove(allItems.filter(i => checked.includes(i.id)))}>Согласовать выбранные · {checked.length}</button></div>}
  </div>;
}
