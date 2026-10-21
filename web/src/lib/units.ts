// CSS defines 1cm as exactly 96/2.54 px. Every position and size on the canvas
// is a whole number of centimetres, so the drawing always lands on the grid.
export const CM = 96 / 2.54;

export const px = (cm: number) => cm * CM;

/** Round a pixel length up to the next whole centimetre. */
export const snapUp = (lengthPx: number) => Math.ceil(lengthPx / CM - 1e-3) * CM;

export const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));
