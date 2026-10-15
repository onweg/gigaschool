import {
  AnimatePresence,
  animate,
  motion,
  useInView,
  useMotionValue,
  useReducedMotion,
  useTransform,
} from "framer-motion";
import { useEffect, useRef, useState, type ReactNode } from "react";

/** Мягкая пружина — одна на весь интерфейс, чтобы движение было узнаваемым. */
export const SPRING = { type: "spring", stiffness: 260, damping: 30, mass: 0.9 } as const;

/** Появление блока при попадании в область просмотра. */
export function Reveal({
  children,
  delay = 0,
  y = 14,
  className,
}: {
  children: ReactNode;
  delay?: number;
  y?: number;
  className?: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const inView = useInView(ref, { once: true, margin: "-40px" });
  const reduced = useReducedMotion();
  return (
    <motion.div
      ref={ref}
      className={className}
      initial={reduced ? false : { opacity: 0, y, filter: "blur(6px)" }}
      animate={
        inView || reduced
          ? { opacity: 1, y: 0, filter: "blur(0px)" }
          : { opacity: 0, y, filter: "blur(6px)" }
      }
      transition={{ duration: 0.65, delay, ease: [0.16, 1, 0.3, 1] }}
    >
      {children}
    </motion.div>
  );
}

/** Число, которое «доезжает» до значения: часы и токены меняются на глазах. */
export function AnimatedNumber({
  value,
  decimals = 0,
  format,
}: {
  value: number;
  decimals?: number;
  format?: (value: number) => string;
}) {
  const reduced = useReducedMotion();
  const motionValue = useMotionValue(reduced ? value : 0);
  const rounded = useTransform(motionValue, (latest) =>
    format ? format(latest) : latest.toFixed(decimals)
  );
  useEffect(() => {
    if (reduced) {
      motionValue.set(value);
      return;
    }
    const controls = animate(motionValue, value, {
      duration: 0.85,
      ease: [0.16, 1, 0.3, 1],
    });
    return () => controls.stop();
  }, [value, reduced, motionValue]);
  return <motion.span>{rounded}</motion.span>;
}

/** Три точки: агент думает. */
export function Typing() {
  return (
    <div className="typing">
      {[0, 1, 2].map((i) => (
        <motion.span
          key={i}
          animate={{ opacity: [0.25, 1, 0.25], y: [0, -3, 0] }}
          transition={{ duration: 1.1, repeat: Infinity, delay: i * 0.16, ease: "easeInOut" }}
        />
      ))}
    </div>
  );
}

/** Человекочитаемая длительность: «4,2 с», «1 мин 12 с». */
export function formatDuration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return "—";
  if (seconds < 60) {
    const value = seconds < 10 ? seconds.toFixed(1) : String(Math.round(seconds));
    return `${value.replace(".", ",")} с`;
  }
  const minutes = Math.floor(seconds / 60);
  const rest = Math.round(seconds % 60);
  return rest ? `${minutes} мин ${rest} с` : `${minutes} мин`;
}

/** Секундомер, который тикает, пока агент работает. */
export function Elapsed({ since }: { since: number }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 100);
    return () => clearInterval(timer);
  }, [since]);
  return <>{formatDuration(Math.max(0, (now - since) / 1000))}</>;
}

/* ------------------------------------------------------------------ Значки */
/**
 * Набор значков под действия агента. Рисуем инлайновым SVG: это один файл без
 * внешних зависимостей (демо должно работать офлайн) и цвет наследуется от
 * строки статуса. Каждый значок анимируется по-своему, чтобы шаг читался
 * боковым зрением.
 */
export type MarkKind =
  | "parse"
  | "legal"
  | "map"
  | "plan"
  | "hours"
  | "tests"
  | "seminars"
  | "slides"
  | "check";

const STROKE = {
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.6,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
};

const PULSE = { duration: 1.6, repeat: Infinity, ease: "easeInOut" as const };

const MARKS: Record<MarkKind, (reduced: boolean) => ReactNode> = {
  // Разбор запроса: строки текста пробегают сверху вниз
  parse: (reduced) => (
    <>
      <path d="M4 2.6h8.4a1 1 0 0 1 1 1v10.8a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V3.6a1 1 0 0 1 1-1Z" {...STROKE} />
      {[6.2, 9, 11.8].map((y, i) => (
        <motion.line
          key={y}
          x1="5.4" x2={i === 2 ? "9.6" : "11"} y1={y} y2={y}
          {...STROKE}
          animate={reduced ? {} : { opacity: [0.25, 1, 0.25] }}
          transition={{ ...PULSE, delay: i * 0.22 }}
        />
      ))}
    </>
  ),
  // Нормативка: книга, страница перелистывается
  legal: (reduced) => (
    <>
      <path d="M2.6 3.4h4.3a1.7 1.7 0 0 1 1.7 1.7v8.6a1.3 1.3 0 0 0-1.3-1.3H2.6V3.4Z" {...STROKE} />
      <motion.path
        d="M13.4 3.4H9.1a1.7 1.7 0 0 0-1.7 1.7v8.6a1.3 1.3 0 0 1 1.3-1.3h4.7V3.4Z"
        {...STROKE}
        style={{ originX: "8px", originY: "8px" }}
        animate={reduced ? {} : { rotateY: [0, -55, 0] }}
        transition={{ duration: 2.1, repeat: Infinity, ease: "easeInOut" }}
      />
    </>
  ),
  // Карта компетенций: узлы графа загораются по очереди
  map: (reduced) => (
    <>
      <path d="M8 5.4v2.1M8 7.5 4.9 10.3M8 7.5l3.1 2.8" {...STROKE} />
      {[
        { cx: 8, cy: 3.9, d: 0 },
        { cx: 4.2, cy: 11.6, d: 0.3 },
        { cx: 11.8, cy: 11.6, d: 0.6 },
      ].map((n) => (
        <motion.circle
          key={`${n.cx}-${n.cy}`}
          cx={n.cx} cy={n.cy} r="1.7"
          {...STROKE}
          animate={reduced ? {} : { opacity: [0.3, 1, 0.3], scale: [0.86, 1, 0.86] }}
          style={{ originX: `${n.cx}px`, originY: `${n.cy}px` }}
          transition={{ ...PULSE, delay: n.d }}
        />
      ))}
    </>
  ),
  // Учебный план: строки таблицы прибавляются
  plan: (reduced) => (
    <>
      <rect x="2.6" y="3" width="10.8" height="10" rx="1.4" {...STROKE} />
      <line x1="2.6" x2="13.4" y1="6.3" y2="6.3" {...STROKE} />
      {[8.4, 10.6].map((y, i) => (
        <motion.line
          key={y}
          x1="4.6" x2="11.4" y1={y} y2={y}
          {...STROKE}
          animate={reduced ? {} : { pathLength: [0, 1], opacity: [0.35, 1] }}
          transition={{ duration: 1.3, repeat: Infinity, repeatType: "reverse", delay: i * 0.3 }}
        />
      ))}
    </>
  ),
  // Часы: стрелка идёт по кругу
  hours: (reduced) => (
    <>
      <circle cx="8" cy="8" r="5.5" {...STROKE} />
      <motion.line
        x1="8" y1="8" x2="8" y2="4.8"
        {...STROKE}
        style={{ originX: "8px", originY: "8px" }}
        animate={reduced ? {} : { rotate: 360 }}
        transition={{ duration: 2, repeat: Infinity, ease: "linear" }}
      />
      <line x1="8" y1="8" x2="10.6" y2="8" {...STROKE} opacity={0.55} />
    </>
  ),
  // Тесты: галочка прорисовывается в списке
  tests: (reduced) => (
    <>
      <rect x="2.6" y="2.8" width="10.8" height="10.4" rx="1.4" {...STROKE} />
      <line x1="5.2" x2="10.8" y1="6" y2="6" {...STROKE} opacity={0.5} />
      <motion.path
        d="m5.2 10 1.9 1.9 3.8-4"
        {...STROKE}
        animate={reduced ? {} : { pathLength: [0, 1, 1, 0], opacity: [0.4, 1, 1, 0.4] }}
        transition={{ duration: 2, repeat: Infinity, times: [0, 0.4, 0.75, 1] }}
      />
    </>
  ),
  // Семинары: карандаш пишет
  seminars: (reduced) => (
    <>
      <motion.g
        animate={reduced ? {} : { x: [-0.8, 0.8, -0.8], y: [0.8, -0.8, 0.8] }}
        transition={{ duration: 1.5, repeat: Infinity, ease: "easeInOut" }}
      >
        <path d="M10.4 2.9 13.1 5.6 6.3 12.4l-3.4.7.7-3.4 6.8-6.8Z" {...STROKE} />
        <line x1="9.2" x2="11.9" y1="4.1" y2="6.8" {...STROKE} />
      </motion.g>
      <line x1="3" x2="13" y1="14.6" y2="14.6" {...STROKE} opacity={0.4} />
    </>
  ),
  // Слайды: кадры сменяются на экране
  slides: (reduced) => (
    <>
      <rect x="2.4" y="3.2" width="11.2" height="8" rx="1.3" {...STROKE} />
      <line x1="8" x2="8" y1="11.2" y2="13.4" {...STROKE} />
      <line x1="5.6" x2="10.4" y1="13.4" y2="13.4" {...STROKE} />
      <motion.rect
        x="4.4" y="5.2" width="7.2" height="4" rx="0.7"
        fill="currentColor"
        animate={reduced ? {} : { opacity: [0.15, 0.6, 0.15] }}
        transition={PULSE}
      />
    </>
  ),
  // Автопроверка: щит с галочкой
  check: (reduced) => (
    <>
      <motion.path
        d="M8 2.3l4.8 1.9v4.1c0 3-2 5.1-4.8 5.9-2.8-.8-4.8-2.9-4.8-5.9V4.2L8 2.3Z"
        {...STROKE}
        animate={reduced ? {} : { opacity: [0.5, 1, 0.5] }}
        transition={PULSE}
      />
      <motion.path
        d="m5.9 8.1 1.6 1.6 2.9-3"
        {...STROKE}
        animate={reduced ? {} : { pathLength: [0, 1] }}
        transition={{ duration: 1.1, repeat: Infinity, repeatType: "reverse" }}
      />
    </>
  ),
};

export function Mark({ kind }: { kind: MarkKind }) {
  const reduced = useReducedMotion() ?? false;
  return (
    <AnimatePresence mode="wait">
      <motion.svg
        key={kind}
        className="sl-mark"
        viewBox="0 0 16 16"
        width="15"
        height="15"
        initial={{ opacity: 0, scale: 0.7, rotate: -25 }}
        animate={{ opacity: 1, scale: 1, rotate: 0 }}
        exit={{ opacity: 0, scale: 0.7, rotate: 25 }}
        transition={{ duration: 0.26, ease: [0.16, 1, 0.3, 1] }}
      >
        {MARKS[kind](reduced)}
      </motion.svg>
    </AnimatePresence>
  );
}

/**
 * Строка статуса генерации: значок под текущее действие, подпись с переливом
 * и секундомер — методист видит, чем агент занят и что он не завис.
 */
export function StatusLine({
  label,
  mark,
  since,
}: {
  label: string;
  mark: MarkKind;
  since: number;
}) {
  const reduced = useReducedMotion();
  return (
    <div className="statusline">
      <Mark kind={mark} />
      <AnimatePresence mode="wait">
        <motion.span
          key={label}
          className={`sl-label ${reduced ? "" : "shimmer"}`}
          initial={{ opacity: 0, y: 4 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -4 }}
          transition={{ duration: 0.28 }}
        >
          {label}
        </motion.span>
      </AnimatePresence>
      <span className="sl-time">
        <Elapsed since={since} />
      </span>
    </div>
  );
}
