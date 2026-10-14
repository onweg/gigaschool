import { motion } from "framer-motion";
import type { Program } from "../lib/types";
import { Reveal } from "./Motion";

export function Kit({ program }: { program: Program }) {
  const slides = program.lecture_plans.reduce((acc, p) => acc + p.slides.length, 0);
  if (!program.test_questions.length && !program.seminar_tasks.length && !slides) return null;

  return (
    <>
      {program.test_questions.length > 0 && (
        <Reveal className="card">
          <h3>Тестовые вопросы · {program.test_questions.length}</h3>
          {program.test_questions.slice(0, 4).map((q, i) => (
            <motion.div
              key={q.id}
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: i * 0.06, duration: 0.5, ease: [0.16, 1, 0.3, 1] }}
              style={{ marginBottom: 16, fontSize: 12.5, lineHeight: 1.6 }}
            >
              <div style={{ marginBottom: 6 }}>
                <span className="tag">{q.id}</span>
                {q.stem}{" "}
                {q.outcome_ids.map((id) => (
                  <span className="tag green" key={id}>
                    {id}
                  </span>
                ))}
              </div>
              {q.options.map((option, oi) => {
                const correct = q.correct.includes(oi);
                return (
                  <div
                    key={oi}
                    style={{
                      color: correct ? "var(--green)" : "var(--muted)",
                      paddingLeft: 14,
                      display: "flex",
                      gap: 8,
                    }}
                  >
                    <span style={{ fontFamily: "var(--font-mono)", fontSize: 10, opacity: 0.8 }}>
                      {correct ? "✓" : "·"}
                    </span>
                    {option}
                  </div>
                );
              })}
            </motion.div>
          ))}
          {program.test_questions.length > 4 && (
            <div className="hint">
              ещё {program.test_questions.length - 4} вопросов — в выгрузке GIFT / CSV
            </div>
          )}
        </Reveal>
      )}

      {program.seminar_tasks.length > 0 && (
        <Reveal className="card" delay={0.05}>
          <h3>Задания для семинаров · {program.seminar_tasks.length}</h3>
          {program.seminar_tasks.slice(0, 3).map((task, i) => (
            <motion.div
              key={task.id}
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: i * 0.07, duration: 0.5, ease: [0.16, 1, 0.3, 1] }}
              style={{ marginBottom: 16, fontSize: 12.5, lineHeight: 1.6 }}
            >
              <div style={{ fontSize: 15, fontWeight: 600, marginBottom: 4 }}>
                {task.title}
              </div>
              <div style={{ color: "var(--text-dim)", marginBottom: 6 }}>{task.statement}</div>
              {task.criteria.map((criterion, ci) => (
                <div key={ci} style={{ paddingLeft: 14, color: "var(--muted)" }}>
                  — {criterion}
                </div>
              ))}
            </motion.div>
          ))}
        </Reveal>
      )}

      {slides > 0 && (
        <Reveal className="card" delay={0.1}>
          <h3>Слайды лекций · {slides}</h3>
          {program.lecture_plans.map((plan) => (
            <div key={plan.id} style={{ marginBottom: 14 }}>
              <div style={{ fontSize: 12.5, marginBottom: 9, color: "var(--text-dim)" }}>
                {plan.title}
              </div>
              <div className="row">
                {plan.slides.map((slide, si) => (
                  <motion.div
                    key={si}
                    className="slide-thumb"
                    initial={{ opacity: 0, y: 10, rotateX: -8 }}
                    animate={{ opacity: 1, y: 0, rotateX: 0 }}
                    whileHover={{ y: -4 }}
                    transition={{ delay: si * 0.05, duration: 0.5, ease: [0.16, 1, 0.3, 1] }}
                  >
                    <div className="t">{slide.title}</div>
                    {slide.bullets.slice(0, 3).map((bullet, bi) => (
                      <div className="b" key={bi}>
                        · {bullet}
                      </div>
                    ))}
                  </motion.div>
                ))}
              </div>
            </div>
          ))}
        </Reveal>
      )}
    </>
  );
}
