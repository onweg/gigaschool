import { motion } from "framer-motion";
import { Fragment } from "react";
import type { Program, SessionState } from "../lib/types";

export type SectionKey =
  | "program"
  | "competencies"
  | "plan"
  | "report"
  | "kit"
  | "exports"
  | "stats";

const ICONS: Record<SectionKey, string> = {
  program: "◆",
  competencies: "◎",
  plan: "▤",
  report: "✓",
  kit: "❏",
  exports: "↓",
  stats: "▦",
};

const LABELS: Record<SectionKey, string> = {
  program: "Программа",
  competencies: "Компетенции",
  plan: "Учебный план",
  report: "Автопроверка",
  kit: "Комплект",
  exports: "Выгрузки",
  stats: "Статистика",
};

/** Навигация по разделам собранной программы — она же индикатор готовности. */
export function Sidebar({
  program,
  session,
  active,
  onSelect,
  issues,
}: {
  program: Program | null;
  session: SessionState | null;
  active: SectionKey;
  onSelect: (key: SectionKey) => void;
  issues: number;
}) {
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
    stats: "",
  };

  const ready: Record<SectionKey, boolean> = {
    program: Boolean(program),
    competencies: Boolean(program?.outcomes.length),
    plan: Boolean(program?.modules.length),
    report: Boolean(session?.report),
    kit: kitCount > 0,
    exports: Boolean(program),
    // Панель телеметрии не зависит от текущей программы: данные копятся всегда.
    stats: true,
  };

  return (
    <aside className="sidebar">
      <div className="sidebar-head">
        <div className="logo">
          АЦР<sup>®</sup>
        </div>
        {issues > 0 && <span className="badge">{issues}</span>}
      </div>

      <nav className="nav">
        {(Object.keys(LABELS) as SectionKey[]).map((key) => (
          <Fragment key={key}>
          {key === "stats" && <div className="nav-split" />}
          <button
            className={`nav-item ${active === key ? "active" : ""}`}
            disabled={!ready[key]}
            onClick={() => onSelect(key)}
          >
            <span className="ico">{ICONS[key]}</span>
            {LABELS[key]}
            {counts[key] && <span className="count">{counts[key]}</span>}
            {active === key && (
              <motion.span
                layoutId="nav-active"
                style={{ position: "absolute", inset: 0, borderRadius: 16 }}
                transition={{ type: "spring", stiffness: 300, damping: 30 }}
              />
            )}
          </button>
          </Fragment>
        ))}
      </nav>

      <div className="sidebar-foot">
        <div className="avatar">МТ</div>
        <div className="who">Методист</div>
        <div className="role">Академия цифрового развития</div>
      </div>
    </aside>
  );
}
