import { AnimatePresence, motion } from "framer-motion";
import type { Finding } from "../lib/types";

export function Report({ findings }: { findings: Finding[] }) {
  if (!findings.length) {
    return (
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        style={{ color: "var(--green)", fontSize: 13, display: "flex", gap: 9, alignItems: "center" }}
      >
        <span className="dot" /> Замечаний нет — часы, покрытие и разделы сходятся.
      </motion.div>
    );
  }
  return (
    <AnimatePresence initial={false}>
      {findings.map((finding, i) => (
        <motion.div
          className={`finding ${finding.severity}`}
          key={`${finding.code}-${finding.path}-${i}`}
          initial={{ opacity: 0, x: -8 }}
          animate={{ opacity: 1, x: 0 }}
          exit={{ opacity: 0, height: 0 }}
          transition={{ delay: i * 0.05, duration: 0.45, ease: [0.16, 1, 0.3, 1] }}
        >
          <motion.div
            className="mark"
            initial={{ scaleY: 0 }}
            animate={{ scaleY: 1 }}
            transition={{ delay: i * 0.05 + 0.1, duration: 0.4 }}
          />
          <div>
            <div>{finding.message}</div>
            <code>
              {finding.code}
              {finding.path ? ` · ${finding.path}` : ""}
              {finding.hint ? ` · ${finding.hint}` : ""}
            </code>
          </div>
        </motion.div>
      ))}
    </AnimatePresence>
  );
}
