import { AnimatePresence, motion } from "framer-motion";
import { Fragment, useState } from "react";
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
  /** Пересобрать тему: пожелание методиста и, если он его менял, новое название. */
  onRegenerate?: (topicId: string, instruction: string, title: string) => void;
  busyTopic?: string | null;
}) {
  // Тема, у которой открыта форма доработки. Форма одна на таблицу: править
  // две темы одновременно методисту незачем, а так не плодим состояние.
  const [editing, setEditing] = useState<string | null>(null);
  const [instruction, setInstruction] = useState("");
  const [title, setTitle] = useState("");

  function open(topicId: string, currentTitle: string) {
    setEditing(topicId);
    setInstruction("");
    setTitle(currentTitle);
  }

  function submit(topicId: string) {
    if (!onRegenerate) return;
    onRegenerate(topicId, instruction.trim(), title.trim());
    setEditing(null);
  }

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
    <div className="table-scroll">
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
                      className="patch-btn"
                      disabled={busyTopic === topic.id}
                      onClick={() => open(topic.id, topic.title)}
                      title="Переделать эту тему по вашему указанию"
                      whileHover={{ scale: 1.04 }}
                      whileTap={{ scale: 0.97 }}
                    >
                      <motion.span
                        className="patch-ico"
                        animate={busyTopic === topic.id ? { rotate: 360 } : { rotate: 0 }}
                        transition={
                          busyTopic === topic.id
                            ? { repeat: Infinity, duration: 1.1, ease: "linear" }
                            : {}
                        }
                      >
                        ↻
                      </motion.span>
                      {busyTopic === topic.id ? "переделываю…" : "переделать"}
                    </motion.button>
                  )}

                  <AnimatePresence>
                    {editing === topic.id && (
                      <motion.div
                        className="patch-form"
                        initial={{ opacity: 0, height: 0 }}
                        animate={{ opacity: 1, height: "auto" }}
                        exit={{ opacity: 0, height: 0 }}
                        transition={{ duration: 0.22, ease: [0.16, 1, 0.3, 1] }}
                      >
                        <label>
                          Название темы
                          <input
                            value={title}
                            onChange={(e) => setTitle(e.target.value)}
                            placeholder={topic.title}
                          />
                        </label>
                        <label>
                          Что поправить
                          <textarea
                            value={instruction}
                            onChange={(e) => setInstruction(e.target.value)}
                            placeholder="например: усложнить, добавить примеры из банков, меньше теории"
                            rows={2}
                            autoFocus
                            onKeyDown={(e) => {
                              if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) submit(topic.id);
                              if (e.key === "Escape") setEditing(null);
                            }}
                          />
                        </label>
                        <div className="patch-actions">
                          <span className="hint">⌘/Ctrl + Enter</span>
                          <button className="ghost" onClick={() => setEditing(null)}>
                            Отмена
                          </button>
                          <button className="accent" onClick={() => submit(topic.id)}>
                            Переделать тему
                          </button>
                        </div>
                      </motion.div>
                    )}
                  </AnimatePresence>
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
    </div>
  );
}
