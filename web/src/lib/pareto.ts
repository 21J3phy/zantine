export type Better = 'higher' | 'lower';

/** The points no other point beats on both axes at once (the Pareto frontier), ordered by x. */
export function paretoFront<T extends { x: number; y: number }>(pts: T[], better: { x: Better; y: Better }): T[] {
  const sx = better.x === 'higher' ? 1 : -1;
  const sy = better.y === 'higher' ? 1 : -1;
  const beats = (b: T, a: T) => sx * b.x >= sx * a.x && sy * b.y >= sy * a.y && (sx * b.x > sx * a.x || sy * b.y > sy * a.y);
  return pts.filter((a) => !pts.some((b) => beats(b, a))).sort((a, b) => a.x - b.x);
}
