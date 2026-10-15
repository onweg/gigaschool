import { AnimatePresence, motion } from "framer-motion";
import { useEffect, useRef, useState } from "react";
import type { ParsedReference } from "../lib/api";
import type { SessionState } from "../lib/types";
import { References } from "./References";
import { formatDuration, StatusLine, Typing, type MarkKind } from "./Motion";

export type Message = { role: "user" | "bot" | "sys"; text: string };

const STEPS: { key: SessionState["stage"][]; label: string }[] = [
  { key: ["created", "clarifying"], label: "Запрос" },
  { key: ["skeleton"], label: "Каркас" },
  { key: ["approved"], label: "Утверждение" },
  { key: ["content"], label: "Комплект" },
  { key: ["ready"], label: "Проверка" },
];

const ORDER: SessionState["stage"][] = [
  "created",
  "clarifying",
  "skeleton",
  "approved",
  "content",
  "ready",
];

/** Чем агент занят на каркасе: бэкенд отдаёт результат одним ответом,
 *  поэтому шаги показываем по таймеру — они соответствуют порядку в pipeline. */
const SKELETON_STEPS: { mark: MarkKind; label: string }[] = [
  { mark: "parse", label: "Разбираю запрос и подбираю шаблон" },
  { mark: "legal", label: "Сверяюсь с нормативкой и профстандартами" },
  { mark: "map", label: "Строю карту компетенций" },
  { mark: "plan", label: "Раскладываю модули и темы" },
  { mark: "hours", label: "Считаю часы и проверяю суммы" },
];

const BUSY_STEP: Record<string, { mark: MarkKind; label: string }> = {
  create: { mark: "parse", label: "Разбираю запрос" },
  answers: { mark: "parse", label: "Учитываю уточнения" },
  approve: { mark: "check", label: "Утверждаю каркас" },
};

/** Стадию события проставляет пайплайн — значок меняется строго под шаг. */
const STAGE_MARK: Record<string, MarkKind> = {
  content: "parse",
  tests: "tests",
  seminars: "seminars",
  lectures: "slides",
  ready: "check",
  skeleton: "plan",
};

const FIELD_LABEL: Record<string, string> = {
  program_type: "Тип программы",
  total_hours: "Объём, часов",
  audience: "Аудитория",
  entry_level: "Входной уровень",
  study_form: "Форма обучения",
  attestation_form: "Итоговая аттестация",
};

/**
 * Чат агента как встраиваемый виджет: именно в таком виде он будет жить
 * на странице платформы рядом с остальным интерфейсом методиста.
 */
export function ChatWidget({
  open,
  onOpenChange,
  session,
  messages,
  draft,
  onDraft,
  onSubmit,
  answers,
  onAnswers,
  onSendAnswers,
  references,
  onReferences,
  samples,
  onApprove,
  exports,
  exportUrl,
  busy,
  working,
  retry,
  onReset,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  session: SessionState | null;
  messages: Message[];
  draft: string;
  onDraft: (value: string) => void;
  onSubmit: (text: string) => void;
  answers: Record<string, string>;
  onAnswers: (next: Record<string, string>) => void;
  onSendAnswers: () => void;
  references: ParsedReference[];
  onReferences: (next: ParsedReference[]) => void;
  samples: { n: string; label: string; text: string }[];
  onApprove: () => void;
  exports: { kind: string; label: string; ext: string }[];
  exportUrl: (kind: string) => string;
  busy: string | null;
  working: boolean;
  retry: (() => void) | null;
  onReset: () => void;
}) {
  const logRef = useRef<HTMLDivElement>(null);
  const position = ORDER.indexOf(session?.stage ?? "created");
  const [since, setSince] = useState(0);
  const [tick, setTick] = useState(0);

  // Секундомер стартует в момент, когда агент берётся за работу. Для содержания
  // точка отсчёта приходит с сервера — переживает перезагрузку страницы.
  useEffect(() => {
    if (!working) {
      setSince(0);
      setTick(0);
      return;
    }
    const serverStart = session?.phase_started_at
      ? Date.parse(session.phase_started_at)
      : NaN;
    setSince((prev) =>
      Number.isFinite(serverStart) ? serverStart : prev || Date.now()
    );
  }, [working, session?.phase_started_at]);

  useEffect(() => {
    if (!working) return;
    const timer = setInterval(() => setTick((n) => n + 1), 2600);
    return () => clearInterval(timer);
  }, [working]);

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, session?.questions.length, working, open]);

  const trace = (session?.events ?? []).slice(-4).reverse();
  const lastEvent = session?.events?.[session.events.length - 1];
  const step =
    session?.stage === "content"
      ? {
          mark: STAGE_MARK[lastEvent?.stage ?? ""] ?? "parse",
          label: lastEvent?.message?.split(":")[0] ?? "Собираю содержание",
        }
      : (busy ? BUSY_STEP[busy] : undefined) ??
        SKELETON_STEPS[tick % SKELETON_STEPS.length];
  // Утверждение каркаса бэкенд отклоняет, пока есть ошибки автопроверки.
  const blocking = (session?.report?.findings ?? []).filter((f) => f.severity === "error");

  return (
    <>
      <AnimatePresence>
        {!open && (
          <motion.button
            key="launcher"
            className="widget-launcher"
            onClick={() => onOpenChange(true)}
            initial={{ opacity: 0, scale: 0.9, y: 10 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.9, y: 10 }}
            whileHover={{ scale: 1.03 }}
            transition={{ type: "spring", stiffness: 320, damping: 26 }}
          >
            {working ? <Typing /> : "✦"} Агент-методист
          </motion.button>
        )}
      </AnimatePresence>

      <AnimatePresence>
        {open && (
          <motion.section
            key="widget"
            className="widget"
            initial={{ opacity: 0, scale: 0.96, y: 18 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.96, y: 18 }}
            transition={{ type: "spring", stiffness: 300, damping: 28 }}
          >
            <header className="widget-head">
              <div className="ava">АЦ</div>
              <div>
                <div className="ttl">Агент-методист</div>
                <div className="sub">
                  {session ? `сессия ${session.id}` : "программы по шаблонам АЦР"}
                </div>
              </div>
              <button className="x" onClick={() => onOpenChange(false)} title="Свернуть">
                ✕
              </button>
            </header>

            <div className="steps">
              {STEPS.map((step, i) => {
                const stepPosition = ORDER.indexOf(step.key[step.key.length - 1]);
                const active = step.key.includes(session?.stage ?? "created");
                const done = position > stepPosition;
                return (
                  <div
                    key={step.label}
                    className={`step ${active ? "active" : ""} ${done ? "done" : ""}`}
                  >
                    <span className="idx">{done ? "✓" : i + 1}</span>
                    {step.label}
                  </div>
                );
              })}
            </div>

            {session?.stage === "content" && <div className="progress" />}

            <div className="chat-log" ref={logRef}>
              <AnimatePresence initial={false}>
                {messages.map((message, i) => (
                  <motion.div
                    key={i}
                    className={`msg ${message.role}`}
                    initial={{ opacity: 0, y: 10 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ duration: 0.35, ease: [0.16, 1, 0.3, 1] }}
                  >
                    {message.text}
                  </motion.div>
                ))}
              </AnimatePresence>

              {working && since > 0 && (
                <motion.div
                  initial={{ opacity: 0, y: 6 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ duration: 0.3 }}
                >
                  <StatusLine label={step.label} mark={step.mark} since={since} />
                </motion.div>
              )}

              {!session && (
                <motion.div
                  className="samples"
                  initial={{ opacity: 0, y: 8 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ delay: 0.15, duration: 0.4 }}
                >
                  <div className="samples-title">Запросы из кейса</div>
                  {samples.map((sample) => (
                    <button key={sample.label} onClick={() => onDraft(sample.text)}>
                      <span className="n">{sample.n}</span>
                      {sample.label}
                    </button>
                  ))}
                </motion.div>
              )}

              <AnimatePresence>
                {session?.questions.length ? (
                  <motion.div
                    className="q-block"
                    initial={{ opacity: 0, y: 12 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0, y: -8 }}
                    transition={{ duration: 0.4, ease: [0.16, 1, 0.3, 1] }}
                  >
                    <div className="q-title">Уточнения перед сборкой</div>
                    {session.questions.map((question) => (
                      <div key={question.field}>
                        <label>{question.question}</label>
                        <input
                          type="text"
                          value={answers[question.field] ?? ""}
                          placeholder={FIELD_LABEL[question.field] ?? question.field}
                          onChange={(event) =>
                            onAnswers({ ...answers, [question.field]: event.target.value })
                          }
                          onKeyDown={(event) => {
                            if (event.key === "Enter") onSendAnswers();
                          }}
                        />
                      </div>
                    ))}
                    <button
                      className="btn primary"
                      disabled={busy !== null}
                      onClick={onSendAnswers}
                    >
                      {busy === "answers" ? "Отправляю…" : "Ответить"}
                    </button>
                  </motion.div>
                ) : null}
              </AnimatePresence>

              <AnimatePresence>
                {session?.stage === "skeleton" ? (
                  <motion.div
                    className="act-block"
                    initial={{ opacity: 0, y: 12 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0, y: -8 }}
                    transition={{ duration: 0.4, ease: [0.16, 1, 0.3, 1] }}
                  >
                    <div className="q-title">
                      Каркас собран — шаг 3 из 5
                      {session.timings?.skeleton ? (
                        <span className="q-time">
                          за {formatDuration(session.timings.skeleton)}
                        </span>
                      ) : null}
                    </div>
                    {blocking.length > 0 ? (
                      <>
                        <div className="act-note">
                          Автопроверка не пускает дальше. Поправьте часы в учебном
                          плане и нажмите пересчёт:
                        </div>
                        {blocking.slice(0, 3).map((finding, i) => (
                          <div key={i} className="act-err">
                            {finding.code} · {finding.message}
                          </div>
                        ))}
                      </>
                    ) : (
                      <div className="act-note">
                        Часы сходятся. Дальше агент наполнит темы, тесты с ключами,
                        задания семинаров и слайды.
                      </div>
                    )}
                    <button
                      className="btn primary"
                      disabled={busy !== null || blocking.length > 0}
                      onClick={onApprove}
                    >
                      {busy === "approve" || busy === "content"
                        ? "Собираю…"
                        : "Утвердить каркас и собрать комплект"}
                    </button>
                  </motion.div>
                ) : null}

                {session?.stage === "ready" ? (
                  <motion.div
                    key="done"
                    className="act-block"
                    initial={{ opacity: 0, y: 12 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ duration: 0.4, ease: [0.16, 1, 0.3, 1] }}
                  >
                    <div className="q-title">
                      Комплект готов — забирайте файлы
                      {session.timings?.content ? (
                        <span className="q-time">
                          за {formatDuration(session.timings.content)}
                        </span>
                      ) : null}
                    </div>
                    <div className="exports">
                      {exports.map((item) => (
                        <a key={item.kind} href={exportUrl(item.kind)}>
                          {item.label}
                          <span className="ext">.{item.ext}</span>
                        </a>
                      ))}
                    </div>
                  </motion.div>
                ) : null}
              </AnimatePresence>

              {retry && !working && (
                <motion.div
                  className="act-block"
                  initial={{ opacity: 0, y: 10 }}
                  animate={{ opacity: 1, y: 0 }}
                >
                  <div className="act-note">
                    Сессия цела: каркас, ответы и референсы сохранены.
                  </div>
                  <button className="btn primary" onClick={retry}>
                    Повторить сборку
                  </button>
                </motion.div>
              )}

              {trace.length > 0 && (
                <div className="trace">
                  {trace.map((event, i) => (
                    <motion.div
                      key={`${event.at}-${i}`}
                      className="msg sys"
                      initial={{ opacity: 0 }}
                      animate={{ opacity: 1 - i * 0.16 }}
                    >
                      <b>{event.stage}</b> · {event.message}
                    </motion.div>
                  ))}
                </div>
              )}
            </div>

            <div className="composer">
              <References
                items={references}
                onChange={onReferences}
                disabled={Boolean(session)}
              />

              <textarea
                rows={2}
                value={draft}
                placeholder="Программа повышения квалификации на 72 часа по анализу данных на Python для сотрудников банков, очно-заочная, итоговая аттестация — защита проекта"
                onChange={(event) => onDraft(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
                    onSubmit(draft);
                  }
                }}
              />

              <div className="row spread">
                <span className="hint">⌘/Ctrl + Enter</span>
                <div className="row">
                  {session && (
                    <button className="btn ghost" onClick={onReset}>
                      Новая
                    </button>
                  )}
                  <button
                    className="btn primary"
                    disabled={busy !== null || !draft.trim()}
                    onClick={() => onSubmit(draft)}
                  >
                    {busy === "create" ? "Разбираю…" : "Отправить"}
                  </button>
                </div>
              </div>
            </div>
          </motion.section>
        )}
      </AnimatePresence>
    </>
  );
}
