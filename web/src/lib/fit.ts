export type FitBand = 'good' | 'mid' | 'low';

/** The judge's fit in three bands: green from 70, amber 50 to 69, muted below 50 (a weak fit is quiet, never red). */
export function fitBand(fit: number): FitBand {
  return fit >= 70 ? 'good' : fit >= 50 ? 'mid' : 'low';
}
