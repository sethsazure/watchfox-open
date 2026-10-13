// The WatchFox mark: a fox head, outlined so the white muzzle holds its shape. Inverts in dark mode.
export default function Fox({ size = 28, title = 'WatchFox' }) {
  return (
    <svg className="fox" width={size} height={size} viewBox="0 0 64 64" role="img" aria-label={title}
         strokeLinejoin="miter" strokeLinecap="butt">
      {/* head outline with a pale face */}
      <path d="M4 3 L24 17 H40 L60 3 L60 35 L32 62 L4 35 Z" fill="var(--bg)" stroke="currentColor" strokeWidth="3" />
      {/* dark ears and brow */}
      <path d="M4 3 L24 17 H40 L60 3 L60 28 L46 33 L32 27 L18 33 L4 28 Z" fill="currentColor" />
      {/* inner ears */}
      <path d="M11 11 L20 18 L12 25 Z" fill="var(--bg)" />
      <path d="M53 11 L44 18 L52 25 Z" fill="var(--bg)" />
      {/* narrowed eyes */}
      <path d="M16 23 L28 27 L25 31 Z" fill="var(--bg)" />
      <path d="M48 23 L36 27 L39 31 Z" fill="var(--bg)" />
      {/* cheek markings running to the nose */}
      <path d="M6 36 L21 41 L28 56" fill="none" stroke="currentColor" strokeWidth="2.5" />
      <path d="M58 36 L43 41 L36 56" fill="none" stroke="currentColor" strokeWidth="2.5" />
      {/* nose */}
      <path d="M25 54 L39 54 L32 62 Z" fill="currentColor" />
    </svg>
  )
}
