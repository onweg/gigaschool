import { AnimatePresence, motion } from "framer-motion";
import { useEffect, useState } from "react";
import { ApiError, api, type PendingSource } from "../lib/api";
import type { SessionState } from "../lib/types";

function warnings(n: number): string {
  const tail = n % 10 === 1 && n % 100 !== 11 ? "предупреждение"
    : [2, 3, 4].includes(n % 10) && ![12, 13, 14].includes(n % 100) ? "предупреждения"
    : "предупреждений";
  return `${n} ${tail}`;
}

const GROUPS: {
  kind: PendingSource["kind"];
  title: string;
  note: (n: number) => string;
}[] = [
  {
    kind: "prof_standard",
    title: "Профстандарты",
    note: warnings,
  },
  {
    kind: "legal_act",
    title: "Нормативные акты",
    note: warnings,
  },
  {
    kind: "source",
    title: "Список литературы",
    note: warnings,
  },
];

/**
 * Неподтверждённые источники — маршрут для методиста.
 *
 * Агент помечает то, что не нашёл в реестре, но пометка сама ничего не
 * решает. Здесь у каждого источника: что именно сверить, ссылка на реестр
 * и решение — подтвердить после проверки или убрать из программы.
 */
export function SourceCheck({
  session,
  onChange,
}: {
  session: SessionState;
  onChange: (next: SessionState) => void;
}) {
  const [items, setItems] = useState<PendingSource[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api
      .pendingSources(session.id)
      .then((r) => setItems(r.items))
      .catch((e) => setError(e instanceof ApiError ? e.full : String(e)));
  }, [session.id]);

  async function decide(item: PendingSource, action: "confirm" | "remove") {
    setBusy(item.key);
    try {
      const next = await api.reviewSource(session.id, item.kind, item.key, action);
      setItems(next.pending_sources);
      onChange(next);
    } catch (e) {
      setError(e instanceof ApiError ? e.full : String(e));
    } finally {
      setBusy(null);
    }
  }

  if (error) return <div className="ref-error">{error}</div>;
  if (items === null) return null;
  if (!items.length) {
    return (
      <div className="source-check done">
        <span className="dot" /> Все источники и профстандарты подтверждены.
      </div>
    );
  }

  // Каждая строка здесь — одно предупреждение автопроверки: счёт совпадает.
  const groups = GROUPS.map((g) => ({ ...g, items: items.filter((i) => i.kind === g.kind) })).filter(
    (g) => g.items.length
  );

  return (
    <div className="source-check">
      <div className="source-check-head">Требует проверки методистом</div>
      {groups.map((group) => (
        <div className="source-group" key={group.kind}>
          <div className="source-group-head">
            {group.title} · {group.items.length}
            <span className="source-group-note">{group.note(group.items.length)}</span>
          </div>
          <AnimatePresence initial={false}>
            {group.items.map((item) => (
              <motion.div
                className="source-item"
                key={`${item.kind}-${item.key}`}
                initial={{ opacity: 0, y: 6 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, height: 0, marginTop: 0 }}
                transition={{ duration: 0.25 }}
              >
                <div className="source-body">
                  <div className="source-title">{item.title}</div>
                  <div className="source-what">
                    Сверить {item.what_to_check}:{" "}
                    <a href={item.check_url} target="_blank" rel="noreferrer">
                      открыть реестр ↗
                    </a>
                  </div>
                </div>
                <div className="source-actions">
                  <button
                    className="btn ghost"
                    disabled={busy !== null}
                    onClick={() => void decide(item, "remove")}
                    title="Убрать из программы и из документа"
                  >
                    Убрать
                  </button>
                  <button
                    className="btn primary"
                    disabled={busy !== null}
                    onClick={() => void decide(item, "confirm")}
                    title="Я сверил источник — снять пометку"
                  >
                    {busy === item.key ? "…" : "Подтверждаю"}
                  </button>
                </div>
              </motion.div>
            ))}
          </AnimatePresence>
        </div>
      ))}
    </div>
  );
}
