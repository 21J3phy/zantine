// Real, computed design facts (not simulated): the block design, the planned
// precision, the cost projection at list prices, and an example map built with
// the same rules as the engine's generator.

import { mulberry32, shuffle } from '../lib/rng';
import { ENTRIES, MODELS, type Effort, type Model } from './models';

// Block design ---------------------------------------------------------------

function combinations(n: number, k: number): number[][] {
  const out: number[][] = [];
  const pick = (start: number, acc: number[]) => {
    if (acc.length === k) {
      out.push(acc.slice());
      return;
    }
    for (let i = start; i < n; i++) pick(i + 1, [...acc, i]);
  };
  pick(0, []);
  return out;
}

export interface Lobby {
  index: number;
  models: number[];
  /** Impostor Pairing: two model indices, equal for a same-model pairing. */
  impostors: [number, number];
  benign: boolean;
}

/**
 * One 28-game block: all 21 five-of-seven lineups as Balanced Lobbies, each
 * model holding exactly 6 Impostor seats, plus 7 of those lineups replayed as
 * Benign Matches. Found by backtracking; the result is fixed and deterministic.
 */
function buildBlock(): Lobby[] {
  const lobbies = combinations(7, 5);
  const count = new Array(7).fill(0);
  const usedPairs = new Set<string>();
  const chosen: [number, number][] = [];
  const TARGET_SAME = 5;
  let same = 0;

  const options = (models: number[]) => {
    const opts: [number, number][] = [];
    for (let a = 0; a < models.length; a++)
      for (let b = a + 1; b < models.length; b++) opts.push([models[a], models[b]]);
    for (const m of models) opts.push([m, m]);
    return opts;
  };

  const solve = (i: number): boolean => {
    if (i === lobbies.length) return same === TARGET_SAME && count.every((c) => c === 6);
    for (const [a, b] of options(lobbies[i])) {
      const key = `${a}-${b}`;
      const isSame = a === b;
      if (usedPairs.has(key)) continue;
      if (isSame && same >= TARGET_SAME) continue;
      count[a]++;
      count[b]++;
      if (count[a] <= 6 && count[b] <= 6) {
        usedPairs.add(key);
        if (isSame) same++;
        chosen.push([a, b]);
        if (solve(i + 1)) return true;
        chosen.pop();
        if (isSame) same--;
        usedPairs.delete(key);
      }
      count[a]--;
      count[b]--;
    }
    return false;
  };
  solve(0);

  return lobbies.map((models, index) => ({
    index,
    models,
    impostors: chosen[index],
    benign: index % 3 === 0,
  }));
}

export const BLOCK = buildBlock();
export const BLOCK_MODELS: Model[] = MODELS;

// Planned precision ------------------------------------------------------------

const Z = 1.96;

/** 95% CI half-widths as a function of the number of 28-game blocks. */
export const precision = {
  survival: (blocks: number) => Z * Math.sqrt((0.25 * 1.3) / (6 * blocks)),
  correctVote: (blocks: number) => Z * Math.sqrt((0.25 * 1.3) / ((860 / 12) * blocks)),
  damage: (blocks: number) => Z * 6 * Math.sqrt(1 / (5.5 * blocks) + 1 / (7 * blocks)),
};

export const PLANNED_BLOCKS = 12;

// Cost projection ---------------------------------------------------------------

/** Planning estimate per Seat per game: ~4.6M input and ~0.7M output per game, over 10 Seats. */
export const SEAT_TOKENS = { input: 0.46, output: 0.07 };
export const CACHE_SHARE = 0.9;

export interface CostRange {
  id: string;
  uncached: number;
  cached: number;
  cachedKnown: boolean;
}

export const COSTS: CostRange[] = MODELS.map((m) => {
  const out = SEAT_TOKENS.output * m.priceOut;
  const uncached = SEAT_TOKENS.input * m.priceIn + out;
  const cached =
    m.cachedIn === null
      ? uncached
      : SEAT_TOKENS.input * (1 - CACHE_SHARE) * m.priceIn + SEAT_TOKENS.input * CACHE_SHARE * m.cachedIn + out;
  return { id: m.id, uncached, cached, cachedKnown: m.cachedIn !== null };
});

/**
 * Cost at each effort level. Output (including thinking) tokens scale with effort; the
 * multipliers are a planning assumption until the pilot measures them.
 */
export const EFFORT_OUTPUT_FACTOR: Record<Effort, number> = { minimal: 0.45, low: 0.7, medium: 1, high: 1.6, max: 2.6 };

export const ENTRY_COSTS: CostRange[] = ENTRIES.map((en) => {
  const m = en.model;
  const out = SEAT_TOKENS.output * EFFORT_OUTPUT_FACTOR[en.effort] * m.priceOut;
  const uncached = SEAT_TOKENS.input * m.priceIn + out;
  const cached =
    m.cachedIn === null ? uncached : SEAT_TOKENS.input * (1 - CACHE_SHARE) * m.priceIn + SEAT_TOKENS.input * CACHE_SHARE * m.cachedIn + out;
  return { id: en.id, uncached, cached, cachedKnown: m.cachedIn !== null };
});

/** Costs by model id (Medium effort) and by entry id. */
export const COST_BY_ID: Record<string, CostRange> = Object.fromEntries([...COSTS, ...ENTRY_COSTS].map((c) => [c.id, c]));

// Example map ---------------------------------------------------------------------

const ROOM_NAMES = [
  'Reactor', 'Bridge', 'Galley', 'Hydroponics', 'Archive', 'Foundry', 'Observatory', 'Infirmary',
  'Workshop', 'Vault', 'Signal Room', 'Hangar', 'Cold Storage', 'Chartroom', 'Pump Hall', 'Lab',
];

export interface MapRoom {
  name: string;
  col: number;
  row: number;
  meeting: boolean;
}

export interface ExampleMap {
  cols: number;
  rows: number;
  rooms: MapRoom[];
  corridors: [number, number][];
}

/**
 * Same shape as the engine's generator (docs/rules.md): 8 rooms, a random
 * spanning tree plus 1 to 4 extra corridors for loops, no room with more than
 * 4 corridors, and the best-connected room hosts Meetings. Rooms sit on a grid
 * here so the drawing lands on whole centimetres.
 */
export function exampleMap(seed = 7): ExampleMap {
  const rand = mulberry32(seed);
  const cols = 4;
  const rows = 3;
  const key = (c: number, r: number) => r * cols + c;
  const neighbors = (k: number) => {
    const c = k % cols;
    const r = Math.floor(k / cols);
    const out: number[] = [];
    if (c > 0) out.push(key(c - 1, r));
    if (c < cols - 1) out.push(key(c + 1, r));
    if (r > 0) out.push(key(c, r - 1));
    if (r < rows - 1) out.push(key(c, r + 1));
    return out;
  };

  // Grow a connected set of 8 cells.
  const cells = [key(1, 1)];
  while (cells.length < 8) {
    const frontier = cells.flatMap(neighbors).filter((k) => !cells.includes(k));
    cells.push(frontier[Math.floor(rand() * frontier.length)]);
  }

  // Random spanning tree over grid adjacency.
  const index = new Map(cells.map((k, i) => [k, i]));
  const degree = new Array(cells.length).fill(0);
  const corridors: [number, number][] = [];
  const visited = new Set([0]);
  while (visited.size < cells.length) {
    const edges: [number, number][] = [];
    for (const i of visited)
      for (const n of neighbors(cells[i])) {
        const j = index.get(n);
        if (j !== undefined && !visited.has(j)) edges.push([i, j]);
      }
    const [a, b] = edges[Math.floor(rand() * edges.length)];
    corridors.push([a, b]);
    degree[a]++;
    degree[b]++;
    visited.add(b);
  }

  // 1 to 4 extra corridors for loops, never above degree 4.
  const extras = 1 + Math.floor(rand() * 4);
  const candidates: [number, number][] = [];
  cells.forEach((k, i) => {
    for (const n of neighbors(k)) {
      const j = index.get(n);
      if (j === undefined || j <= i) continue;
      if (!corridors.some(([a, b]) => (a === i && b === j) || (a === j && b === i))) candidates.push([i, j]);
    }
  });
  for (const [a, b] of shuffle(candidates, rand).slice(0, extras)) {
    if (degree[a] >= 4 || degree[b] >= 4) continue;
    corridors.push([a, b]);
    degree[a]++;
    degree[b]++;
  }

  const names = shuffle(ROOM_NAMES, rand);
  const top = degree.indexOf(Math.max(...degree));
  return {
    cols,
    rows,
    rooms: cells.map((k, i) => ({
      name: names[i],
      col: k % cols,
      row: Math.floor(k / cols),
      meeting: i === top,
    })),
    corridors,
  };
}
