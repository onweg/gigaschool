import { useEffect, useRef, useState } from "react";
import { api, ApiError } from "../lib/api";
import type { MaterialValue, ReviewData, ReviewItem } from "../lib/review";
import type { SessionState } from "../lib/types";
import { ReviewChecklist } from "./ReviewChecklist";
import { Icon } from "./Icon";
import type { SectionKey } from "./Sidebar";

const STATUS = { todo: "Не проверено", reviewed: "Согласовано", deferred: "Отложено", needs_changes: "Нужна доработка", stale: "Повторная проверка" };
const LABELS: Record<string, string> = { content: "Содержание", activities: "Практика", stem: "Вопрос", options: "Варианты ответа", correct: "Правильные ответы", explanation: "Объяснение ответа", title: "Название", statement: "Задание", deliverable: "Результат работы", criteria: "Критерии оценки", objectives: "Цели занятия", timing: "План занятия", slides: "Слайды", description: "Описание" };
const QUICK = ["Уточнить формулировку", "Добавить понятный пример", "Проверить ключ и объяснение ответа", "Согласовать задание с материалом темы"];
const date = (s: string) => new Date(s).toLocaleString("ru-RU", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });

function Material({ value }: { value: MaterialValue }) {
  return <div className="review-material-text">{Object.entries(value).filter(([k]) => k !== "correct").map(([key, val]) => (
    <section key={key}>
      <h4>{LABELS[key] ?? key}</h4>
      {key === "slides" ? (val as { title: string; bullets: string[]; notes: string }[]).map((slide, i) =>
        <div className="review-slide" key={i}><strong>{i + 1}. {slide.title}</strong><ul>{slide.bullets.map((b, j) => <li key={j}>{b}</li>)}</ul>{slide.notes && <p>{slide.notes}</p>}</div>)
        : Array.isArray(val) ? <ul className={key === "options" ? "review-test-options" : ""}>{(val as string[]).map((v, i) =>
          <li key={i} className={key === "options" && (value.correct as number[] ?? []).includes(i) ? "is-correct" : ""}>
            {key === "options" && <span className="option-letter">{String.fromCharCode(1040 + i)}</span>}{v}
            {key === "options" && (value.correct as number[] ?? []).includes(i) && <span className="correct-label"><Icon name="check" size={13} />Верный</span>}
          </li>)}</ul>
          : <p>{val || <span className="review-muted">Пока не заполнено</span>}</p>}
    </section>
  ))}</div>;
}

function Editor({ value, onChange, disabled }: { value: MaterialValue; onChange: (v: MaterialValue) => void; disabled: boolean }) {
  const update = (key: string, next: MaterialValue[string]) => onChange({ ...value, [key]: next });
  return <fieldset className="review-editor" disabled={disabled}><legend className="sr-only">Предлагаемая редакция</legend>
    {Object.entries(value).map(([key, val]) => {
      if (key === "correct") return <div className="review-answer-keys" key={key}><span>Правильные ответы</span>{(value.options as string[]).map((_, i) =>
        <label key={i}><input type="checkbox" checked={(val as number[]).includes(i)} onChange={e => update(key, e.target.checked ? [...val as number[], i] : (val as number[]).filter(n => n !== i))} />{String.fromCharCode(1040 + i)}</label>)}</div>;
      if (key === "slides") return <div key={key}>{(val as { title: string; bullets: string[]; notes: string }[]).map((slide, i, slides) =>
        <div className="review-slide" key={i}><strong>Слайд {i + 1}</strong>{["title", "bullets", "notes"].map(field => {
          const current = slide[field as keyof typeof slide];
          return <label key={field}>{field === "title" ? "Заголовок слайда" : field === "bullets" ? "Тезисы, по одному на строку" : "Заметки докладчика"}<textarea aria-label={`${field === "title" ? "Заголовок" : field === "bullets" ? "Тезисы" : "Заметки"} слайда ${i + 1}`} rows={field === "bullets" ? 4 : 2} value={Array.isArray(current) ? current.join("\n") : current} onChange={e => update(key, slides.map((s, j) => j === i ? { ...s, [field]: field === "bullets" ? e.target.value.split("\n") : e.target.value } : s))} /></label>;
        })}</div>)}</div>;
      if (key === "options") return <div key={key}>{(val as string[]).map((option, i) => <label key={i}>Вариант {String.fromCharCode(1040 + i)}<textarea aria-label={`Вариант ${String.fromCharCode(1040 + i)}`} rows={2} value={option} onChange={e => update(key, (val as string[]).map((v, j) => j === i ? e.target.value : v))} /></label>)}</div>;
      return <label key={key}>{LABELS[key] ?? key}{Array.isArray(val) && " · по одному на строку"}<textarea aria-label={LABELS[key] ?? key} rows={key === "content" ? 10 : 3} value={Array.isArray(val) ? val.join("\n") : val} onChange={e => update(key, Array.isArray(val) ? e.target.value.split("\n") : e.target.value)} /></label>;
    })}
  </fieldset>;
}

export function ReviewWorkspace({ session, active, mode, onSession, onNavigate, working, onWorkingChange }: {
  onWorkingChange: (working: boolean) => void;
  active: boolean; session: SessionState | null; mode: "expert" | "review" | "approval";
  onSession: (s: SessionState) => void; onNavigate: (s: SectionKey) => void; working: boolean;
}) {
  const [data, commitData] = useState<ReviewData | null>(null);
  const sessionIdentity = useRef(session?.id);
  sessionIdentity.current = session?.id;
  const setData = (next: ReviewData | null) => { if (sessionIdentity.current === session?.id) commitData(next); };
  const [selected, setSelected] = useState("");
  const selectedRef = useRef(selected);
  selectedRef.current = selected;
  const [checked, setChecked] = useState<string[]>([]);
  const [route, setRoute] = useState<"all" | "issues">("all");
  const [busy, setBusy] = useState("");
  useEffect(() => { onWorkingChange(!!busy); return () => onWorkingChange(false); }, [busy, onWorkingChange]);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [author, setAuthor] = useState(() => { try { return localStorage.getItem("acr.reviewer-name") || ""; } catch { return ""; } });
  const [agentAnswer, setAgentAnswer] = useState("");
  const [instruction, setInstruction] = useState("");
  const [edit, setEdit] = useState<{ item: ReviewItem; value: MaterialValue } | null>(null);
  const [approvalComment, setApprovalComment] = useState("");
  const [share, setShare] = useState("");
  const [quote, setQuote] = useState("");
  const reader = useRef<HTMLDivElement>(null);
  const top = useRef<HTMLDivElement>(null);
  const editor = useRef<HTMLElement>(null);
  const agent = useRef<HTMLElement>(null);
  useEffect(() => {
    if (selected) top.current?.scrollIntoView({ block: "start", behavior: "smooth" });
  }, [selected]);
  useEffect(() => {
    if (edit) editor.current?.scrollIntoView({ block: "start", behavior: "smooth" });
  }, [!!edit]);
  useEffect(() => { setShare(""); setNotice(""); setError(""); }, [mode]);
  const id = session?.id;
  const disabled = !!busy || working;

  async function run<T,>(label: string, fn: () => Promise<T>): Promise<T | undefined> {
    setBusy(label); setError(""); setNotice("");
    try { return await fn(); }
    catch (e) { setError(e instanceof ApiError ? e.full : (e as Error).message); }
    finally { setBusy(""); }
  }
  useEffect(() => {
    setData(null); setSelected(""); setChecked([]); setAgentAnswer(""); setEdit(null); setInstruction(""); setError("");
  }, [id]);
  useEffect(() => {
    if (!id || !session?.program || !active || edit) return;
    let alive = true;
    api.review(id).then(d => { if (alive) setData(d); }).catch(e => { if (alive) setError(e.message); });
    return () => { alive = false; };
  }, [id, Boolean(session?.program), session?.stage, active, mode]);
  useEffect(() => {
    try { localStorage.setItem("acr.reviewer-name", author); } catch { /* приватный режим */ }
  }, [author]);
  useEffect(() => {
    if (!edit) return;
    const warn = (e: BeforeUnloadEvent) => { e.preventDefault(); e.returnValue = ""; };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [!!edit]);

  if (!session?.program) return <div className="review-empty"><Icon name="expert" size={32} /><h1>{mode === "approval" ? "Согласование программы" : "Экспертная проверка"}</h1><p>Откройте ссылку на программу или сначала соберите её в мастерской методиста.</p><button className="btn primary" onClick={() => onNavigate("program")}>В мастерскую</button></div>;
  if (!data) return <div className="review-empty" role="status"><p>{error || "Открываю материалы…"}</p>{error && <button className="btn" onClick={() => void run("Загружаю", async () => setData(await api.review(session.id)))}>Повторить</button>}</div>;
  const currentData = data;
  const program = session.program;
  const items = data.items;
  const hasIssue = (i: ReviewItem) => i.findings.some(f => !f.dismissed) || i.status === "needs_changes" || i.status === "stale";
  const routeItems = route === "issues" ? items.filter(hasIssue) : items;
  const item = items.find(i => i.id === selected) ?? routeItems[0] ?? items[0];
  const topicItems = items.filter(i => i.topic_id === item?.topic_id);
  const groups = Array.from(new Set(items.map(i => i.topic_id)));
  const pending = data.proposals.filter(p => p.status === "pending");
  const scan = item ? data.scans[item.topic_id] : null;
  const scanned = groups.filter(g => data.scans[g] && !data.scans[g].stale).length;
  const reviewLabel = data.summary.stale ? "Нужна повторная проверка" : data.summary.reviewed === 0 ? "Без экспертной проверки" : data.summary.reviewed === data.summary.total ? "Все материалы согласованы" : "Согласовано частично";

  function select(next: string) {
    if (edit && !window.confirm("Оставить несохранённую правку и перейти к другому материалу?")) return;
    setSelected(next); setEdit(null); setInstruction(""); setQuote(""); setAgentAnswer("");
    top.current?.scrollIntoView({ block: "start", behavior: "smooth" });
  }
  function next() {
    const index = routeItems.findIndex(i => i.id === item.id);
    const target = routeItems[index + 1];
    if (target) select(target.id); else { setSelected(""); setNotice("Маршрут завершён. Вы вернулись к списку материалов."); }
  }
  async function refresh() {
    if (edit && !window.confirm("Обновить материалы и сбросить несохранённую правку?")) return;
    await run("Обновляю материалы", async () => {
      const [s, d] = await Promise.all([api.state(session!.id), api.review(session!.id)]);
      onSession(s); setData(d); setEdit(null); setChecked([]);
    });
  }
  async function analyzeAll(onlyTopic = false) {
    await run(onlyTopic ? "Агент проверяет тему…" : "Агент проверяет комплект…", async () => {
      const targets = onlyTopic ? [item.topic_id] : groups.filter(g => !currentData.scans[g] || currentData.scans[g].stale);
      for (let index = 0; index < targets.length; index++) {
        setBusy(`Проверяю ${index + 1} из ${targets.length} тем…`);
        setData(await api.scanReview(session!.id, targets[index]));
      }
      setNotice(session!.offline_stub ? "Офлайн-режим: смысловой анализ недоступен. Материалы можно проверять и редактировать вручную." : "Анализ завершён. Замечания — предположения агента; решение остаётся за вами.");
    });
  }
  async function decide(status: string, advance = false) {
    if (!author.trim()) { setError("Укажите ваше имя, чтобы сохранить результат проверки."); return; }
    const d = await run("Сохраняю решение", () => api.reviewDecision(session!.id, { item_id: item.id, revision: item.revision, status, author, comment: instruction }));
    if (d) { setData(d); setNotice(status === "reviewed" ? "Материал согласован." : "Решение сохранено."); if (advance) next(); }
  }
  async function approveBatch(targets: ReviewItem[]) {
    if (!author.trim()) { setError("Укажите ваше имя, чтобы сохранить согласование."); document.querySelector<HTMLInputElement>('[placeholder="Имя эксперта"]')?.focus(); return; }
    if (!targets.length) return;
    const d = await run("Сохраняю согласование", () => api.reviewBatch(session!.id, targets.map(i => ({ item_id: i.id, revision: i.revision })), author));
    if (d) { setData(d); setChecked([]); setNotice(`Согласовано материалов: ${targets.length}. Замечания и предложения правок сохранены.`); }
  }
  async function askAgent() {
    const result = await run("Агент готовит объяснение…", () => api.askReview(session!.id, { item_id: item.id, revision: item.revision, instruction }));
    if (result && sessionIdentity.current === session!.id && selectedRef.current === item.id) setAgentAnswer(result.answer);
  }
  async function suggest(instructions = instruction) {
    const result = await run("Готовлю предложение правки…", () => api.suggestEdit(session!.id, { item_id: item.id, revision: item.revision, instruction: instructions }));
    if (result && sessionIdentity.current === session!.id && selectedRef.current === item.id) {
      setEdit({ item, value: result.replacement });
      setNotice(result.offline ? "Офлайн-режим: агент вернул исходный текст. Внесите правку вручную или подключите модель." : "Предложение готово. Проверьте редакцию перед отправкой методисту.");
    }
  }
  async function submitEdit() {
    if (!edit) return;
    if (!author.trim()) { setError("Укажите ваше имя перед отправкой правки."); return; }
    const d = await run("Отправляю правку", () => api.proposeEdit(session!.id, { item_id: edit.item.id, revision: edit.item.revision, after: edit.value, author, comment: instruction }));
    if (d) { setData(d); setEdit(null); setNotice("Правка отправлена в раздел «Правки эксперта». После принятия методистом она попадёт в комплект и новые выгрузки."); }
  }
  async function shareLink(target: string) {
    const url = `${location.origin}${location.pathname}#/${target}?session=${session!.id}`;
    setShare(url);
    try { await navigator.clipboard.writeText(url); setNotice("Ссылка скопирована. Передайте её участнику работы над программой."); }
    catch { setNotice("Ссылка готова — скопируйте её из поля ниже."); }
  }

  return <div className="review-workspace">
    <header className="review-page-heading">
      <div><span className="review-eyebrow"><Icon name={mode === "approval" ? "approval" : "expert"} size={16} />Совместная работа</span><h1>{mode === "expert" ? "Ваша экспертиза. Помощь агента." : mode === "review" ? "Правки и решения эксперта" : "Программа на согласование"}</h1><p>{mode === "expert" ? "Проверяйте по существу. Агент найдёт спорные места и поможет с редакцией." : mode === "review" ? "Принимайте предложения в общую версию. Экспертиза не блокирует вашу работу." : "Содержание, результаты проверки и решение по текущей версии."}</p></div>
      <a className="review-download" href={api.exportUrl(session.id, "docx")} download><Icon name="exports" size={17} />Документ на подпись</a>
    </header>
    <div className="review-summary-bar"><span><b>{data.summary.reviewed}</b> / {data.summary.total} материалов согласовано</span><span>{pending.length} предложений</span><span className="review-status">{reviewLabel}</span></div>
    <div className="review-toolbar">
      {mode !== "review" && <label>Ваше имя<input value={author} onChange={e => setAuthor(e.target.value)} placeholder={mode === "approval" ? "Имя утверждающего" : "Имя эксперта"} maxLength={120} /></label>}
      <button className="btn" disabled={disabled} onClick={() => void refresh()}>Обновить</button>
      <button className="btn" onClick={() => void shareLink(mode === "approval" ? "approval" : "expert")}>{mode === "approval" ? "Ссылка руководителю" : "Ссылка эксперту"}</button>
      {mode === "expert" && <button className="btn" onClick={() => onNavigate("review")}>Правки для методиста <Icon name="arrow" size={14} /></button>}
    </div>
    {share && <label className="review-share">Ссылка на программу · доступна тому, кому вы её передадите<input aria-label="Ссылка на программу" readOnly value={share} onFocus={e => e.target.select()} /></label>}
    <div aria-live="polite">{busy && <p className="review-feedback"><span className="review-pulse" />{busy}</p>}{error && <p className="review-error" role="alert">{error}</p>}{notice && <p className="review-feedback">{notice}</p>}</div>
    {mode === "expert" && <>
      <div className="review-route-bar"><div className="review-segmented" aria-label="Маршрут проверки"><button aria-pressed={route === "all"} onClick={() => { if (edit && !window.confirm("Сбросить несохранённую правку и вернуться к списку?")) return; setEdit(null); setRoute("all"); setSelected(""); setChecked([]); }}>Все материалы</button><button aria-pressed={route === "issues"} onClick={() => { if (edit && !window.confirm("Сбросить несохранённую правку и вернуться к списку?")) return; setEdit(null); setRoute("issues"); setSelected(""); setChecked([]); }}>Требуют внимания <span>{items.filter(hasIssue).length}</span></button></div><button className="btn primary" disabled={disabled || scanned === groups.length} onClick={() => void analyzeAll()}><Icon name="spark" size={16} />{scanned === groups.length ? "Анализ агента готов" : "Анализировать комплект с ИИ"}</button></div>
      {route === "issues" && !routeItems.length && <p className="review-feedback">{scanned ? "Актуальных замечаний нет. Продолжите последовательную проверку: агент мог не заметить неточности." : "Запустите анализ или проверьте материалы последовательно."}</p>}
      {!selected && data.summary.reviewed === data.summary.total && <div className="review-complete-actions"><a className="review-download" href={api.exportUrl(session.id, "docx")} download>Скачать программу · DOCX</a><button className="btn primary" onClick={() => onNavigate("approval")}>Перейти к руководителю <Icon name="arrow" size={16} /></button><button className="btn" onClick={() => onNavigate("review")}>Посмотреть предложения правок</button></div>}
      {!selected && <ReviewChecklist key={`${id}-${route}`} items={routeItems} allItems={items} checked={checked} onChecked={setChecked} onOpen={select} onApprove={targets => void approveBatch(targets)} disabled={disabled} />}
      {selected && <><button className="btn review-back-list" onClick={() => select("")}>← К списку материалов</button>
      <div className="review-layout">
        <nav className="review-topic-nav" aria-label="Темы для экспертизы">{groups.map(group => {
          const list = items.filter(i => i.topic_id === group), count = list.filter(i => i.status === "reviewed").length;
          return <button key={group} className={item.topic_id === group ? "selected" : ""} disabled={disabled} onClick={() => select(list[0].id)}><span>{list[0].topic_label}</span><small>{count} / {list.length} согласовано {list.some(hasIssue) && "· Есть замечания"}</small><span className="review-topic-progress"><i style={{ width: `${count / list.length * 100}%` }} /></span></button>;
        })}</nav>
        <div className="review-focus" ref={top}>
          <div className="review-material-tabs" aria-label="Материалы темы">{topicItems.map((i, n) => <button key={i.id} aria-pressed={item.id === i.id} disabled={disabled} onClick={() => select(i.id)}><span className={`review-dot ${i.status}`} />{i.label}{i.kind === "test" ? ` ${topicItems.filter((x, j) => j <= n && x.kind === "test").length}` : ""}</button>)}</div>
          <div className="review-focus-tools"><button className="review-text-button" onClick={() => agent.current?.scrollIntoView({ block: "start", behavior: "smooth" })}>К замечаниям и правкам{item.findings.some(f => !f.dismissed) ? ` · ${item.findings.filter(f => !f.dismissed).length}` : ""}</button><div><button className="review-text-button" disabled={routeItems.findIndex(i => i.id === item.id) <= 0} onClick={() => select(routeItems[routeItems.findIndex(i => i.id === item.id) - 1].id)}>Назад</button><button className="review-text-button" onClick={next}>Далее <Icon name="arrow" size={15} /></button></div></div>
          <article className="review-reader"><header><div><span className="review-eyebrow">{item.label}</span><h2>{item.title}</h2></div><span className={`review-badge ${item.status}`}>{STATUS[item.status]}</span></header>
            {item.kind !== "topic" && item.topic_id !== "general" && <button className="review-text-button" onClick={() => select(topicItems.find(i => i.kind === "topic")!.id)}>Открыть материал темы <Icon name="arrow" size={14} /></button>}
            <div ref={reader} onMouseUp={() => { const selection = window.getSelection(); if (selection?.anchorNode && reader.current?.contains(selection.anchorNode)) setQuote(selection.toString().trim().slice(0, 2000)); }}><Material value={item.value} /></div>
            {quote && <button className="btn" onClick={() => setInstruction(`Уточнить фрагмент «${quote}»: `)}>Обсудить выделенный фрагмент</button>}
            {item.decision && <p className="review-signed">{item.decision.author} · {date(item.decision.at)}{item.decision.method === "batch" && " · Пакетное согласование"}{item.decision.comment && ` · ${item.decision.comment}`}</p>}
          </article>
          <section className="review-agent" ref={agent}><div className="review-agent-heading"><h3><Icon name="spark" size={18} />Взгляд агента</h3><button className="review-text-button" disabled={disabled} onClick={() => void analyzeAll(true)}>{scan ? "Повторить ИИ-анализ темы" : "Анализировать тему с ИИ"}</button></div>
            {!scan || scan.stale ? <p className="review-muted">{scan?.stale ? "Материалы изменились. Запустите анализ новой версии." : "Агент сопоставит объяснения, задания и ключи ответов в этой теме."}</p> : <p className="review-muted">{scan.offline ? "Офлайн-режим: смысловой анализ не выполнялся." : (item.findings.some(f => !f.dismissed) ? "Анализ темы выполнен. Ниже — предположения агента по этому материалу." : "В этом материале нет открытых замечаний агента. Это не заменяет решение эксперта.")}</p>}
            {item.findings.filter(f => !f.dismissed).map(f => <div className="review-finding" key={f.id}><strong>{f.title}</strong><blockquote>{f.quote}</blockquote><p>{f.reason}</p>{f.suggestion && <p>{f.suggestion}</p>}<div className="review-actions"><button className="btn" disabled={disabled} onClick={() => { setInstruction(f.suggestion || f.reason); void suggest(f.suggestion || f.reason); }}>Предложить исправление</button><button className="btn ghost" disabled={disabled} onClick={() => void run("Сохраняю", async () => setData(await api.dismissFinding(session.id, f.id, item.revision)))}>Здесь всё корректно</button></div></div>)}
            <p className="review-muted">ИИ-анализ ищет неточности в теме и связанных заданиях. Он не меняет материалы и не согласовывает их за вас.</p>
            <div className="review-quick">{QUICK.filter(q => item.kind === "test" || !q.includes("ключ")).map(q => <button key={q} disabled={disabled} onClick={() => setInstruction(q)}>{q}</button>)}</div>
            <label className="review-comment-label">Ваш вопрос, замечание или поручение агенту<textarea aria-label="Ваше замечание или поручение агенту" value={instruction} onChange={e => setInstruction(e.target.value)} placeholder="Спросите о содержании или опишите, что улучшить. Подсказки выше можно дополнить." rows={3} maxLength={5000} /></label>
            <div className="review-actions"><button className="btn" disabled={disabled || !instruction.trim()} onClick={() => void askAgent()}>Спросить агента</button><button className="btn primary" disabled={disabled || !instruction.trim()} onClick={() => void suggest()}><Icon name="spark" size={15} />Предложить правку</button><button className="btn" disabled={disabled} onClick={() => setEdit({ item, value: structuredClone(item.value) })}>Редактировать самому</button></div>
            {agentAnswer && <div className="review-agent-answer" role="status"><strong>Ответ агента</strong><p>{agentAnswer}</p><small>Материал не изменён. Для изменения используйте «Предложить правку».</small></div>}
          </section>
          {edit && <section className="review-edit-panel" ref={editor}><h3>Предложение правки</h3><p>Правка появится в разделе «Правки эксперта». После принятия методистом изменится уже собранный комплект и последующие выгрузки. Ранее скачанные файлы нужно скачать заново.</p><details><summary>Было</summary><Material value={edit.item.value} /></details><h4>Предлагается</h4><Editor value={edit.value} onChange={value => setEdit({ ...edit, value })} disabled={disabled} /><div className="review-actions"><button className="btn primary" disabled={disabled || JSON.stringify(edit.value) === JSON.stringify(edit.item.value)} onClick={() => void submitEdit()}>Отправить методисту</button><button className="btn" disabled={disabled} onClick={() => setEdit(null)}>Отменить правку</button></div></section>}
          <div className="review-decision-bar review-sticky-actions">
            <div className="review-action-position"><b>{items.findIndex(i => i.id === item.id) + 1} / {items.length}</b><span>{STATUS[item.status]}</span></div>
            <button className="btn" disabled={disabled || !!edit || routeItems.findIndex(i => i.id === item.id) <= 0} onClick={() => select(routeItems[routeItems.findIndex(i => i.id === item.id) - 1].id)}>← Назад</button>
            <button className="btn primary" disabled={disabled || !!edit} onClick={() => void decide("reviewed", true)}><Icon name="check" size={17} />Согласовать и дальше <Icon name="arrow" size={17} /></button>
            <button className="btn" disabled={disabled || !!edit} onClick={() => void decide("deferred", true)}>Пропустить →</button>
            <details className="review-more-actions"><summary>Ещё</summary><button className="review-text-button" disabled={disabled || !!edit} onClick={() => void decide("reviewed")}>Согласовать и остаться</button><button className="review-text-button" disabled={disabled || !!edit || !instruction.trim()} onClick={() => void decide("needs_changes", true)}>Вернуть с замечанием</button></details>
          </div>
        </div>
      </div></>}
    </>}
    {mode === "review" && <div className="review-methodist">
      <div className="review-callout"><div><h2>Вы можете завершить программу самостоятельно</h2><p>Эксперт помогает с содержанием. Его участие и ответы не ограничивают сборку и выгрузку документов.</p></div><button className="btn" onClick={() => onNavigate("program")}>Вернуться в мастерскую</button></div>
      {!pending.length && <p className="review-feedback">Новых предложений правок пока нет. Можно продолжить работу или передать ссылку эксперту.</p>}
      {data.proposals.slice().reverse().map(p => <section className="review-proposal" key={p.id}><header><div><span className="review-eyebrow">{p.author} · {date(p.at)}</span><h2>{p.title}</h2></div><span className="review-badge">{p.status === "accepted" ? "Принята" : p.status === "rejected" ? "Отклонена" : p.stale ? "Материал изменился" : "Ожидает решения"}</span></header>{p.comment && <p>{p.comment}</p>}<details open={p.status === "pending"}><summary>Сравнить редакции</summary><div className="review-diff"><div><h3>Было</h3><Material value={p.before} /></div><div><h3>Предлагается</h3><Material value={p.after} /></div></div></details>{p.status === "pending" && <><p className="review-muted">{p.stale ? "Эта правка относится к предыдущей версии. Эксперт может подготовить новое предложение после обновления материалов." : "После принятия изменённый материал и связанные проверки потребуют повторного просмотра."}</p><div className="review-actions"><button className="btn primary" disabled={disabled || p.stale} onClick={() => void run("Применяю правку", async () => { const r = await api.resolveEdit(session.id, p.id, "accept"); setData(r.review); onSession(r.session); })}>Принять в программу</button><button className="btn" disabled={disabled} onClick={() => void run("Сохраняю решение", async () => { const r = await api.resolveEdit(session.id, p.id, "reject"); setData(r.review); onSession(r.session); })}>Отклонить</button></div></>}</section>)}
      <h2>Замечания по материалам</h2>{items.filter(i => i.decision?.comment).map(i => <div className="review-note" key={i.id}><strong>{i.title}</strong><p>{i.decision!.comment}</p><small>{i.decision!.author} · {STATUS[i.status]}</small><button className="review-text-button" onClick={() => { select(i.id); onNavigate("expert"); }}>Открыть материал <Icon name="arrow" size={14} /></button></div>)}
      {!!data.decision_history?.length && <details className="review-decision-history"><summary>Предыдущие решения и замечания эксперта</summary>{data.decision_history.slice().reverse().map((d, n) => <div className="review-note" key={n}><strong>{items.find(i => i.id === d.item_id)?.title ?? "Материал предыдущей версии"}</strong><p>{d.comment || STATUS[d.status as keyof typeof STATUS]}</p><small>{d.author} · {date(d.at)}</small></div>)}</details>}
      {data.approval && <div className="review-note"><strong>Решение руководителя: {data.approval.stale ? "относится к прошлой версии" : data.approval.decision === "approved" ? "согласовано" : "возвращено на доработку"}</strong><p>{data.approval.comment}</p><small>{data.approval.author} · {date(data.approval.at)}</small></div>}
    </div>}
    {mode === "approval" && <div className="review-approval">
      <section className="review-reader"><span className="review-eyebrow">Текущая версия программы</span><h2>{program.meta.title}</h2><div className="review-facts"><span><b>{program.meta.total_hours}</b> академических часов</span><span>{program.meta.audience}</span><span>{program.modules.length} модулей</span></div><p>{program.goal}</p><h3>Что получит слушатель</h3><ul>{program.outcomes.map(o => <li key={o.id}>{o.text}</li>)}</ul><h3>Учебный план</h3>{program.modules.map(m => <details key={m.id}><summary>{m.index}. {m.title} · {m.hours.total} ч</summary><ul>{m.topics.map(t => <li key={t.id}>{t.code} {t.title}</li>)}</ul></details>)}<button className="review-text-button" onClick={() => onNavigate("expert")}>Открыть все материалы <Icon name="arrow" size={15} /></button></section>
      <section className="review-reader"><h2>Готовность к согласованию</h2><p>{reviewLabel}. {data.summary.reviewed} из {data.summary.total} материалов согласовано.</p><p>Участники проверки: {Array.from(new Set(items.filter(i => i.decision).map(i => i.decision!.author))).join(", ") || "эксперт не подключался"}.</p><p>Предложений к рассмотрению: {pending.length}. Материалов с замечаниями: {data.summary.needs_changes}.</p><p>Автопроверка: ошибок — {session.report_summary?.errors ?? 0}, предупреждений — {session.report_summary?.warnings ?? 0}.</p><div className="review-actions"><button className="btn" onClick={() => onNavigate("review")}>Правки и замечания</button><button className="btn" onClick={() => onNavigate("report")}>Отчёт автопроверки</button></div>
        <div className="approval-record-card"><h3>Результат согласования</h3><p>Сохраняются имя участника, дата, решение и конкретная версия программы. После изменений требуется новое согласование.</p>{data.approval ? <a className="review-download" href={api.approvalRecordUrl(session.id)} download><Icon name="exports" size={17} />Скачать лист согласования</a> : <p className="review-muted">После решения здесь появится лист согласования для скачивания.</p>}<small>Электронная подпись не подключена. Подпись руководителя оформляется отдельно.</small></div>
        <h3>История решений</h3>{!data.approvals.length && <p className="review-muted">Решение ещё не принято. Документ можно передать на подпись вне сайта.</p>}{data.approvals.slice().reverse().map((a, n) => <div className="review-note" key={n}><strong>{a.decision === "approved" ? "Согласовано" : "Возвращено на доработку"}</strong><p>{a.comment}</p><small>{a.author} · {date(a.at)}{n === 0 && data.approval?.stale && " · Программа с тех пор изменилась"}</small></div>)}
        <label className="review-comment-label">Комментарий к решению<textarea aria-label="Комментарий к решению" rows={3} value={approvalComment} onChange={e => setApprovalComment(e.target.value)} placeholder="Что важно учесть или доработать" maxLength={5000} /></label><div className="review-actions">{(["approved", "returned"] as const).map(decision => <button key={decision} className={`btn ${decision === "approved" ? "primary" : ""}`} disabled={disabled || !author.trim() || (decision === "returned" ? !approvalComment.trim() : session.stage !== "ready" || !!session.report_summary?.errors)} onClick={() => void run("Сохраняю решение", async () => { setData(await api.approval(session.id, { revision: currentData.revision, decision, author, comment: approvalComment })); setNotice("Решение по текущей версии сохранено."); })}>{decision === "approved" ? "Согласовать версию" : "Вернуть с замечаниями"}</button>)}</div><p className="review-muted">Согласование на сайте фиксирует решение по версии. Подпись руководителя оформляется в документе.</p>
      </section>
    </div>}
  </div>;
}
