import { motion } from "framer-motion";
import { Fragment } from "react";
import type { Program } from "../lib/types";
import { AnimatedNumber } from "./Motion";

const fmt = (value: number) => String(Number(value.toFixed(1)));

function Cell({ value, dim }: { value: number; dim?: boolean }) {
  return (
    <td className="num" style={dim && !value ? { color: "var(--faint)" } : undefined}>
      <AnimatedNumber value={value} format={fmt} />
    </td>
  );
}

export function HoursTable({
  program,
  onRegenerate,
  busyTopic,
}: {
  program: Program;
  onRegenerate?: (topicId: string) => void;
  busyTopic?: string | null;
}) {
  const sum = program.modules.reduce(
    (acc, m) => ({
      lectures: acc.lectures + m.hours.lectures,
      practice: acc.practice + m.hours.practice,
      labs: acc.labs + m.hours.labs,
      self_study: acc.self_study + m.hours.self_study,
      total: acc.total + m.hours.total,
    }),
    { lectures: 0, practice: 0, labs: 0, self_study: 0, total: 0 }
  );
  const grand = sum.total + program.final_attestation.hours;
  const matches = Math.abs(grand - program.meta.total_hours) < 0.01;

  return (
    <table>
      <thead>
        <tr>
          <th style={{ width: 54 }}>№</th>
          <th>Раздел / тема</th>
          <th className="num">Всего</th>
          <th className="num">ЛК</th>
          <th className="num">ПР</th>
          <th className="num">ЛБ</th>
          <th className="num">СРС</th>
          <th style={{ width: 118 }}>РО</th>
        </tr>
      </thead>
      <tbody>
        {program.modules.map((module, mi) => (
          <Fragment key={module.id}>
            <motion.tr
              className="module"
              initial={{ opacity: 0, x: -10 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ delay: mi * 0.07, duration: 0.5, ease: [0.16, 1, 0.3, 1] }}
            >
              <td>{module.index}.</td>
              <td>{module.title}</td>
              <Cell value={module.hours.total} />
              <Cell value={module.hours.lectures} dim />
              <Cell value={module.hours.practice} dim />
              <Cell value={module.hours.labs} dim />
              <Cell value={module.hours.self_study} dim />
              <td>{module.assessment_form ? <span className="tag">контроль</span> : null}</td>
            </motion.tr>
            {module.topics.map((topic, ti) => (
              <motion.tr
                className="topic"
                key={topic.id}
                initial={{ opacity: 0, x: -10 }}
                animate={{ opacity: 1, x: 0 }}
                transition={{
                  delay: mi * 0.07 + ti * 0.035 + 0.05,
                  duration: 0.5,
                  ease: [0.16, 1, 0.3, 1],
                }}
              >
                <td>{topic.code}</td>
                <td>
                  {topic.title}
                  {onRegenerate && (
                    <motion.button
                      className="tiny ghost"
                      style={{ marginLeft: 8 }}
                      disabled={busyTopic === topic.id}
                      onClick={() => onRegenerate(topic.id)}
                      title="Пересобрать только эту тему"
                      whileHover={{ color: "var(--green)" }}
                      animate={busyTopic === topic.id ? { rotate: 360 } : { rotate: 0 }}
                      transition={
                        busyTopic === topic.id
                          ? { repeat: Infinity, duration: 1.1, ease: "linear" }
                          : {}
                      }
                    >
                      ↻
                    </motion.button>
                  )}
                </td>
                <Cell value={topic.hours.total} />
                <Cell value={topic.hours.lectures} dim />
                <Cell value={topic.hours.practice} dim />
                <Cell value={topic.hours.labs} dim />
                <Cell value={topic.hours.self_study} dim />
                <td>
                  {topic.outcome_ids.map((id) => (
                    <span className="tag green" key={id}>
                      {id}
                    </span>
                  ))}
                </td>
              </motion.tr>
            ))}
          </Fragment>
        ))}
        <tr className="module">
          <td />
          <td>Итоговая аттестация — {program.final_attestation.form || "—"}</td>
          <Cell value={program.final_attestation.hours} />
          <td className="num" style={{ color: "var(--faint)" }}>—</td>
          <td className="num" style={{ color: "var(--faint)" }}>—</td>
          <td className="num" style={{ color: "var(--faint)" }}>—</td>
          <td className="num" style={{ color: "var(--faint)" }}>—</td>
          <td />
        </tr>
        <tr className="total">
          <td />
          <td>Итого · запрошено {fmt(program.meta.total_hours)} ч</td>
          <td
            className="num"
            style={{ color: matches ? "var(--green)" : "var(--red)", fontWeight: 700 }}
          >
            <AnimatedNumber value={grand} format={fmt} />
          </td>
          <Cell value={sum.lectures} />
          <Cell value={sum.practice} />
          <Cell value={sum.labs} />
          <Cell value={sum.self_study} />
          <td>
            <motion.span
              className={`pill ${matches ? "accent" : "err"}`}
              initial={{ opacity: 0, scale: 0.9 }}
              animate={{ opacity: 1, scale: 1 }}
              transition={{ delay: 0.5 }}
            >
              <span className="dot" /> {matches ? "сходится" : "расхождение"}
            </motion.span>
          </td>
        </tr>
      </tbody>
    </table>
  );
}
