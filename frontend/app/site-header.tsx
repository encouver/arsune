import MoreTools from "./more-tools";
import { tasks, type TaskPath } from "./nav";

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
        <MoreTools current={current} />
      </nav>
    </header>
  );
}
