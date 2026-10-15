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

/**
 * Строка статуса генерации: подпись «переливается» слева направо,
 * рядом тикает секундомер — методист видит, что агент не завис.
 */
export function StatusLine({ label, since }: { label: string; since: number }) {
  const reduced = useReducedMotion();
  return (
    <div className="statusline">
      <motion.span
        className="sl-mark"
        animate={reduced ? {} : { rotate: 360 }}
        transition={{ duration: 2.4, repeat: Infinity, ease: "linear" }}
      />
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
