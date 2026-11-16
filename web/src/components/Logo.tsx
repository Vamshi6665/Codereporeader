export function Logo({ size = 26 }: { size?: number }) {
  return (
    <svg aria-hidden="true" width={size} height={size} viewBox="0 0 26 26" style={{ flexShrink: 0, display: "block" }}>
      <rect width="26" height="26" rx="7" fill="var(--btn)" />
      <path
        d="M10 8.5L5.5 13l4.5 4.5M16 8.5l4.5 4.5-4.5 4.5"
        fill="none"
        stroke="var(--btn-text)"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <circle cx="13" cy="13" r="1.6" fill="var(--btn-text)" />
    </svg>
  );
}
