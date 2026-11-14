import type { H1bTag } from '../types';

const tag = (label: string, trend: string, years: string): H1bTag => ({ label, trend, detail: `USCIS approvals by fiscal year: ${years}` });

/** The sample companies' H-1B tags (#252). Any other company has none, as with no H-1B list built. */
const SAMPLE: Record<string, H1bTag> = {
  'Retell AI': tag('H-1B 14, rising', 'rising', 'FY2022 0, FY2023 1, FY2024 2, FY2025 4, FY2026 7'),
  Pylon: { label: 'No H-1B on record', trend: 'none', detail: 'No USCIS H-1B approvals for this company in the list.' },
  Baseten: tag('H-1B 2,436, rising', 'rising', 'FY2022 301, FY2023 380, FY2024 410, FY2025 520, FY2026 825'),
  Kestrel: tag('H-1B 260, steady', 'steady', 'FY2022 50, FY2023 55, FY2024 51, FY2025 52, FY2026 52'),
  Resend: tag('H-1B 12, new', 'new', 'FY2022 0, FY2023 0, FY2024 0, FY2025 0, FY2026 12'),
  Graphite: tag('H-1B 31, falling', 'falling', 'FY2022 12, FY2023 9, FY2024 6, FY2025 3, FY2026 1'),
};

export const sampleH1b = (companyName: string): H1bTag | null => SAMPLE[companyName] ?? null;
