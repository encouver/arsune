const tasks = [
  ["/", "Website checks"],
  ["/incidents", "Incident investigation"],
  ["/demo", "Local demo"],
] as const;

type TaskPath = (typeof tasks)[number][0];

export default function SiteHeader({ current }: { current: TaskPath }) {
  return (
    <header className="site-header">
      <a href="/" className="brand" aria-label="Arsune home">
        <span className="brand-mark" aria-hidden="true">
          a
        </span>
        arsune<span className="brand-dot">.</span>
      </a>
      <nav className="task-nav" aria-label="Tasks">
        {tasks.map(([path, label]) => (
          <a
            key={path}
            href={path}
            aria-current={path === current ? "page" : undefined}
          >
            {label}
          </a>
        ))}
      </nav>
    </header>
  );
}
