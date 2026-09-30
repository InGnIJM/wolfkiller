/** Readable labels for `recovery_block_code`, with the raw code as the fallback.
 *
 * The backend code is the stable contract (`recovery_blocked` games are keyed by
 * it); the label is only for the lobby tooltip, so an unknown code — a newer
 * backend, or an archive written before a rename — must still show something.
 * The table itself lives with the rest of the copy in `src/labels.ts`.
 */
import { RECOVERY_BLOCK_LABELS } from '../../labels';

export function recoveryBlockLabel(code: string | null | undefined): string | null {
  if (!code) return null;
  return RECOVERY_BLOCK_LABELS[code] ?? code;
}
