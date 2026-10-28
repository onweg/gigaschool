import { useEffect, useState } from "react";
import { TowerSilhouette } from "./TowerSilhouette";

/** A brief brand entrance; App mounts and loads independently underneath. */
export function OpeningScene() {
  const [visible, setVisible] = useState(() => !window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  useEffect(() => {
    if (!visible) return;
    const dismiss = () => setVisible(false);
    const motion = window.matchMedia("(prefers-reduced-motion: reduce)");
    const motionChanged = () => { if (motion.matches) dismiss(); };
    const timer = window.setTimeout(dismiss, 2400);
    window.addEventListener("pointerdown", dismiss, { once: true });
    window.addEventListener("keydown", dismiss, { once: true });
    motion.addEventListener("change", motionChanged);
    return () => {
      window.clearTimeout(timer);
      window.removeEventListener("pointerdown", dismiss);
      window.removeEventListener("keydown", dismiss);
      motion.removeEventListener("change", motionChanged);
    };
  }, [visible]);
  if (!visible) return null;
  return <div className="opening-scene" aria-hidden="true">
    <div className="opening-halo" />
    <div className="opening-art"><TowerSilhouette /><div className="opening-brand">АЦР<span>Академия цифрового развития</span></div></div>
  </div>;
}
