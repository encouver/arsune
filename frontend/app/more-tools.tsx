"use client";

import { useEffect, useRef } from "react";
import { tools } from "./nav";

export default function MoreTools({ current }: { current: string }) {
  const menu = useRef<HTMLDetailsElement>(null);
  const inside = tools.some(([path]) => path === current);

  // A <details> menu stays open until closed: close it on an outside click or Escape.
  useEffect(() => {
    function click(e: MouseEvent) {
      const el = menu.current;
      if (el?.open && !el.contains(e.target as Node)) el.open = false;
    }
    function key(e: KeyboardEvent) {
      const el = menu.current;
      if (e.key !== "Escape" || !el?.open) return;
      el.open = false;
      el.querySelector("summary")?.focus();
    }
    document.addEventListener("click", click);
    document.addEventListener("keydown", key);
    return () => {
      document.removeEventListener("click", click);
      document.removeEventListener("keydown", key);
    };
  }, []);

  return (
    <details
      className="task-more"
      ref={menu}
      data-current={inside ? "true" : undefined}
    >
      <summary>More tools</summary>
      <div className="task-menu">
        {tools.map(([path, label]) => (
          <a
            key={path}
            href={path}
            aria-current={path === current ? "page" : undefined}
          >
            {label}
          </a>
        ))}
      </div>
    </details>
  );
}
