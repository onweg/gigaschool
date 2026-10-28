import { AnimatePresence, motion } from "framer-motion";
import { useEffect, useRef, useState, type ReactNode } from "react";
import type { ParsedReference } from "../lib/api";
import type { SessionState } from "../lib/types";
import { References } from "./References";
import { QuestionAnswer } from "./QuestionAnswer";
import { Icon, type IconName } from "./Icon";
import type { SectionKey } from "./Sidebar";
import { formatDuration, StatusLine, type MarkKind } from "./Motion";

export type Message = { role: "user" | "bot" | "sys"; text: string };

const STEPS: { key: SessionState["stage"][]; label: string }[] = [
  { key: ["created", "clarifying"], label: "Запрос" },
  { key: ["skeleton"], label: "Каркас" },
  { key: ["approved"], label: "Утверждение" },
  { key: ["content", "ready"], label: "Комплект" },
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

const EXAMPLES: { icon: IconName; title: string; detail: string; text: string }[] = [
  { icon: "plan", title: "Анализ данных на Python", detail: "72 часа · сотрудники банков", text: "Программа повышения квалификации по анализу данных на Python на 72 часа для сотрудников банков, начинающих изучать Python. Очно-заочно, итоговая аттестация — защита проекта." },
  { icon: "spark", title: "Первый шаг в ИИ", detail: "24 часа · студенты", text: "Общеразвивающая программа по основам искусственного интеллекта на 24 часа для студентов без подготовки, очно. Итоговая аттестация — тест." },
  { icon: "report", title: "Кибербезопасность", detail: "36 часов · госслужащие", text: "Программа повышения квалификации по кибербезопасности на 36 часов для госслужащих с базовыми навыками работы на компьютере, заочно. Итоговая аттестация — тест." },
];

/** Единая лента: диалог, интерактивная программа и действия над ней. */
export function ChatWorkspace({
  session, messages, draft, onDraft, onSubmit, answers, onAnswers, onSendAnswers,
  references, onReferences, onApprove, exports, exportUrl, busy, working, retry,
  children, section, navigationVersion, onSelect,
}: {
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
  onApprove: () => void;
  exports: { kind: string; label: string; ext: string }[];
  exportUrl: (kind: string) => string;
  busy: string | null;
  working: boolean;
  retry: (() => void) | null;
  children: ReactNode;
  section: SectionKey;
  navigationVersion: number;
  onSelect: (key: SectionKey) => void;
}) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const artifactRef = useRef<HTMLDivElement>(null);
  const latestRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const [since, setSince] = useState(0);
  const [tick, setTick] = useState(0);
  const [showLatest, setShowLatest] = useState(false);
  const welcome = !session && messages.length <= 1;
  const position = ORDER.indexOf(session?.stage ?? "created");

  function scrollTo(target: HTMLElement | null) {
    const container = scrollRef.current;
    if (!target || !container) return;
    const top = target.getBoundingClientRect().top - container.getBoundingClientRect().top + container.scrollTop - 24;
    container.scrollTo({ top, behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth" });
  }

  useEffect(() => {
    if (!working) { setSince(0); setTick(0); return; }
    const serverStart = session?.phase_started_at ? Date.parse(session.phase_started_at) : NaN;
    setSince((prev) => Number.isFinite(serverStart) ? serverStart : prev || Date.now());
  }, [working, session?.phase_started_at]);

  useEffect(() => {
    if (!working) return;
    const timer = setInterval(() => setTick((n) => n + 1), 2600);
    return () => clearInterval(timer);
  }, [working]);

  useEffect(() => {
    if (!welcome) scrollTo(latestRef.current);
  }, [messages.length, session?.questions.length]);

  useEffect(() => {
    if (session?.program && section !== "stats") {
      const frame = requestAnimationFrame(() => scrollTo(artifactRef.current));
      return () => cancelAnimationFrame(frame);
    }
  }, [section, navigationVersion, session?.id, Boolean(session?.program)]);

  useEffect(() => {
    if (welcome) scrollRef.current?.scrollTo({ top: 0 });
  }, [welcome]);

  useEffect(() => {
    const input = inputRef.current;
    if (input) { input.style.height = "auto"; input.style.height = `${Math.min(input.scrollHeight, 160)}px`; }
  }, [draft]);

  const lastEvent = session?.events?.[session.events.length - 1];
  const step = session?.stage === "content"
    ? { mark: STAGE_MARK[lastEvent?.stage ?? ""] ?? "parse", label: lastEvent?.message?.split(":")[0] ?? "Собираю содержание" }
    : (busy ? BUSY_STEP[busy] : undefined)
      ?? (busy === "skeleton" ? SKELETON_STEPS[tick % SKELETON_STEPS.length] : { mark: "parse" as MarkKind, label: busy === "refresh" ? "Проверяю актуальность" : busy === "adapt" ? "Адаптирую программу" : "Работаю над программой" });
  const blocking = (session?.report?.findings ?? []).filter((f) => f.severity === "error");

  function submit() { if (!working && draft.trim()) onSubmit(draft); }

  return (
    <section className={`chat-workspace ${welcome ? "is-welcome" : ""}`} aria-label="Диалог с агентом-методистом">
      <div className="workspace-steps" aria-label="Этапы создания программы">
        {STEPS.map((step, i) => {
          const active = step.key.includes(session?.stage ?? "created");
          const done = position > ORDER.indexOf(step.key[step.key.length - 1]) || (session?.stage === "ready" && i === 3);
          return <div key={step.label} className={`workspace-step ${active ? "active" : ""} ${done ? "done" : ""}`} aria-current={active ? "step" : undefined}>
            <span className="step-number">{done ? <Icon name="check" size={13} /> : String(i + 1).padStart(2, "0")}</span>
            <span>{step.label}</span>{i < STEPS.length - 1 && <span className="step-connector" />}
          </div>;
        })}
      </div>
      {session?.stage === "content" && <div className="progress" />}
      <div className="conversation-scroll" ref={scrollRef} onScroll={() => {
        const el = scrollRef.current;
        if (el) setShowLatest(el.scrollHeight - el.clientHeight - el.scrollTop > 350);
      }}>
        <div className="conversation-inner">
          {welcome ? (
            <div className="welcome">
              <div className="welcome-mark"><Icon name="spark" size={34} /></div>
              <h1>Создайте образовательную<br />программу <span>с помощью ИИ</span></h1>
              <div className="welcome-features">
                <span><Icon name="program" size={15} />Шаблоны АЦР</span>
                <span><Icon name="report" size={15} />Проверка часов и компетенций</span>
                <span><Icon name="exports" size={15} />Готовые документы</span>
              </div>
              <div className="examples-label">Начните с идеи <span>или опишите свою ниже</span></div>
              <div className="example-grid">
                {EXAMPLES.map((example) => (
                  <button className="example-card" key={example.title} onClick={() => { onDraft(example.text); inputRef.current?.focus(); }}>
                    <span className="example-icon"><Icon name={example.icon} size={20} /></span>
                    <strong>{example.title}</strong><span className="example-detail">{example.detail}</span>
                    <Icon name="arrow" size={16} />
                  </button>
                ))}
              </div>
            </div>
          ) : (
            <>
              <div className="conversation-date">Совместная работа над программой</div>
              <div className="messages" role="log" aria-label="Сообщения" aria-live="polite" aria-relevant="additions">
                {messages.slice(1).map((message, i) => (
                  <div key={i} ref={i === messages.length - 2 ? latestRef : undefined} className={`conversation-message ${message.role}`}>
                    {message.role === "bot" && <span className="agent-mark"><Icon name="spark" size={19} /></span>}
                    <div>{message.role === "bot" && <span className="message-author">Агент-методист</span>}<div className="message-text">{message.text}</div></div>
                  </div>
                ))}
              </div>
              <AnimatePresence>
                {session?.questions.length ? (
                  <motion.div
                    className="q-block"
                    initial={{ opacity: 0, y: 12 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0, y: -8 }}
                    transition={{ duration: 0.4, ease: [0.16, 1, 0.3, 1] }}
                  >
                    <div className="q-heading">
                      <div className="q-title">Уточнения перед сборкой</div>
                      <p>Выберите вариант или напишите свой ответ. Выбранный вариант можно дополнить и изменить.</p>
                    </div>
                    {session.questions.map((question) => (
                      <QuestionAnswer
                        key={question.field}
                        field={question.field}
                        question={question.question}
                        value={answers[question.field] ?? ""}
                        onChange={(value) => onAnswers({ ...answers, [question.field]: value })}
                        onSubmit={onSendAnswers}
                        disabled={working}
                      />
                    ))}
                    <button
                      className="btn primary"
                      disabled={working || !session.questions.some((question) => answers[question.field]?.trim())}
                      onClick={onSendAnswers}
                    >
                      {busy === "answers" ? "Отправляю…" : "Ответить"}
                    </button>
                  </motion.div>
                ) : null}
              </AnimatePresence>


              {session?.program && <div className="artifact-anchor" ref={artifactRef}>{children}</div>}
              {working && since > 0 && <div className="working-status" role="status"><StatusLine label={step.label} mark={step.mark} since={since} /></div>}
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
                      Каркас готов к утверждению
                      {session.timings?.skeleton ? (
                        <span className="q-time">
                          за {formatDuration(session.timings.skeleton)}
                        </span>
                      ) : null}
                    </div>
                    {blocking.length > 0 ? (
                      <>
                        <div className="act-note">
                          Автопроверка не пускает дальше — что исправить:
                        </div>
                        {blocking.slice(0, 3).map((finding, i) => (
                          <div key={i} className="act-err">
                            {finding.message}
                            {finding.hint ? <div className="act-hint">→ {finding.hint}</div> : null}
                          </div>
                        ))}
                        {blocking.length > 3 && (
                          <div className="act-note">
                            и ещё {blocking.length - 3} — в разделе «Автопроверка»
                          </div>
                        )}
                      </>
                    ) : (
                      <div className="act-note">
                        Часы сходятся. Дальше агент наполнит темы, тесты с ключами,
                        задания семинаров и слайды.
                      </div>
                    )}
                    <button
                      className="btn primary"
                      disabled={working || blocking.length > 0}
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
                        <a key={item.kind} href={exportUrl(item.kind)} download>
                          {item.label}
                          <span className="ext">.{item.ext}</span>
                        </a>
                      ))}
                    </div>
                  </motion.div>
                ) : null}
              </AnimatePresence>


              {retry && !working && <div className="act-block"><div className="act-note">Сессия сохранена. Можно продолжить сборку.</div><button className="btn primary" onClick={retry}>Повторить сборку</button></div>}
            </>
          )}
        </div>
      </div>
      <div className="composer-dock">
        {!welcome && showLatest && <button className="latest-button" aria-label="К последним сообщениям" onClick={() => scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" })}><Icon name="chevron" size={18} /></button>}
        <div className="workspace-composer">
          {session?.program && <div className="composer-context"><Icon name="program" size={14} /><span>{session.program.meta.title}</span><button onClick={() => onSelect("plan")}>Открыть план <Icon name="arrow" size={13} /></button></div>}
          <label className="sr-only" htmlFor="request">{session ? "Сообщение агенту" : "Опишите образовательную программу"}</label>
          <textarea ref={inputRef} id="request" rows={2} value={draft}
            placeholder={session?.program ? session.stage === "skeleton" ? "Ваше пожелание к программе…" : "Что изменить? Например: тема 2.1 — добавить практику" : session ? "Ответьте на уточнения выше или опишите программу заново" : "Какую программу создадим? Тема, аудитория, количество часов…"}
            onChange={(event) => onDraft(event.target.value)}
            onKeyDown={(event) => { if (event.key === "Enter" && (event.metaKey || event.ctrlKey) && !event.nativeEvent.isComposing) { event.preventDefault(); submit(); } }} />
          <div className="composer-tools">
            <References items={references} onChange={onReferences} disabled={Boolean(session) || working} />
            <span className="composer-shortcut">⌘ / Ctrl ↵</span>
            <button className="send-button" disabled={working || !draft.trim()} onClick={submit} aria-label="Отправить сообщение"><Icon name="send" size={20} /></button>
          </div>
        </div>
        <div className="composer-footnote">{session?.stage === "skeleton" ? "Пожелания к темам будут учтены при сборке комплекта после утверждения." : "Агент готовит программу. Вы проверяете и принимаете решения."}</div>
      </div>
    </section>
  );
}
