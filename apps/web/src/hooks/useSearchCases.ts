import { useMutation } from "@tanstack/react-query";

import { expandSearchCases, searchCases } from "../services/searchApi";
import type { SearchCasesResult } from "../types/search";

export interface SearchCasesVariables {
  query: string;
  limit?: number;
  useMock?: boolean;
}

export function useSearchCases() {
  return useMutation<SearchCasesResult, Error, SearchCasesVariables>({
    retry: false,
    mutationFn: ({ query, limit = 10, useMock }) =>
      searchCases(
        {
          query,
          mode: "standard",
          limit,
        },
        { useMock }
      ),
  });
}

export function useExpandSearchCases() {
  return useMutation<SearchCasesResult, Error, SearchCasesVariables>({
    retry: false,
    mutationFn: ({ query, limit = 10, useMock }) =>
      expandSearchCases(
        {
          query,
          mode: "expand",
          limit,
        },
        { useMock }
      ),
  });
}
