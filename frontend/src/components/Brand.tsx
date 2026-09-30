export function Brand({ compact = false, tagline = false }: { compact?: boolean; tagline?: boolean }) {
  return <span className={`xx-brand ${compact ? 'xx-brand-compact' : ''}`}>
    <span className="xx-wordmark" aria-label="xx">xx</span>
    {tagline && <span className="xx-tagline">I listen and I don't judge</span>}
  </span>;
}
