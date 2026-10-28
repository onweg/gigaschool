import type { CSSProperties } from "react";

const paths = {
  menu: "M4 7h16M4 12h16M4 17h16",
  expert: "M10 17a7 7 0 1 0 0-14 7 7 0 0 0 0 14Zm5-2 6 6M7 10l2 2 4-4",
  review: "M4 3h12v18H4V3Zm4 5h4m-4 4h4m-4 4h3m7-9 3 3-6 6h-3v-3l6-6Z",
  approval: "M5 3h14v18H5V3Zm3 5h8m-8 4h5m-5 4 2 2 5-5",
  spark: "m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5L12 3Z",
  program: "M4 5h6a3 3 0 0 1 3 3v13a4 4 0 0 0-4-2H4V5Zm16 0h-4a3 3 0 0 0-3 3m7-3v14h-3a4 4 0 0 0-4 2",
  competencies: "M12 7v4m0 0H5v5m7-5h7v5M9 3h6v4H9V3ZM2 16h6v5H2v-5Zm14 0h6v5h-6v-5Z",
  plan: "M4 4h16v16H4V4Zm0 5h16M9 9v11",
  kit: "M3 7h7l2 2h9v11H3V7Zm0 0V4h7l2 3h7v2",
  report: "m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6l8-3Zm-4 9 3 3 5-6",
  exports: "M12 3v12m-5-5 5 5 5-5M4 15v5h16v-5",
  stats: "M5 20v-6m7 6V9m7 11V4",
  plus: "M12 5v14M5 12h14",
  arrow: "M5 12h14m-5-5 5 5-5 5",
  send: "M12 19V5m-6 6 6-6 6 6",
  clip: "m8 13 7-7a3 3 0 0 1 4 4l-9 9a5 5 0 0 1-7-7l9-9m-5 11 7-7",
  close: "m6 6 12 12M6 18 18 6",
  chevron: "m7 10 5 5 5-5",
  check: "m5 12 4 4L19 6",
  clock: "M12 8v5l3 2M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z",
} as const;

export type IconName = keyof typeof paths;

export function Icon({ name, size = 20, style }: { name: IconName; size?: number; style?: CSSProperties }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" style={style}>
      <path d={paths[name]} />
    </svg>
  );
}
