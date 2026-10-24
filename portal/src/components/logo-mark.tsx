/** The lanes mark, copied from the portal canvas header. */
export function LogoMark({ size = 26 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 26 26" aria-hidden="true">
      <path d="M3 6 C 9 6, 9 13, 15 13" stroke="#71717A" strokeWidth="2" fill="none" strokeLinecap="round" />
      <path d="M3 20 C 9 20, 9 13, 15 13" stroke="#71717A" strokeWidth="2" fill="none" strokeLinecap="round" />
      <rect x="14" y="9" width="9" height="8" rx="2" fill="#A371F7" />
      <path d="M18 11 l-2 3 h2 l-1 2" stroke="#3FB950" strokeWidth="1.6" fill="none" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
