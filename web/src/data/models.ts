// The v1 Model Pool, as checked on 2026-10-20 (docs/model-pool.md).
// Prices are list prices per 1M tokens. Re-check before any paid run.

export type LabId = 'anthropic' | 'google' | 'meta' | 'openai' | 'xai';

export interface Lab {
  id: LabId;
  name: string;
  /**
   * Mark color: muted inks, validated all-pairs on the white page (light mode): every color inside the
   * lightness band, worst CVD ΔE 9.7, worst normal-vision ΔE 17.5, every color at least 3:1 against white.
   */
  color: string;
}

export const LABS: Record<LabId, Lab> = {
  anthropic: { id: 'anthropic', name: 'Anthropic', color: '#8a461a' }, // sienna
  google: { id: 'google', name: 'Google', color: '#b38625' }, // ochre
  meta: { id: 'meta', name: 'Meta', color: '#8f7dcf' }, // heather
  openai: { id: 'openai', name: 'OpenAI', color: '#2ea397' }, // teal
  xai: { id: 'xai', name: 'xAI', color: '#1a5d94' }, // steel
};

export type Tier = 'flagship' | 'sibling';

/** Reasoning effort, in increasing order. Each model lists the levels its API offers. */
export type Effort = 'minimal' | 'low' | 'medium' | 'high' | 'max';
export const EFFORTS: Effort[] = ['minimal', 'low', 'medium', 'high', 'max'];
export const EFFORT_LABEL: Record<Effort, string> = { minimal: 'Minimal', low: 'Low', medium: 'Medium', high: 'High', max: 'Max' };
/** Short tags for chart axes. */
export const EFFORT_TAG: Record<Effort, string> = { minimal: 'Min', low: 'Low', medium: 'Med', high: 'High', max: 'Max' };

export interface Model {
  id: string;
  name: string;
  short: string;
  lab: LabId;
  tier: Tier;
  apiId: string;
  priceIn: number;
  priceOut: number;
  /** Cached input price, or null where it has not been verified. */
  cachedIn: number | null;
  effort: string;
  /** Effort levels the API offers, low to high. */
  efforts: Effort[];
  note: string;
  /** The other model from the same lab, for Flagship–Sibling links. */
  pair?: string;
}

// Fixed alphabetical order. Color and order follow the model, never its rank.
export const MODELS: Model[] = [
  {
    id: 'fable',
    name: 'Claude Fable 5.1',
    short: 'Fable 5.1',
    lab: 'anthropic',
    tier: 'flagship',
    apiId: 'claude-fable-5-1',
    priceIn: 10,
    priceOut: 50,
    cachedIn: 0.25,
    effort: 'medium (default is high)',
    efforts: ['low', 'medium', 'high'],
    note: 'Anthropic Flagship, “for demanding reasoning and long-horizon agentic work.”',
    pair: 'opus',
  },
  {
    id: 'opus',
    name: 'Claude Opus 5.5',
    short: 'Opus 5.5',
    lab: 'anthropic',
    tier: 'sibling',
    apiId: 'claude-opus-5-5',
    priceIn: 4,
    priceOut: 20,
    cachedIn: 0.2,
    effort: 'medium (the default)',
    efforts: ['low', 'medium', 'high'],
    note: 'Sibling of Fable 5.1, Anthropic’s recommended default “for most workloads.”',
    pair: 'fable',
  },
  {
    id: 'argon',
    name: 'Gemini 4 Argon',
    short: 'Gemini 4 Argon',
    lab: 'google',
    tier: 'flagship',
    apiId: 'gemini-4-argon',
    priceIn: 2,
    priceOut: 10,
    cachedIn: 0.1,
    effort: 'medium (low / medium / high)',
    efforts: ['low', 'medium', 'high'],
    note: '“Our flagship reasoning model,” listed as Stable. List price doubles to $4 / $20 on 2026-12-01.',
  },
  {
    id: 'astra',
    name: 'GPT-6 Astra',
    short: 'GPT-6 Astra',
    lab: 'openai',
    tier: 'flagship',
    apiId: 'gpt-6-astra',
    priceIn: 10,
    priceOut: 50,
    cachedIn: null,
    effort: 'medium (low – max)',
    efforts: ['low', 'medium', 'high', 'max'],
    note: 'Listed by OpenAI as “our most capable model.”',
    pair: 'sol',
  },
  {
    id: 'sol',
    name: 'GPT-6.1 Sol',
    short: 'GPT-6.1 Sol',
    lab: 'openai',
    tier: 'sibling',
    apiId: 'gpt-6.1-sol',
    priceIn: 2,
    priceOut: 10,
    cachedIn: null,
    effort: 'medium (low – max)',
    efforts: ['low', 'medium', 'high', 'max'],
    note: 'Sibling of GPT-6 Astra: “near-Astra performance … at a lower cost.”',
    pair: 'astra',
  },
  {
    id: 'grok',
    name: 'Grok 4.7',
    short: 'Grok 4.7',
    lab: 'xai',
    tier: 'flagship',
    apiId: 'grok-4.7',
    priceIn: 2,
    priceOut: 6,
    cachedIn: 0.5,
    effort: 'medium (default is high)',
    efforts: ['low', 'medium', 'high'],
    note: 'xAI Flagship. Prices apply to prompts under 200K tokens.',
  },
  {
    id: 'muse',
    name: 'Muse Wonder',
    short: 'Muse Wonder',
    lab: 'meta',
    tier: 'flagship',
    apiId: 'muse-wonder-1.0',
    priceIn: 4,
    priceOut: 20,
    cachedIn: 0.2,
    effort: 'medium (minimal – max)',
    efforts: ['minimal', 'low', 'medium', 'high', 'max'],
    note: 'Labeled Preview. Muse Wonder Full is Muse Code only, not on the API.',
  },
];

export const MODEL_BY_ID: Record<string, Model> = Object.fromEntries(MODELS.map((m) => [m.id, m]));

/** One model at one effort level: the unit every result chart plots. Ids look like `fable:high`. */
export interface Entry {
  id: string;
  model: Model;
  effort: Effort;
}

export const ENTRIES: Entry[] = MODELS.flatMap((m) => m.efforts.map((effort) => ({ id: `${m.id}:${effort}`, model: m, effort })));
export const ENTRY_BY_ID: Record<string, Entry> = Object.fromEntries(ENTRIES.map((e) => [e.id, e]));

/** The model behind a model id or an entry id. */
export const modelOf = (id: string) => MODEL_BY_ID[id.split(':')[0]];
/** The effort level of an entry id, or undefined for a plain model id. */
export const effortOf = (id: string) => ENTRY_BY_ID[id]?.effort;
/** "Claude Fable 5.1, High effort" for tooltips and tables. */
export const entryName = (id: string) => {
  const e = effortOf(id);
  return e ? `${modelOf(id).name}, ${EFFORT_LABEL[e]} effort` : modelOf(id).name;
};

export const colorOf = (id: string) => LABS[modelOf(id).lab].color;

/**
 * Ordinal ink ramp for non-identity categories: one hue, strongest first (dark→light on the white page).
 * Validated as an ordinal ramp on white; the light end is 2.2:1.
 */
export const INK_RAMP = ['#0d3a73', '#2d5c9b', '#5a87c6', '#8fb0de'];

/** Neutral mark ink for non-model marks (legend glyphs, single-series lines). */
export const INK = '#141b26';
