import { useQuery } from "@tanstack/react-query";

import { fetchFactAlignment } from "../services/searchApi";
import type { FactAlignmentResult } from "../types/search";

/**
 * Lazy-loaded similar-fact alignment.
 *
 * The query signal is sent in-request only and never persisted client-side.
 * The query is disabled until `enabled` is true, so it stays off the detail
 * page critical path and degrades independently of base detail info.
 */
export function useFactAlignment(
  caseId: string,
  querySignal: string,
  options: { useMock?: boolean; enabled?: boolean } = {}
) {
  const enabled = Boolean(caseId) && options.enabled === true;
  return useQuery<FactAlignmentResult, Error>({
    // querySignal is intentionally excluded from the key to avoid retaining
    // raw user text in the in-memory query cache keyspace.
    queryKey: ["fact-alignment", caseId, options.useMock === true],
    queryFn: () => fetchFactAlignment(caseId, querySignal, options),
    enabled,
    retry: false,
    staleTime: 0,
    gcTime: 0,
  });
}
