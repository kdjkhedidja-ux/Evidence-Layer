// Interface icons from the Verdara (HorizonX) template, plus a few drawn in
// the same 24px grid and 1.6 stroke for destinations the template lacks.
const ICON_PATHS = {
  home: (
    <>
      <path d="m3 10 9-7 9 7-3 11H6Z" />
      <path d="M10 21v-7h4v7" />
    </>
  ),
  layers: (
    <>
      <path d="m12 3 9 5-9 5-9-5Z" />
      <path d="m3 12 9 5 9-5M3 16l9 5 9-5" />
    </>
  ),
  team: (
    <>
      <circle cx="9" cy="8" r="3" />
      <path d="M3 21v-3a6 6 0 0 1 12 0v3M16 5a3 3 0 0 1 0 6m2 3a5 5 0 0 1 3 5v2" />
    </>
  ),
  report: (
    <>
      <rect x="4" y="3" width="16" height="18" rx="3" />
      <path d="M8 8h8M8 12h8M8 16h5" />
    </>
  ),
  help: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M9 9a3 3 0 1 1 4 3c-1 1-1 1-1 3M12 18h.01" />
    </>
  ),
  settings: (
    <>
      <path d="m10 2-1 3-3 1-3-1-2 4 3 2v3l-2 2 2 4 3-1 3 1 1 2 4-1 1-3 2-1 3 1 1-4-2-2v-3l2-2-3-3-3 1-2-1-1-3Z" />
      <circle cx="12" cy="12" r="3" />
    </>
  ),
  chart: <path d="M4 20V10M10 20V4M16 20v-7M21 20H3" />,
  link: (
    <>
      <circle cx="6" cy="12" r="3" />
      <circle cx="18" cy="6" r="3" />
      <circle cx="18" cy="18" r="3" />
      <path d="m8.7 10.6 6.6-3.2M8.7 13.4l6.6 3.2" />
    </>
  ),
  plug: <path d="M9 3v5M15 3v5M6 8h12v3a6 6 0 0 1-12 0Zm6 9v4" />,
  gauge: (
    <>
      <path d="M3 17a9 9 0 1 1 18 0" />
      <path d="m12 17 4-6" />
    </>
  ),
  coins: (
    <>
      <ellipse cx="12" cy="6" rx="7" ry="3" />
      <path d="M5 6v6c0 1.7 3.1 3 7 3s7-1.3 7-3V6M5 12v6c0 1.7 3.1 3 7 3s7-1.3 7-3v-6" />
    </>
  ),
} as const;

export type IconName = keyof typeof ICON_PATHS;

export default function NavIcon({ name }: { name: IconName }) {
  return (
    <svg
      aria-hidden="true"
      fill="none"
      stroke="currentColor"
      strokeLinecap="round"
      strokeLinejoin="round"
      strokeWidth="1.6"
      viewBox="0 0 24 24"
    >
      {ICON_PATHS[name]}
    </svg>
  );
}
