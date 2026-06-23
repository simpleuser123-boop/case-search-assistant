import { useQuery } from "@tanstack/react-query";

import { fetchCaseDetail } from "../services/searchApi";
import type { CaseDetailResult } from "../types/search";

export function useCaseDetail(
  caseId: string,
  options: { useMock?: boolean } = {}
) {
  return useQuery<CaseDetailResult, Error>({
    queryKey: ["case-detail", caseId, options.useMock === true],
    queryFn: () => fetchCaseDetail(caseId, options),
    enabled: Boolean(caseId),
    retry: false,
  });
}
