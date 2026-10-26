import { useQuery, useQueryClient } from '@tanstack/react-query';
import { pollMs, queryKeys, useClient, type Pulse } from '@/api';

/** What a change of the pulse refetches: the plan, the shell's counts and Needs you (a person or request logged from
 * the terminal shows on all three). */
export const PULSE_REFETCHES = [queryKeys.plan, queryKeys.shell, queryKeys.needsYou] as const;

/**
 * Polls `GET /api/pulse` (one growing number) every 5 seconds while the tab is visible, and refetches
 * `PULSE_REFETCHES` when the number moves, so a write from the MCP bridge (intake, log_sent) shows on an open page
 * within seconds. No websocket, no heavy poll: the views keep their own slower polls. The number is compared with
 * the one in the cache, so the first read (where the page starts) refetches nothing, and a remount changes nothing.
 */
export function useLivePulse(): void {
  const client = useClient();
  const queryClient = useQueryClient();
  useQuery({
    queryKey: queryKeys.pulse,
    queryFn: async (): Promise<Pulse> => {
      const next = await client.pulse();
      const last = queryClient.getQueryData<Pulse>(queryKeys.pulse);
      if (last !== undefined && last.revision !== next.revision) {
        for (const queryKey of PULSE_REFETCHES) void queryClient.invalidateQueries({ queryKey });
      }
      return next;
    },
    refetchInterval: pollMs.pulse,
    refetchIntervalInBackground: false,
  });
}
