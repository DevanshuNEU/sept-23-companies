import type { Actor, CompanyEvent, ImportSource } from '@/api';
import type { Tone } from '@/lib/status';

const TONE_BY_KIND: Record<string, Tone> = { reply: 'ok', bounce: 'bad' };

/** A reply is a win and a bounce is a stop; everything else is history. */
export function eventTone(e: CompanyEvent): Tone {
  return TONE_BY_KIND[e.kind] ?? 'idle';
}

const ACTOR_TAG = {
  devanshu: 'You',
  claude: 'Claude',
  sender: 'Sender',
  crawler: 'Crawler',
  rules: 'Rules',
  daily_list: 'Daily list',
  import: 'Imported',
  system: 'System',
} satisfies Record<Actor, string>;

// Design 5.6: for imports the tag names the old place the row came from.
const IMPORT_TAG = {
  data_js: 'Old dashboard',
  local_storage: 'Browser stages',
  contacts_json: 'Old contacts',
  radar_db: 'Job Radar',
  devboard: 'Old devboard',
  contacted_txt: 'contacted.txt',
  devvault_notes: 'DevVault notes',
} satisfies Record<ImportSource, string>;

/** The source tag of a History row: who did it, or which old file it was imported from. */
export function sourceTag(e: CompanyEvent): string {
  return e.importSource ? IMPORT_TAG[e.importSource] : ACTOR_TAG[e.actor];
}
