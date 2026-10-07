export function Brand({ compact = false, tagline = false, icon = true }: { compact?: boolean; tagline?: boolean; icon?: boolean }) {
  return <span className={`xx-brand ${compact ? 'xx-brand-compact' : ''}`}>
    {compact ? <img className="xx-brand-mascot" src="/oats-buddy.png" alt="oats" width={48} height={48} /> : <span className="xx-brand-lockup">
      {icon && <img className="xx-brand-mascot" src="/oats-buddy.png" alt="" width={48} height={48} />}
      <span className="xx-wordmark" aria-label="oats">oats</span>
    </span>}
    {tagline && <span className="xx-tagline">I listen and I don't judge</span>}
  </span>;
}
