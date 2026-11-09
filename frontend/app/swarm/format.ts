export function usdc(units: number): string {
  const text = (units / 1e6).toFixed(6).replace(/0+$/, "");
  return text.endsWith(".")
    ? `${text}00`
    : text.length - text.indexOf(".") < 3
      ? `${text}0`
      : text;
}

export function share(bps: number | null | undefined): string {
  return bps == null ? "—" : `${(bps / 100).toFixed(bps % 100 ? 1 : 0)}%`;
}

export function pagePath(urls: string[] | undefined): string {
  const first = urls?.[0];
  if (!first) return "—";
  try {
    const u = new URL(first);
    return u.pathname + u.search || "/";
  } catch {
    return first;
  }
}

export function clock(iso?: string): string {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : d.toISOString().slice(11, 19);
}
