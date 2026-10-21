const base = { width: 16, height: 16, viewBox: '0 0 16 16', fill: 'none', stroke: 'currentColor', strokeWidth: 1.5, strokeLinecap: 'round' as const, strokeLinejoin: 'round' as const, 'aria-hidden': true };

export const IconExpand = () => (
  <svg {...base}>
    <path d="M9.5 2.5h4v4M6.5 13.5h-4v-4M13.5 2.5 9 7M2.5 13.5 7 9" />
  </svg>
);
export const IconClose = () => (
  <svg {...base}>
    <path d="M3.5 3.5l9 9M12.5 3.5l-9 9" />
  </svg>
);
export const IconPrev = () => (
  <svg {...base}>
    <path d="M10 3.5 5.5 8l4.5 4.5" />
  </svg>
);
export const IconNext = () => (
  <svg {...base}>
    <path d="M6 3.5 10.5 8 6 12.5" />
  </svg>
);
export const IconPlus = () => (
  <svg {...base}>
    <path d="M8 3v10M3 8h10" />
  </svg>
);
export const IconMinus = () => (
  <svg {...base}>
    <path d="M3 8h10" />
  </svg>
);
export const IconFit = () => (
  <svg {...base}>
    <path d="M2.5 5.5v-3h3M10.5 2.5h3v3M13.5 10.5v3h-3M5.5 13.5h-3v-3" />
    <rect x="5.5" y="5.5" width="5" height="5" rx="0.5" />
  </svg>
);
export const IconHome = () => (
  <svg {...base}>
    <path d="M2.5 7.5 8 3l5.5 4.5M4 6.5v6.5h8V6.5" />
  </svg>
);
export const IconPareto = () => (
  <svg {...base}>
    <path d="M2.5 3 6 7.5l4 3 3.5 1.5" strokeDasharray="2.2 2" />
    <circle cx="2.5" cy="3" r="1.4" fill="currentColor" stroke="none" />
    <circle cx="6" cy="7.5" r="1.4" fill="currentColor" stroke="none" />
    <circle cx="10" cy="10.5" r="1.4" fill="currentColor" stroke="none" />
    <circle cx="13.5" cy="12" r="1.4" fill="currentColor" stroke="none" />
  </svg>
);
export const IconDownload = () => (
  <svg {...base}>
    <path d="M8 2.5v8M4.5 7 8 10.5 11.5 7M3 13.5h10" />
  </svg>
);
export const IconLink = () => (
  <svg {...base}>
    <path d="M6.5 9.5l3-3M7 4.5l1-1a2.8 2.8 0 0 1 4 4l-1 1M9 11.5l-1 1a2.8 2.8 0 0 1-4-4l1-1" />
  </svg>
);
export const IconWarning = () => (
  <svg {...base} width={14} height={14}>
    <path d="M8 2.2 14.3 13H1.7z" />
    <path d="M8 6.5v3M8 11.3v.2" />
  </svg>
);
export const IconDoc = () => (
  <svg {...base}>
    <path d="M4 1.8h5.5L12.5 5v9.2H4z M9.5 1.8V5h3M6 8h4.5M6 10.5h4.5" />
  </svg>
);
export const IconExternal = () => (
  <svg {...base} width={12} height={12}>
    <path d="M6.5 3H3v10h10V9.5M9.5 2.5h4v4M13.5 2.5 7.5 8.5" />
  </svg>
);

/** The Zantine mark: a stencil-cut Z. Top and bottom bars are chamfered at opposite corners; the diagonal floats between them. */
const Z_PATHS = ['M5 3H21V7.5H3V5Z', 'M13.42 9H18.92L10.58 15H5.08Z', 'M3 16.5H21V19L19 21H3Z'];

export const Mark = ({ size = 24 }: { size?: number }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden>
    {Z_PATHS.map((d) => (
      <path key={d} d={d} fill="currentColor" />
    ))}
  </svg>
);
