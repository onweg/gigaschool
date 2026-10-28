import { useId } from "react";

/** Original vector interpretation of the three stone towers in the reference. */
export function TowerSilhouette() {
  const id = useId().replace(/:/g, "");
  const towers = [
    { x: 188, y: 53, scale: .85 },
    { x: 292, y: 86, scale: .78 },
    { x: 78, y: 18, scale: 1 },
  ];
  return <svg className="tower-silhouette" viewBox="0 0 460 390" fill="none" aria-hidden="true">
    <defs>
      <linearGradient id={`${id}-stone`} x1="0" y1="0" x2="110" y2="320" gradientUnits="userSpaceOnUse"><stop stopColor="#a6cfff" stopOpacity=".3" /><stop offset=".5" stopColor="#416aa8" stopOpacity=".12" /><stop offset="1" stopColor="#254674" stopOpacity=".03" /></linearGradient>
      <linearGradient id={`${id}-edge`} x1="0" y1="0" x2="90" y2="340" gradientUnits="userSpaceOnUse"><stop stopColor="#e0eeff" /><stop offset=".55" stopColor="#8eb9f5" /><stop offset="1" stopColor="#517ba8" stopOpacity=".2" /></linearGradient>
    </defs>
    <path className="tower-land" d="M20 354 67 340 105 347 151 324 194 334 241 319 282 338 326 333 372 353 440 362M44 368l84-13 66 8 69-16 64 13 78 10" stroke="#7397cc" strokeOpacity=".25" />
    {towers.map((tower, i) => <g key={i} transform={`translate(${tower.x} ${tower.y}) scale(${tower.scale})`}>
      <g className={`tower-building tower-building-${i}`}>
        <path d="M12 326 26 60 65 60 81 326 42 340Z" fill={`url(#${id}-stone)`} />
        <path d="m65 60 19 10 13 253-16 3Z" fill="#4f77ac" fillOpacity=".08" />
        <path className="tower-outline" pathLength="1" d="M12 326 26 60 31 60 31 51 36 51 36 41 41 41 41 30 46 30 46 19 50 19 50 30 55 30 55 41 60 41 60 51 65 51 65 60 81 326 42 340 12 326M65 60l19 10 13 253-16 3M26 60h39M31 51h29M36 41h19M41 30h9M42 340 46 60" stroke={`url(#${id}-edge)`} strokeWidth="1.6" strokeLinejoin="round" />
        <path className="tower-windows" d="M37 108V88q5-11 10 0v20Zm1 81v-15q4-8 8 0v15Zm-1 90v-18q5-9 10 0v18Z" fill="#aed4ff" fillOpacity=".5" />
        <path d="m70 89 6 3v24l-5-1M72 172l6 2v16l-5-1" stroke="#90b4e6" strokeOpacity=".35" />
      </g>
    </g>)}
    <ellipse cx="230" cy="366" rx="122" ry="10" fill="#81adff" fillOpacity=".05" />
  </svg>;
}
