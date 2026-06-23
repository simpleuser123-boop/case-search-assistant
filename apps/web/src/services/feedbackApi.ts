import { hashAnalyticsIdentifier } from "./analytics";

export const FEEDBACK_API_PATH = "/api/feedback";

export type FeedbackSelection = "relevant" | "not_relevant";
export type FeedbackValue = FeedbackSelection | "cleared";
export type FeedbackSearchMode = "standard" | "expanded";
export type FeedbackConfidenceLevel = "high" | "medium" | "low";

export type FeedbackEventRequest = {
  event_type: "result_feedback";
  session_hash: string;
  query_hash: string;
  case_id_hash: string;
  rank: number;
  feedback_value: FeedbackValue;
  search_mode: FeedbackSearchMode;
  confidence_level: FeedbackConfidenceLevel;
};

export type FeedbackEventInput = {
  querySessionId?: string | null;
  queryText: string;
  caseId: string;
  rank: number;
  feedbackValue: FeedbackValue;
  searchMode: FeedbackSearchMode;
  confidenceLevel: FeedbackConfidenceLevel;
};

export type FeedbackSubmitResult = {
  sent: boolean;
  reason?: "invalid_payload" | "network_error" | "http_error";
  status?: number;
  request?: FeedbackEventRequest;
};

export async function buildFeedbackEventRequest(
  input: FeedbackEventInput
): Promise<
  | {
      request: FeedbackEventRequest;
      reason?: never;
    }
  | {
      request?: never;
      reason: FeedbackSubmitResult["reason"];
    }
> {
  const querySessionId = input.querySessionId?.trim();
  const queryText = input.queryText.trim();
  const caseId = input.caseId.trim();

  if (!querySessionId || !queryText || !caseId || !Number.isInteger(input.rank) || input.rank < 1) {
    return { reason: "invalid_payload" };
  }

  return {
    request: {
      event_type: "result_feedback",
      session_hash: await hashAnalyticsIdentifier(querySessionId),
      query_hash: await hashAnalyticsIdentifier(queryText),
      case_id_hash: await hashAnalyticsIdentifier(caseId),
      rank: input.rank,
      feedback_value: input.feedbackValue,
      search_mode: input.searchMode,
      confidence_level: input.confidenceLevel,
    },
  };
}

export async function submitFeedbackEvent(
  input: FeedbackEventInput
): Promise<FeedbackSubmitResult> {
  const requestResult = await buildFeedbackEventRequest(input);

  if (!requestResult.request) {
    return {
      sent: false,
      reason: requestResult.reason,
    };
  }

  if (typeof fetch === "undefined") {
    return {
      sent: false,
      reason: "network_error",
      request: requestResult.request,
    };
  }

  try {
    const response = await fetch(FEEDBACK_API_PATH, {
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
      },
      body: JSON.stringify(requestResult.request),
    });

    if (!response.ok) {
      return {
        sent: false,
        reason: "http_error",
        status: response.status,
        request: requestResult.request,
      };
    }

    return {
      sent: true,
      status: response.status,
      request: requestResult.request,
    };
  } catch {
    return {
      sent: false,
      reason: "network_error",
      request: requestResult.request,
    };
  }
}
