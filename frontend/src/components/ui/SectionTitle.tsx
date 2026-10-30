export function SectionTitle({ number, title, sub }: { number?: string; title: string; sub?: string }) {
  return <div className="section-heading"><div>{number && <span className="section-number">{number}</span>}<h2>{title}</h2></div>{sub && <p>{sub}</p>}</div>
}
