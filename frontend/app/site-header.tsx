import { tasks } from "./nav";

// `current` is the page's path; the older tools pass theirs and match no link.
export default function SiteHeader({ current }: { current: string }) {
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
