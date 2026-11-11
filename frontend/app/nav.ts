// The site's task links: the main path first, then the other tools in a menu.
export const tasks = [
  ["/", "Start a race"],
  ["/races", "Races"],
  ["/swarm", "Live board"],
] as const;

export const tools = [
  ["/checks", "Website check"],
  ["/incidents", "Incident investigation"],
  ["/demo", "Local demo"],
] as const;

export type TaskPath = (typeof tasks)[number][0] | (typeof tools)[number][0];
