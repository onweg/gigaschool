import { motion } from "framer-motion";
import { Fragment, useEffect, useRef, useState } from "react";
import type { Program, SessionState } from "../lib/types";
import { Icon } from "./Icon";

export type SectionKey =
  | "program"
  | "competencies"
  | "plan"
  | "report"
  | "kit"
  | "exports"
  | "stats"
  | "expert"
  | "review"
  | "approval";

const LABELS: Record<SectionKey, string> = {
  program: "Программа",
  competencies: "Компетенции",
  plan: "Учебный план",
  report: "Автопроверка",
  kit: "Комплект",
  exports: "Выгрузки",
  expert: "Экспертиза",
  review: "Правки эксперта",
  approval: "Согласование",
  stats: "Статистика",
};

/** Навигация по разделам собранной программы — она же индикатор готовности. */
export function Sidebar({
  program,
  session,
  active,
  onSelect,
  issues,
  onReset,
  working,
}: {
  program: Program | null;
  session: SessionState | null;
  active: SectionKey;
  onSelect: (key: SectionKey) => void;
  issues: number;
  onReset: () => void;
  working: boolean;
}) {
  const menu = useRef<HTMLDialogElement>(null);
  const menuButton = useRef<HTMLButtonElement>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  useEffect(() => {
    const desktop = window.matchMedia("(min-width: 761px)");
    const closeOnDesktop = (event: MediaQueryListEvent) => { if (event.matches) menu.current?.close(); };
    desktop.addEventListener("change", closeOnDesktop);
    return () => desktop.removeEventListener("change", closeOnDesktop);
  }, []);
  const kitCount =
    (program?.test_questions.length ?? 0) +
    (program?.seminar_tasks.length ?? 0) +
    (program?.lecture_plans.reduce((acc, p) => acc + p.slides.length, 0) ?? 0);

  const counts: Record<SectionKey, string> = {
    program: program ? `${program.meta.total_hours} ч` : "",
    competencies: program ? String(program.outcomes.length) : "",
    plan: program ? String(program.modules.length) : "",
    report: issues ? String(issues) : program ? "0" : "",
    kit: kitCount ? String(kitCount) : "",
    exports: "",
    stats: "", expert: "", review: "", approval: "",
  };

  const ready: Record<SectionKey, boolean> = {
    program: true,
    competencies: Boolean(program?.outcomes.length),
    plan: Boolean(program?.modules.length),
    report: Boolean(session?.report),
    kit: kitCount > 0,
    exports: Boolean(program),
    // Панель телеметрии не зависит от текущей программы: данные копятся всегда.
    stats: true, expert: Boolean(program), review: Boolean(program), approval: Boolean(program),
  };

  return (
    <aside className="sidebar">
      <div className="sidebar-head">
        <div className="logo">
          АЦР<sup>®</sup>
        </div>
        <div className="brand-caption">Академия<br />цифрового развития</div>
      </div>

      <button className="new-program" aria-label="Новая программа" disabled={working} onClick={onReset}><Icon name="plus" size={18} /><span>Новая программа</span></button>
      <button ref={menuButton} className="mobile-menu-toggle" aria-label="Открыть разделы" aria-controls="mobile-sections" aria-expanded={menuOpen} onClick={() => { menu.current?.showModal(); setMenuOpen(true); }}><Icon name="menu" size={22} /></button>
      <dialog ref={menu} id="mobile-sections" className="mobile-menu-dialog" aria-labelledby="mobile-sections-title" onClose={() => { setMenuOpen(false); if (window.matchMedia("(max-width: 760px)").matches) menuButton.current?.focus(); }} onClick={(event) => {
        if (event.target !== event.currentTarget) return;
        const bounds = event.currentTarget.getBoundingClientRect();
        if (event.clientY < bounds.top || event.clientY > bounds.bottom || event.clientX < bounds.left || event.clientX > bounds.right) menu.current?.close();
      }}>
        <header><div><span>Рабочее пространство</span><h2 id="mobile-sections-title">Ваша программа</h2></div><button autoFocus aria-label="Закрыть разделы" onClick={() => menu.current?.close()}><Icon name="close" /></button></header>
        <nav aria-label="Разделы программы">
          {(Object.keys(LABELS) as SectionKey[]).map(key => <button key={key} className={`mobile-section ${active === key ? "active" : ""}`} aria-current={active === key ? "page" : undefined} disabled={!ready[key]} onClick={() => { onSelect(key); menu.current?.close(); }}><Icon name={key} /><span>{LABELS[key]}</span>{counts[key] && <small>{counts[key]}</small>}{active === key && <Icon name="check" size={18} />}</button>)}
        </nav>
      </dialog>
      <div className="nav-caption">Рабочее пространство</div>
      <nav className="nav" aria-label="Основная навигация">
        {(Object.keys(LABELS) as SectionKey[]).map((key) => (
          <Fragment key={key}>
          {(key === "stats" || key === "expert") && <div className="nav-split" />}
          <button
            className={`nav-item ${active === key ? "active" : ""}`}
            aria-current={active === key ? "page" : undefined}
            disabled={!ready[key]}
            onClick={() => onSelect(key)}
          >
            <span className="ico"><Icon name={key} size={19} /></span>
            {LABELS[key]}
            {counts[key] && <span className="count">{counts[key]}</span>}
            {active === key && (
              <motion.span
                layoutId="nav-active"
                style={{
                  position: "absolute",
                  inset: 0,
                  borderRadius: 16,
                  // Декоративная подсветка не должна ловить клики.
                  pointerEvents: "none",
                }}
                transition={{ type: "spring", stiffness: 300, damping: 30 }}
              />
            )}
          </button>
          </Fragment>
        ))}
      </nav>

      <div className="sidebar-foot">
        <div className="avatar">{active === "expert" ? "ЭК" : active === "approval" ? "РК" : "МТ"}</div>
        <div><div className="who">{active === "expert" ? "Эксперт" : active === "approval" ? "Руководитель" : "Методист"}</div><div className="role">Ваше рабочее пространство</div></div>
      </div>
    </aside>
  );
}
