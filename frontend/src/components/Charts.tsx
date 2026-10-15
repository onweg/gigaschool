import { motion } from "framer-motion";
import { useState } from "react";

/**
 * Графики для панели статистики. Рисуем инлайновым SVG: зависимостей нет,
 * палитра проверена валидатором под тёмную подложку #141414 (контраст и
 * различимость при дальтонизме), цвета заданы ролями в styles.css.
 */

export const SERIES = ["var(--s1)", "var(--s2)", "var(--s3)", "var(--s4)"];

const fmt = new Intl.NumberFormat("ru");

/** x — доля ширины графика (0…1): SVG тянется по контейнеру, поэтому
 *  координаты viewBox не равны пикселям и подсказку ставим в процентах. */
export function Tip({ x, y, lines }: { x: number; y: number; lines: string[] }) {
  return (
    <div className="viz-tip" style={{ left: `${(x * 100).toFixed(2)}%`, top: y }}>
      {lines.map((line, i) => (
        <div key={i} className={i === 0 ? "t-head" : ""}>
          {line}
        </div>
      ))}
    </div>
  );
}

/** Воронка: один ряд, величина — длина полосы, значение подписано прямо на ней. */
export function FunnelBars({
  items,
  labels,
}: {
  items: { stage: string; sessions: number; share: number }[];
  labels: Record<string, string>;
}) {
  const max = Math.max(1, ...items.map((i) => i.sessions));
  return (
    <div className="bars">
      {items.map((item, i) => (
        <div className="bar-row" key={item.stage}>
          <span className="bar-label">{labels[item.stage] ?? item.stage}</span>
          <div className="bar-track">
            <motion.div
              className={`bar-fill ${item.stage === "failed" ? "bad" : ""}`}
              initial={{ width: 0 }}
              animate={{ width: `${(item.sessions / max) * 100}%` }}
              transition={{ delay: i * 0.05, duration: 0.6, ease: [0.16, 1, 0.3, 1] }}
            />
          </div>
          <span className="bar-value">
            {fmt.format(item.sessions)}
            <span className="bar-share">{item.share}%</span>
          </span>
        </div>
      ))}
    </div>
  );
}

/** Две величины одной природы (штуки в день) — одна шкала, две линии. */
export function DaysChart({
  days,
}: {
  days: { day: string; sessions: number; ready: number }[];
}) {
  const [hover, setHover] = useState<number | null>(null);
  if (days.length === 0) return <div className="viz-empty">Пока нет данных за период</div>;

  const W = 720;
  const H = 190;
  const PAD = { l: 34, r: 12, t: 14, b: 26 };
  const max = Math.max(2, ...days.map((d) => Math.max(d.sessions, d.ready)));
  const stepX =
    days.length > 1 ? (W - PAD.l - PAD.r) / (days.length - 1) : 0;
  const px = (i: number) => PAD.l + i * stepX;
  const py = (v: number) => PAD.t + (1 - v / max) * (H - PAD.t - PAD.b);

  const line = (key: "sessions" | "ready") =>
    days.map((d, i) => `${i ? "L" : "M"}${px(i).toFixed(1)} ${py(d[key]).toFixed(1)}`).join(" ");

  const ticks = [0, Math.round(max / 2), max];
  const active = hover !== null ? days[hover] : null;

  return (
    <div className="viz-wrap">
      <div className="viz-legend">
        <span>
          <i style={{ background: SERIES[0] }} /> сессии
        </span>
        <span>
          <i style={{ background: SERIES[1] }} /> готовые комплекты
        </span>
      </div>

      <div className="viz-plot">
        <svg viewBox={`0 0 ${W} ${H}`} className="viz-svg" role="img">
          {ticks.map((t) => (
            <g key={t}>
              <line
                x1={PAD.l}
                x2={W - PAD.r}
                y1={py(t)}
                y2={py(t)}
                className="viz-grid"
              />
              <text x={PAD.l - 8} y={py(t) + 3.5} className="viz-axis" textAnchor="end">
                {t}
              </text>
            </g>
          ))}

          {active && hover !== null && (
            <line
              x1={px(hover)}
              x2={px(hover)}
              y1={PAD.t}
              y2={H - PAD.b}
              className="viz-cross"
            />
          )}

          <motion.path
            d={line("sessions")}
            className="viz-line"
            style={{ stroke: SERIES[0] }}
            initial={{ pathLength: 0 }}
            animate={{ pathLength: 1 }}
            transition={{ duration: 0.8, ease: "easeOut" }}
          />
          <motion.path
            d={line("ready")}
            className="viz-line"
            style={{ stroke: SERIES[1] }}
            initial={{ pathLength: 0 }}
            animate={{ pathLength: 1 }}
            transition={{ duration: 0.8, delay: 0.1, ease: "easeOut" }}
          />

          {hover !== null && (
            <>
              <circle cx={px(hover)} cy={py(days[hover].sessions)} r="4.5" fill={SERIES[0]} className="viz-dot" />
              <circle cx={px(hover)} cy={py(days[hover].ready)} r="4.5" fill={SERIES[1]} className="viz-dot" />
            </>
          )}

          {days.map((d, i) => (
            <rect
              key={d.day}
              x={px(i) - stepX / 2}
              y={0}
              width={Math.max(stepX, 10)}
              height={H}
              fill="transparent"
              onMouseEnter={() => setHover(i)}
              onMouseLeave={() => setHover(null)}
            />
          ))}

          {days.map((d, i) =>
            i === 0 || i === days.length - 1 ? (
              <text key={`x${d.day}`} x={px(i)} y={H - 8} className="viz-axis" textAnchor={i ? "end" : "start"}>
                {d.day.slice(5)}
              </text>
            ) : null
          )}
        </svg>

        {active && hover !== null && (
          <Tip
            x={Math.min(0.88, Math.max(0.12, px(hover) / W))}
            y={8}
            lines={[
              active.day,
              `сессии: ${active.sessions}`,
              `готовые: ${active.ready}`,
            ]}
          />
        )}
      </div>
    </div>
  );
}

/** Небольшие полосы для распределений: форматы выгрузок, типы программ. */
export function MiniBars({
  items,
  color = SERIES[0],
}: {
  items: { label: string; value: number; hint?: string }[];
  color?: string;
}) {
  const max = Math.max(1, ...items.map((i) => i.value));
  if (!items.length) return <div className="viz-empty">Нет данных</div>;
  return (
    <div className="bars tight">
      {items.map((item, i) => (
        <div className="bar-row" key={item.label}>
          <span className="bar-label">{item.label}</span>
          <div className="bar-track">
            <motion.div
              className="bar-fill"
              style={{ background: color }}
              initial={{ width: 0 }}
              animate={{ width: `${(item.value / max) * 100}%` }}
              transition={{ delay: i * 0.04, duration: 0.55, ease: [0.16, 1, 0.3, 1] }}
            />
          </div>
          <span className="bar-value">
            {fmt.format(item.value)}
            {item.hint && <span className="bar-share">{item.hint}</span>}
          </span>
        </div>
      ))}
    </div>
  );
}
