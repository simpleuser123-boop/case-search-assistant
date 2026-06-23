#!/usr/bin/env node
import { createServer as createHttpServer } from "node:http";
import { createServer as createNetServer } from "node:net";
import { existsSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { spawn } from "node:child_process";

const DEFAULT_WEB_BASE = process.env.SMOKE_WEB_BASE || "http://127.0.0.1:5173";
const DEFAULT_API_BASE = process.env.SMOKE_API_BASE || "http://127.0.0.1:8000";
const SMOKE_QUERY = "买卖合同分批交付设备，卖方迟延交付并拒绝退还预付款。";
const REFINED_QUERY = "买卖合同设备迟延交付，卖方拒绝退还预付款并产生解除争议。";
const FORBIDDEN_EVENT_KEYS = new Set([
  "query",
  "raw_query",
  "raw_text",
  "content",
  "text",
  "案情全文",
]);

const options = parseArgs(process.argv.slice(2));
const webBase = options.webBase || DEFAULT_WEB_BASE;
const apiBase = options.apiBase || DEFAULT_API_BASE;
const timeoutMs = Number(options.timeoutMs || 30000);
const useMock = Boolean(options.mock);

main().catch((error) => {
  console.error(
    JSON.stringify(
      {
        status: "failed",
        message: error.message,
      },
      null,
      2
    )
  );
  process.exitCode = 1;
});

async function main() {
  if (!globalThis.WebSocket) {
    throw new Error("Node.js WebSocket is unavailable; use Node 20+.");
  }

  const mockController = useMock ? await startMockApiServer(apiBase) : null;
  const webProbe = await probeWeb(webBase);
  const apiProbe = await probeApi(apiBase, { required: !useMock });
  const browser = await launchBrowser({ headful: Boolean(options.headful) });
  const page = await browser.newPage();

  try {
    await page.setViewport(1280, 900);

    if (useMock) {
      await runMockMainFlow(page, mockController);
      await runMockExceptionFlows(browser, mockController);
    } else {
      await runRealMainFlow(page);
    }

    const allEvents = useMock
      ? mockController.events
      : page.network.analyticsEvents;
    assertAnalyticsEventsSafe(allEvents);

    await page.close();
    await browser.close();
    await mockController?.close();

    console.log(
      JSON.stringify(
        {
          status: "passed",
          mode: useMock ? "mocked_frontend_e2e" : "real_api_e2e",
          web: webProbe,
          api: apiProbe,
          covered_paths: useMock
            ? [
                "home_submit_to_search_results",
                "result_detail_open_close",
                "refine_search",
                "expanded_search",
                "analytics_privacy",
                "llm_timeout_or_rewrite_disabled",
                "chroma_unavailable",
                "zero_results",
                "summary_unavailable",
                "detail_unreachable_retry",
                "network_error_retry",
              ]
            : [
                "home_submit_to_search_results",
                "result_detail_open_close",
                "refine_search",
                page.flags.expandedSearch
                  ? "expanded_search"
                  : "expanded_search_skipped_real_primary_results_sufficient",
                "analytics_privacy",
              ],
          analytics_events: summarizeEvents(allEvents),
        },
        null,
        2
      )
    );
  } catch (error) {
    if (mockController) {
      error.message = `${error.message}; mock state: ${JSON.stringify({
        intercepted: mockController.intercepted,
        fulfilled: mockController.fulfilled,
        errors: mockController.errors,
        search_requests: mockController.searchRequests.length,
        expand_requests: mockController.expandRequests.length,
        events: mockController.events.map((event) => event.event_name),
        pending_search_queue: mockController.searchQueue.length,
        pending_expand_queue: mockController.expandQueue.length,
      })}`;
    }
    await page.close().catch(() => undefined);
    await browser.close().catch(() => undefined);
    await mockController?.close().catch(() => undefined);
    throw error;
  }
}

async function runRealMainFlow(page) {
  await page.goto(`${webBase}/`);
  await page.waitForText("类案检索助手", timeoutMs);
  await page.fill("#case-query", SMOKE_QUERY);
  await page.clickButtonByText("开始检索");
  await page.waitForText("搜索结果", timeoutMs);
  await page.waitForText("可复核案例", timeoutMs);

  const hasResult = await page.evaluate(() =>
    Array.from(document.querySelectorAll("button")).some((button) =>
      button.textContent?.includes("查看详情")
    )
  );
  assert(
    hasResult,
    "real E2E returned no clickable result; rerun with --mock when local retrieval dependencies are unavailable"
  );

  await page.clickButtonByText("查看详情");
  await page.waitForText("案例详情", timeoutMs);
  await page.clickButtonByText("关闭");
  await page.waitForNoDialog(timeoutMs);
  await page.fill("#search-page-query", REFINED_QUERY);
  await page.clickButtonByText("重新检索");
  await page.waitForText("可复核案例", timeoutMs);
  const expanded =
    (await page.clickButtonByTextIfPresent("查看可能相关候选")) ||
    (await page.clickButtonByTextIfPresent("使用扩展检索"));
  page.flags.expandedSearch = expanded;

  if (expanded) {
    await page.waitForText("可能相关候选", timeoutMs);
  }
}

async function runMockMainFlow(page, mock) {
  mock.reset();
  mock.searchQueue.push(
    mockSearchResponse({
      querySessionId: "qs_smoke_main",
      degradedReasons: [
        "LLM_TIMEOUT",
        "CHROMA_UNAVAILABLE",
        "BM25_FALLBACK_USED",
        "SUMMARY_LLM_UNAVAILABLE",
      ],
      result: mockResult({
        title: "合同迟延交付责任候选",
        retrievalSource: ["bm25_fallback"],
        summary: null,
        matchedText: "可核验片段：卖方多次迟延交付设备，买方主张解除合同并返还预付款。",
      }),
    }),
    mockSearchResponse({
      querySessionId: "qs_smoke_refine",
      degradedReasons: ["QUERY_REWRITE_DISABLED"],
      result: mockResult({
        title: "重搜合同履行候选",
        caseId: "case-smoke-refine",
        chunkId: "chunk-smoke-refine-1",
        matchedText: "可核验片段：迟延交付后，双方围绕解除合同和返还价款发生争议。",
      }),
    })
  );
  mock.expandQueue.push(
    mockSearchResponse({
      querySessionId: "qs_smoke_expand",
      degradedReasons: ["BM25_FALLBACK_USED"],
      result: mockResult({
        title: "低置信扩展候选",
        caseId: "case-smoke-expand",
        chunkId: "chunk-smoke-expand-1",
        retrievalSource: ["bm25_fallback_relaxed_recall"],
        confidence: "low",
        finalScore: 0.58,
      }),
    })
  );

  await page.goto(`${webBase}/`);
  await page.waitForText("类案检索助手", timeoutMs);
  await page.fill("#case-query", SMOKE_QUERY);
  await page.clickButtonByText("开始检索");
  await page.waitForText("找到 1 条可复核案例", timeoutMs);
  await page.waitForText("已使用较基础的检索策略", timeoutMs);
  await page.waitForText("案情改写超时，使用原始输入检索。", timeoutMs);
  await page.waitForText("向量库不可用，已回退到基础检索。", timeoutMs);
  await page.waitForText("摘要暂不可用，展示来源片段", timeoutMs);

  await page.clickButtonByText("查看详情");
  await page.waitForText("案例详情", timeoutMs);
  await page.waitForText("法院查明", timeoutMs);
  await page.clickButtonByText("关闭");
  await page.waitForNoDialog(timeoutMs);

  await page.fill("#search-page-query", REFINED_QUERY);
  await page.clickButtonByText("重新检索");
  await page.waitForText("重搜合同履行候选", timeoutMs);
  await page.waitForText("案情改写未启用，使用原始输入检索。", timeoutMs);

  await page.clickButtonByText("查看可能相关候选");
  await page.waitForText("低置信扩展候选", timeoutMs);
  await page.waitForText("低置信候选", timeoutMs);

  assertEventsInclude(mock.events, [
    "search_submit",
    "search_result_render",
    "result_card_click",
    "case_detail_view",
    "search_refine",
    "extended_search_trigger",
  ]);
}

async function runMockExceptionFlows(browser, mock) {
  mock.reset({ keepEvents: true });
  mock.searchQueue.push({
    status: 503,
    body: {
      error: {
        code: "SEARCH_RETRIEVAL_FAILED",
        message: "检索召回暂时不可用，请稍后重试。",
        query_session_id: "qs_retry_error",
      },
    },
  });
  mock.searchQueue.push(
    mockSearchResponse({
      querySessionId: "qs_retry_success",
      result: mockResult({
        title: "网络重试成功候选",
        caseId: "case-smoke-retry",
        chunkId: "chunk-smoke-retry-1",
      }),
    })
  );

  await withFreshPage(browser, async (page) => {
    await page.goto(`${webBase}/search`);
    await page.fill("#search-page-query", SMOKE_QUERY);
    await page.clickButtonByText("重新检索");
    await page.waitForText("检索请求未完成", timeoutMs);
    await page.waitForText("检索召回暂时不可用，请稍后重试。", timeoutMs);
    assert(
      await page
        .textareaValue("#search-page-query")
        .then((value) => value === SMOKE_QUERY),
      "network retry path lost the user input"
    );
    await page.clickButtonByText("重试");
    await page.waitForText("网络重试成功候选", timeoutMs);
  });

  mock.reset({ keepEvents: true });
  mock.searchQueue.push(
    mockSearchResponse({
      querySessionId: "qs_zero",
      results: [],
      degradedReasons: ["QUERY_REWRITE_DISABLED"],
    })
  );
  mock.expandQueue.push(
    mockSearchResponse({
      querySessionId: "qs_zero_expand",
      degradedReasons: ["BM25_FALLBACK_USED"],
      result: mockResult({
        title: "无结果后的扩展候选",
        caseId: "case-smoke-zero-expand",
        chunkId: "chunk-smoke-zero-expand-1",
        retrievalSource: ["bm25_fallback_relaxed_recall"],
        confidence: "low",
      }),
    })
  );

  await withFreshPage(browser, async (page) => {
    await page.goto(`${webBase}/search`);
    await page.fill("#search-page-query", SMOKE_QUERY);
    await page.clickButtonByText("重新检索");
    await page.waitForText("未找到足够匹配的案例", timeoutMs);
    assert(
      await page
        .textareaValue("#search-page-query")
        .then((value) => value === SMOKE_QUERY),
      "zero-result path lost the user input"
    );
    await page.clickButtonByText("使用扩展检索");
    await page.waitForText("无结果后的扩展候选", timeoutMs);
  });

  mock.reset({ keepEvents: true });
  mock.searchQueue.push(
    mockSearchResponse({
      querySessionId: "qs_detail_unreachable",
      result: mockResult({
        title: "详情不可达候选",
        caseId: "case-smoke-detail-fail",
        chunkId: "chunk-smoke-detail-fail-1",
      }),
    })
  );
  mock.detailFailures = 1;

  await withFreshPage(browser, async (page) => {
    await page.goto(`${webBase}/search`);
    await page.fill("#search-page-query", SMOKE_QUERY);
    await page.clickButtonByText("重新检索");
    await page.waitForText("详情不可达候选", timeoutMs);
    await page.clickButtonByText("查看详情");
    await page.waitForText("案例详情加载失败", timeoutMs);
    await page.waitForText("详情服务暂时不可用，请稍后重试。", timeoutMs);
    await page.clickButtonByText("重试");
    await page.waitForText("法院查明", timeoutMs);
  });
}

async function withFreshPage(browser, callback) {
  const page = await browser.newPage();
  await page.setViewport(1280, 900);
  try {
    return await callback(page);
  } finally {
    await page.close().catch(() => undefined);
  }
}

async function startMockApiServer(baseUrl) {
  const url = new URL(baseUrl);
  assert(
    url.protocol === "http:" &&
      ["127.0.0.1", "localhost", "::1"].includes(url.hostname),
    `mock mode requires a local http api base, received ${baseUrl}`
  );
  const port = Number(url.port || 80);
  const controller = {
    events: [],
    searchQueue: [],
    expandQueue: [],
    searchRequests: [],
    expandRequests: [],
    intercepted: [],
    fulfilled: [],
    errors: [],
    detailFailures: 0,
    reset({ keepEvents = false } = {}) {
      if (!keepEvents) {
        this.events = [];
      }
      this.searchQueue = [];
      this.expandQueue = [];
      this.searchRequests = [];
      this.expandRequests = [];
      this.intercepted = [];
      this.fulfilled = [];
      this.errors = [];
      this.detailFailures = 0;
    },
    close: async () =>
      new Promise((resolve) => {
        server.close(() => resolve());
      }),
  };

  const server = createHttpServer(async (request, response) => {
    try {
      const requestUrl = new URL(request.url || "/", baseUrl);
      const pathname = requestUrl.pathname;
      controller.intercepted.push(pathname);

      if (request.method === "OPTIONS") {
        sendJsonResponse(response, 204, null);
        controller.fulfilled.push(pathname);
        return;
      }

      if (request.method === "GET" && pathname === "/health") {
        sendJsonResponse(response, 200, {
          status: "mock",
          secrets_present: { DEEPSEEK_API_KEY: false },
          ollama_reachable: false,
          chroma_collection_queryable: false,
          chroma_chunk_count: 0,
        });
        controller.fulfilled.push(pathname);
        return;
      }

      if (request.method === "GET" && pathname === "/openapi.json") {
        sendJsonResponse(response, 200, {
          openapi: "3.1.0",
          info: { title: "Day 3 smoke mock API", version: "7.1" },
          paths: {
            "/api/search": {},
            "/api/search/expand": {},
            "/api/cases/{case_id}": {},
            "/api/events": {},
          },
        });
        controller.fulfilled.push(pathname);
        return;
      }

      const postData = await readJsonBody(request);

      if (request.method === "POST" && pathname === "/api/events") {
        if (postData) {
          controller.events.push(postData);
        }
        sendJsonResponse(response, 202, {
          accepted: true,
          degraded: false,
          degraded_reasons: [],
          timings: emptyTimings(),
        });
        controller.fulfilled.push(pathname);
        return;
      }

      if (request.method === "POST" && pathname === "/api/search/expand") {
        controller.expandRequests.push(postData);
        sendQueuedJson(
          response,
          controller.expandQueue,
          mockSearchResponse({
            querySessionId: "qs_default_expand",
            degradedReasons: ["BM25_FALLBACK_USED"],
            result: mockResult({ title: "默认扩展候选" }),
          })
        );
        controller.fulfilled.push(pathname);
        return;
      }

      if (request.method === "POST" && pathname === "/api/search") {
        controller.searchRequests.push(postData);
        sendQueuedJson(
          response,
          controller.searchQueue,
          mockSearchResponse({
            querySessionId: "qs_default_search",
            result: mockResult({ title: "默认搜索候选" }),
          })
        );
        controller.fulfilled.push(pathname);
        return;
      }

      if (request.method === "GET" && pathname.startsWith("/api/cases/")) {
        if (controller.detailFailures > 0) {
          controller.detailFailures -= 1;
          sendJsonResponse(response, 503, {
            error: {
              code: "CASE_DETAIL_UNAVAILABLE",
              message: "详情服务暂时不可用，请稍后重试。",
              query_session_id: "qs_detail_error",
            },
          });
          controller.fulfilled.push(pathname);
          return;
        }

        sendJsonResponse(response, 200, mockCaseDetail());
        controller.fulfilled.push(pathname);
        return;
      }

      sendJsonResponse(response, 404, {
        error: {
          code: "SMOKE_NOT_FOUND",
          message: "smoke mock route not found",
          query_session_id: "qs_smoke_not_found",
        },
      });
      controller.fulfilled.push(pathname);
    } catch (error) {
      controller.errors.push(error.message);
      sendJsonResponse(response, 500, {
        error: {
          code: "SMOKE_MOCK_ERROR",
          message: error.message,
          query_session_id: "qs_smoke_mock_error",
        },
      });
    }
  });

  await new Promise((resolve, reject) => {
    server.once("error", (error) => {
      if (error.code === "EADDRINUSE") {
        reject(
          new Error(
            `mock API cannot listen on ${baseUrl}; port is already in use. Stop the service on that port or run real smoke without --mock.`
          )
        );
        return;
      }
      reject(error);
    });
    server.listen(port, resolve);
  });

  return controller;
}

function sendQueuedJson(response, queue, fallback) {
  const next = queue.length > 0 ? queue.shift() : fallback;
  if (next.status && next.body) {
    sendJsonResponse(response, next.status, next.body);
    return;
  }
  sendJsonResponse(response, 200, next);
}

function sendJsonResponse(response, statusCode, body) {
  const payload = body === null ? "" : JSON.stringify(body);
  response.writeHead(statusCode, {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type, Accept",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Content-Type": "application/json",
    "Content-Length": Buffer.byteLength(payload),
  });
  response.end(payload);
}

async function readJsonBody(request) {
  const chunks = [];
  for await (const chunk of request) {
    chunks.push(chunk);
  }
  const raw = Buffer.concat(chunks).toString("utf8").trim();
  return raw ? JSON.parse(raw) : null;
}

function mockSearchResponse({
  querySessionId,
  degradedReasons = [],
  result,
  results,
}) {
  const responseResults =
    results ?? (result ? [result] : [mockResult({ title: "合同履行候选" })]);
  return {
    query_session_id: querySessionId,
    candidates: responseResults,
    results: responseResults,
    degraded: degradedReasons.length > 0,
    degraded_reasons: degradedReasons,
    retrieval_duration_ms: 35,
    timings: emptyTimings({ retrieval_duration_ms: 35, total_duration_ms: 120 }),
  };
}

function mockResult({
  title = "合同履行候选",
  caseId = "case-smoke-001",
  chunkId = "chunk-smoke-001",
  retrievalSource = ["chroma_vector"],
  summary = {
    text: "双方围绕迟延交付和返还预付款发生争议，法院结合履行情况判断责任。",
    source_chunk_id: chunkId,
    source_case_id: caseId,
    method: "extractive",
  },
  matchedText = "可核验片段：合同约定分批交付设备，卖方迟延交付并拒绝退还预付款。",
  confidence = "high",
  finalScore = 0.86,
} = {}) {
  return {
    case_id: caseId,
    chunk_id: chunkId,
    top_chunk_id: chunkId,
    source_chunk_ids: [chunkId],
    hit_chunk_ids: [chunkId],
    retrieval_source: retrievalSource,
    vector_score: retrievalSource.includes("chroma_vector") ? finalScore : null,
    fallback_score: retrievalSource.includes("bm25_fallback") ? finalScore : null,
    retrieval_score: finalScore,
    final_score: finalScore,
    score_breakdown: { smoke_score: finalScore },
    title,
    case_no: "（2026）测01民初1号",
    court: "测试人民法院",
    court_level: "基层法院",
    trial_level: "一审",
    case_cause: "买卖合同纠纷",
    judgment_date: "2026-05-01",
    similarity_score: finalScore,
    confidence,
    summary,
    highlights: [
      {
        text: "迟延交付、解除合同、返还预付款",
        source_chunk_id: chunkId,
      },
    ],
    source_url: null,
    metadata: { source_name: "day3-smoke-fixture" },
    matched_text: matchedText,
  };
}

function mockCaseDetail() {
  return {
    query_session_id: "qs_detail_smoke",
    case_id: "case-smoke-detail",
    case_no: "（2026）测01民初1号",
    title: "合同迟延交付责任候选",
    court: "测试人民法院",
    court_level: "基层法院",
    trial_level: "一审",
    case_cause: "买卖合同纠纷",
    judgment_date: "2026-05-01",
    region: "测试地区",
    source_url: null,
    source_name: "day3-smoke-fixture",
    degraded: false,
    degraded_reasons: [],
    timings: emptyTimings(),
    chunks: [
      {
        chunk_id: "chunk-smoke-001",
        chunk_type: "court_found",
        start_offset: 0,
        end_offset: 120,
        text: "法院查明：双方约定分批交付设备，卖方未按约交付，买方主张解除合同并返还预付款。",
      },
      {
        chunk_id: "chunk-smoke-002",
        chunk_type: "court_opinion",
        start_offset: 121,
        end_offset: 240,
        text: "法院认为：迟延履行是否构成根本违约，需要结合合同目的和催告情况判断。",
      },
    ],
  };
}

function emptyTimings(overrides = {}) {
  return {
    rewrite_duration_ms: 0,
    embedding_duration_ms: 0,
    retrieval_duration_ms: 0,
    rerank_duration_ms: 0,
    summary_duration_ms: 0,
    total_duration_ms: 0,
    ...overrides,
  };
}

async function probeWeb(baseUrl) {
  const response = await fetchWithTimeout(baseUrl, {}, 5000);
  assert(response.ok, `web server is not reachable at ${baseUrl}`);
  return { base_url: baseUrl, reachable: true, status: response.status };
}

async function probeApi(baseUrl, { required }) {
  try {
    const [healthResponse, openapiResponse] = await Promise.all([
      fetchWithTimeout(`${baseUrl}/health`, {}, 8000),
      fetchWithTimeout(`${baseUrl}/openapi.json`, {}, 8000),
    ]);
    const health = await healthResponse.json();
    const openapi = await openapiResponse.json();
    const paths = openapi.paths || {};
    return {
      base_url: baseUrl,
      reachable: true,
      health_status: health.status,
      secrets_present: health.secrets_present,
      ollama_reachable: health.ollama_reachable,
      chroma_collection_queryable: health.chroma_collection_queryable,
      chroma_chunk_count: health.chroma_chunk_count,
      openapi_paths_present: [
        "/api/search",
        "/api/search/expand",
        "/api/cases/{case_id}",
        "/api/events",
      ].every((apiPath) => Object.prototype.hasOwnProperty.call(paths, apiPath)),
    };
  } catch (error) {
    if (required) {
      throw new Error(`api server is not reachable at ${baseUrl}: ${error.message}`);
    }
    return {
      base_url: baseUrl,
      reachable: false,
      skipped_reason: error.message,
    };
  }
}

async function fetchWithTimeout(url, init = {}, timeout = 5000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);
  try {
    return await fetch(url, { ...init, signal: controller.signal });
  } finally {
    clearTimeout(timer);
  }
}

async function launchBrowser({ headful }) {
  const executable = findBrowserExecutable();
  const port = await getFreePort();
  const userDataDir = mkdtempSync(path.join(tmpdir(), "case-search-smoke-"));
  const args = [
    `--remote-debugging-port=${port}`,
    `--user-data-dir=${userDataDir}`,
    "--no-first-run",
    "--disable-extensions",
    "--disable-background-networking",
    "--disable-sync",
    "--disable-features=Translate,MediaRouter",
    headful ? "" : "--headless=new",
    "about:blank",
  ].filter(Boolean);
  const child = spawn(executable, args, {
    stdio: ["ignore", "ignore", "ignore"],
  });
  const close = async () => {
    if (!child.killed && child.exitCode === null) {
      const exited = new Promise((resolve) => child.once("exit", resolve));
      child.kill();
      await Promise.race([exited, delay(2000)]);
    }
    for (let attempt = 0; attempt < 5; attempt += 1) {
      try {
        rmSync(userDataDir, { recursive: true, force: true });
        return;
      } catch (error) {
        if (attempt === 4 || error.code !== "EPERM") {
          throw error;
        }
        await delay(200);
      }
    }
  };

  try {
    await waitFor(async () => {
      const response = await fetch(`http://127.0.0.1:${port}/json/version`).catch(
        () => null
      );
      return response?.ok;
    }, "browser debugging endpoint", 10000);
  } catch (error) {
    await close();
    throw error;
  }

  return {
    async newPage() {
      const response = await fetch(
        `http://127.0.0.1:${port}/json/new?${encodeURIComponent("about:blank")}`,
        { method: "PUT" }
      );
      assert(response.ok, "failed to create browser target");
      const target = await response.json();
      const session = await connectCdp(target.webSocketDebuggerUrl);
      await session.send("Runtime.enable");
      await session.send("Page.enable");
      await session.send("Network.enable");
      return new BrowserPage(session);
    },
    close,
  };
}

function findBrowserExecutable() {
  const candidates = [
    process.env.SMOKE_BROWSER_PATH,
    path.join(
      process.env["PROGRAMFILES(X86)"] || "",
      "Microsoft",
      "Edge",
      "Application",
      "msedge.exe"
    ),
    path.join(
      process.env.PROGRAMFILES || "",
      "Microsoft",
      "Edge",
      "Application",
      "msedge.exe"
    ),
    path.join(
      process.env.PROGRAMFILES || "",
      "Google",
      "Chrome",
      "Application",
      "chrome.exe"
    ),
    path.join(
      process.env.LOCALAPPDATA || "",
      "Google",
      "Chrome",
      "Application",
      "chrome.exe"
    ),
  ].filter(Boolean);
  const executable = candidates.find((candidate) => existsSync(candidate));
  if (!executable) {
    throw new Error("Edge/Chrome executable not found; set SMOKE_BROWSER_PATH.");
  }
  return executable;
}

function getFreePort() {
  return new Promise((resolve, reject) => {
    const server = createNetServer();
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      server.close(() => resolve(address.port));
    });
    server.on("error", reject);
  });
}

async function connectCdp(wsUrl) {
  const ws = new WebSocket(wsUrl);
  await new Promise((resolve, reject) => {
    ws.onopen = resolve;
    ws.onerror = () => reject(new Error("failed to connect to browser websocket"));
  });
  return new CdpSession(ws);
}

class CdpSession {
  constructor(ws) {
    this.ws = ws;
    this.nextId = 1;
    this.pending = new Map();
    this.listeners = new Map();
    ws.onmessage = (message) => {
      const payload = JSON.parse(message.data);
      if (payload.id && this.pending.has(payload.id)) {
        const { resolve, reject } = this.pending.get(payload.id);
        this.pending.delete(payload.id);
        if (payload.error) {
          reject(new Error(payload.error.message));
        } else {
          resolve(payload.result || {});
        }
        return;
      }
      const callbacks = this.listeners.get(payload.method) || [];
      callbacks.forEach((callback) => callback(payload.params || {}));
    };
  }

  send(method, params = {}) {
    const id = this.nextId++;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.ws.send(JSON.stringify({ id, method, params }));
    });
  }

  on(method, callback) {
    const callbacks = this.listeners.get(method) || [];
    callbacks.push(callback);
    this.listeners.set(method, callbacks);
  }

  close() {
    this.ws.close();
  }
}

class BrowserPage {
  constructor(session) {
    this.session = session;
    this.flags = {};
    this.network = { analyticsEvents: [], requests: [], responses: [], failures: [] };
    this.runtimeErrors = [];
    this.consoleMessages = [];
    session.on("Runtime.exceptionThrown", (event) => {
      this.runtimeErrors.push(
        event.exceptionDetails?.exception?.description ||
          event.exceptionDetails?.text ||
          "runtime exception"
      );
    });
    session.on("Runtime.consoleAPICalled", (event) => {
      this.consoleMessages.push({
        type: event.type,
        args: (event.args || []).map((arg) => arg.value || arg.description || ""),
      });
    });
    session.on("Network.requestWillBeSent", (event) => {
      if (event.request?.url?.includes("/api/")) {
        this.network.requests.push({
          requestId: event.requestId,
          url: event.request.url,
          method: event.request.method,
        });
      }
      if (event.request?.url?.includes("/api/events") && event.request.postData) {
        this.network.analyticsEvents.push(JSON.parse(event.request.postData));
      }
    });
    session.on("Network.responseReceived", (event) => {
      if (event.response?.url?.includes("/api/")) {
        this.network.responses.push({
          requestId: event.requestId,
          url: event.response.url,
          status: event.response.status,
        });
      }
    });
    session.on("Network.loadingFailed", (event) => {
      this.network.failures.push({
        requestId: event.requestId,
        errorText: event.errorText,
        canceled: event.canceled,
      });
    });
    session.on("Network.loadingFinished", (event) => {
      this.network.responses = this.network.responses.map((response) =>
        response.requestId === event.requestId
          ? {
              ...response,
              encodedDataLength: event.encodedDataLength,
              finished: true,
            }
          : response
      );
    });
  }

  async goto(url) {
    await this.session.send("Page.navigate", { url });
    await waitFor(async () => {
      const state = await this.evaluate(() => document.readyState);
      return state === "complete" || state === "interactive";
    }, `page navigation to ${url}`, timeoutMs);
  }

  async setViewport(width, height) {
    await this.session.send("Emulation.setDeviceMetricsOverride", {
      width,
      height,
      deviceScaleFactor: 1,
      mobile: width < 600,
    });
  }

  async evaluate(fn) {
    const expression =
      typeof fn === "function" ? `(${fn.toString()})()` : String(fn);
    const result = await this.session.send("Runtime.evaluate", {
      expression,
      awaitPromise: true,
      returnByValue: true,
    });
    if (result.exceptionDetails) {
      throw new Error(result.exceptionDetails.text || "browser evaluation failed");
    }
    return result.result?.value;
  }

  async fill(selector, value) {
    await waitFor(
      () =>
        this.evaluate(
          `document.querySelector(${JSON.stringify(selector)}) !== null`
        ),
      `selector ${selector}`,
      timeoutMs
    );
    const ok = await this.evaluate(`
      (() => {
        const el = document.querySelector(${JSON.stringify(selector)});
        if (!el) return false;
        const setter = Object.getOwnPropertyDescriptor(el.constructor.prototype, "value")?.set;
        if (setter) setter.call(el, ${JSON.stringify(value)});
        else el.value = ${JSON.stringify(value)};
        el.dispatchEvent(new Event("input", { bubbles: true }));
        el.dispatchEvent(new Event("change", { bubbles: true }));
        el.focus();
        return true;
      })()
    `);
    assert(ok, `textarea not found: ${selector}`);
  }

  async textareaValue(selector) {
    return this.evaluate(`
      (() => {
        const el = document.querySelector(${JSON.stringify(selector)});
        return el ? el.value : null;
      })()
    `);
  }

  async clickButtonByText(text) {
    const clicked = await this.clickButtonByTextIfPresent(text);
    assert(clicked, `button not found or disabled: ${text}`);
  }

  async clickButtonByTextIfPresent(text) {
    return this.evaluate(`
      (() => {
        const buttons = Array.from(document.querySelectorAll("button"));
        const button = buttons.find((item) => item.textContent && item.textContent.includes(${JSON.stringify(
          text
        )}) && !item.disabled);
        if (!button) return false;
        button.click();
        return true;
      })()
    `);
  }

  async waitForText(text, timeout = 10000) {
    try {
      await waitFor(
        () =>
          this.evaluate(
            `document.body && document.body.innerText.includes(${JSON.stringify(text)})`
          ),
        `text "${text}"`,
        timeout
      );
    } catch (error) {
      const bodyText = await this.evaluate(
        `document.body ? document.body.innerText.slice(0, 1200) : ""`
      ).catch(() => "");
      throw new Error(
        `${error.message}; current page text: ${bodyText}; network: ${JSON.stringify(
          this.network
        )}; runtime_errors: ${JSON.stringify(
          this.runtimeErrors
        )}; console: ${JSON.stringify(this.consoleMessages.slice(-10))}`
      );
    }
  }

  async waitForNoDialog(timeout = 10000) {
    await waitFor(
      () => this.evaluate(`document.querySelector("[role='dialog']") === null`),
      "detail drawer to close",
      timeout
    );
  }

  async close() {
    this.session.close();
  }
}

async function waitFor(predicate, label, timeout = 10000) {
  const started = Date.now();
  let lastError;
  while (Date.now() - started < timeout) {
    try {
      if (await predicate()) {
        return;
      }
    } catch (error) {
      lastError = error;
    }
    await delay(100);
  }
  throw new Error(
    `Timed out waiting for ${label}${lastError ? `: ${lastError.message}` : ""}`
  );
}

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function assert(condition, message) {
  if (!condition) {
    throw new Error(message);
  }
}

function assertEventsInclude(events, names) {
  const actual = new Set(events.map((event) => event.event_name));
  names.forEach((name) => {
    assert(actual.has(name), `analytics event missing: ${name}`);
  });
}

function assertAnalyticsEventsSafe(events) {
  assert(events.length > 0, "no analytics events captured");
  const raw = JSON.stringify(events);
  [SMOKE_QUERY, REFINED_QUERY, "case-smoke-001", "chunk-smoke-001"].forEach(
    (forbiddenValue) => {
      assert(!raw.includes(forbiddenValue), "analytics payload leaked raw input or id");
    }
  );
  events.forEach((event) => assertNoForbiddenKeys(event));
}

function assertNoForbiddenKeys(value, pathParts = []) {
  if (Array.isArray(value)) {
    value.forEach((item, index) =>
      assertNoForbiddenKeys(item, [...pathParts, String(index)])
    );
    return;
  }
  if (!value || typeof value !== "object") {
    return;
  }
  Object.entries(value).forEach(([key, child]) => {
    assert(
      !FORBIDDEN_EVENT_KEYS.has(key.toLowerCase()),
      `analytics payload contains forbidden key at ${[...pathParts, key].join(".")}`
    );
    assertNoForbiddenKeys(child, [...pathParts, key]);
  });
}

function summarizeEvents(events) {
  const counts = new Map();
  events.forEach((event) => {
    counts.set(event.event_name, (counts.get(event.event_name) || 0) + 1);
  });
  return Object.fromEntries([...counts.entries()].sort());
}

function parseArgs(argv) {
  const parsed = {};
  argv.forEach((arg) => {
    if (arg === "--mock") {
      parsed.mock = true;
      return;
    }
    if (arg === "--headful") {
      parsed.headful = true;
      return;
    }
    const [key, value] = arg.replace(/^--/, "").split("=");
    if (key === "web-base") {
      parsed.webBase = value;
    } else if (key === "api-base") {
      parsed.apiBase = value;
    } else if (key === "timeout-ms") {
      parsed.timeoutMs = value;
    }
  });
  return parsed;
}
