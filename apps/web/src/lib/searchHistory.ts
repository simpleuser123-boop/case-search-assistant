// M4-2 检索历史与草稿恢复（F16）。
//
// 隐私边界（M4-1 合同 / 止损线）：
//   - 草稿正文（draft_text）与历史条目正文（query_text）只存在于**浏览器本地**，
//     可一键清除；绝不上送后端持久层、日志、开发报告或测试快照。
//   - 服务端持久层只接受脱敏字段（query_session_id / input_hash / 耗时 / 结果数等），
//     本模块不向服务端写入任何正文。
//   - 历史/草稿不参与、不改变主排序；重搜只是把正文回填输入框，
//     再走与首次检索完全相同的清洗 / 改写降级 / 主排序默认链路。
//   - 不按 query id / case id 做特判。
//
// 本模块是纯函数 + 可注入 storage，便于在无真实浏览器下单测。所有读写都包了
// try/catch：本地存储异常（隐私模式 / 配额 / JSON 损坏）只做安全降级，绝不破坏
// 主检索链路。

// 仅本地存储的草稿结构。draft_text 是用户未提交的原始案情，仅本地、可清除。
export type SearchDraftRecord = {
  // draft_text: 浏览器本地保存的未提交案情正文（仅本地，可清除）。
  draft_text: string;
  // updated_at: 最近一次写入草稿的时间戳（ISO 字符串）。
  updated_at: string;
};

// 单条历史。query_text 为本地侧正文（用于回填重搜）；query_preview 是截断后的展示
// 文本；其余为可识别但脱敏的元数据（时间、输入长度、结果数、是否降级、可选自填标题）。
export type SearchHistoryEntry = {
  // id: 本地生成的稳定标识，仅用于 React key 与删除单条，不参与排序、不上送。
  id: string;
  // query_text: 本地侧原始案情正文，仅用于本地重搜回填（local-only，可清除）。
  query_text: string;
  // query_preview: 截断后的展示文本，避免历史列表渲染整段长正文。
  query_preview: string;
  // input_length: 输入可视字符数（脱敏元数据）。
  input_length: number;
  // result_count: 该次检索可见结果数（脱敏元数据）。
  result_count: number;
  // degraded: 该次检索是否触发降级（脱敏元数据）。
  degraded: boolean;
  // created_at: 该次检索写入历史的时间戳（ISO 字符串）。
  created_at: string;
  // title: 可选的用户自填短标题，便于识别（仅本地，可清除）。
  title?: string;
};

// 注入式存储抽象。生产用 window.localStorage；测试可传内存实现。
export type StorageLike = {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
};

export const SEARCH_DRAFT_STORAGE_KEY = "case-search:m4:search-draft:v1";
export const SEARCH_HISTORY_STORAGE_KEY = "case-search:m4:search-history:v1";

// 历史最多保留的条数（本地侧上限，防止无限增长）。
export const MAX_HISTORY_ENTRIES = 10;
// 历史预览的最大可视字符数。
export const HISTORY_PREVIEW_MAX_CHARS = 80;
// 历史自填标题的最大可视字符数。
export const HISTORY_TITLE_MAX_CHARS = 40;

function nowIso(): string {
  return new Date().toISOString();
}

// 把任意正文截断为不超过 maxChars 个可视字符，超出加省略号。基于 Array.from
// 处理代理对，避免把 emoji / 生僻字截半。
export function truncatePreview(
  value: string,
  maxChars = HISTORY_PREVIEW_MAX_CHARS
): string {
  const collapsed = value.replace(/\s+/g, " ").trim();
  const chars = Array.from(collapsed);
  if (chars.length <= maxChars) {
    return collapsed;
  }
  return `${chars.slice(0, maxChars).join("")}…`;
}

// 生成本地唯一 id。优先 crypto.randomUUID；不可用时退回时间戳 + 随机串。
// id 仅作 React key 与单条删除，不参与排序、不上送。
function makeLocalId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `h_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 10)}`;
}

// ---------- 草稿（draft）----------

// 读取本地草稿。无草稿 / 解析失败 / 缺字段一律返回 null（安全降级）。
export function loadDraft(storage: StorageLike): SearchDraftRecord | null {
  try {
    const raw = storage.getItem(SEARCH_DRAFT_STORAGE_KEY);
    if (!raw) {
      return null;
    }
    const parsed = JSON.parse(raw) as Partial<SearchDraftRecord>;
    if (typeof parsed?.draft_text !== "string") {
      return null;
    }
    const draftText = parsed.draft_text;
    if (!draftText.trim()) {
      return null;
    }
    return {
      draft_text: draftText,
      updated_at:
        typeof parsed.updated_at === "string" ? parsed.updated_at : nowIso(),
    };
  } catch {
    return null;
  }
}

// 写入本地草稿。空白草稿等价于清除（避免存空串）。返回是否写入成功。
export function saveDraft(storage: StorageLike, draftText: string): boolean {
  try {
    if (!draftText.trim()) {
      storage.removeItem(SEARCH_DRAFT_STORAGE_KEY);
      return false;
    }
    const record: SearchDraftRecord = {
      draft_text: draftText,
      updated_at: nowIso(),
    };
    storage.setItem(SEARCH_DRAFT_STORAGE_KEY, JSON.stringify(record));
    return true;
  } catch {
    return false;
  }
}

// 清除本地草稿。
export function clearDraft(storage: StorageLike): void {
  try {
    storage.removeItem(SEARCH_DRAFT_STORAGE_KEY);
  } catch {
    // 清除失败不应阻断主链路。
  }
}

// ---------- 历史（history）----------

function sanitizeEntry(value: unknown): SearchHistoryEntry | null {
  if (!value || typeof value !== "object") {
    return null;
  }
  const entry = value as Partial<SearchHistoryEntry>;
  if (typeof entry.query_text !== "string" || !entry.query_text.trim()) {
    return null;
  }
  const queryText = entry.query_text;
  return {
    id: typeof entry.id === "string" && entry.id ? entry.id : makeLocalId(),
    query_text: queryText,
    query_preview:
      typeof entry.query_preview === "string" && entry.query_preview
        ? entry.query_preview
        : truncatePreview(queryText),
    input_length:
      typeof entry.input_length === "number" && entry.input_length >= 0
        ? Math.round(entry.input_length)
        : Array.from(queryText).length,
    result_count:
      typeof entry.result_count === "number" && entry.result_count >= 0
        ? Math.round(entry.result_count)
        : 0,
    degraded: entry.degraded === true,
    created_at:
      typeof entry.created_at === "string" ? entry.created_at : nowIso(),
    title:
      typeof entry.title === "string" && entry.title.trim()
        ? truncatePreview(entry.title, HISTORY_TITLE_MAX_CHARS)
        : undefined,
  };
}

// 读取本地历史，按 created_at 倒序（最近在前）。解析失败返回空数组。
export function loadHistory(storage: StorageLike): SearchHistoryEntry[] {
  try {
    const raw = storage.getItem(SEARCH_HISTORY_STORAGE_KEY);
    if (!raw) {
      return [];
    }
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) {
      return [];
    }
    return parsed
      .map(sanitizeEntry)
      .filter((entry): entry is SearchHistoryEntry => entry !== null)
      .slice(0, MAX_HISTORY_ENTRIES);
  } catch {
    return [];
  }
}

function persistHistory(
  storage: StorageLike,
  entries: SearchHistoryEntry[]
): SearchHistoryEntry[] {
  const capped = entries.slice(0, MAX_HISTORY_ENTRIES);
  try {
    storage.setItem(SEARCH_HISTORY_STORAGE_KEY, JSON.stringify(capped));
  } catch {
    // 写入失败时仍返回内存态，调用方据此更新 UI；下次写入再尝试落盘。
  }
  return capped;
}

export type AppendHistoryInput = {
  query_text: string;
  result_count: number;
  degraded: boolean;
  input_length?: number;
  title?: string;
};

// 追加一条历史并返回新列表（最近在前）。规则：
//   - 同一 cleaned query_text 视为同一条，去重后把它提到最前并刷新元数据；
//   - 超过 MAX_HISTORY_ENTRIES 截断尾部最旧条目。
// 入参 query_text 应是已清洗的检索输入；本函数不改写、不参与排序。
export function appendHistory(
  storage: StorageLike,
  existing: SearchHistoryEntry[],
  input: AppendHistoryInput
): SearchHistoryEntry[] {
  const queryText = input.query_text;
  if (!queryText.trim()) {
    return existing;
  }
  const entry: SearchHistoryEntry = {
    id: makeLocalId(),
    query_text: queryText,
    query_preview: truncatePreview(queryText),
    input_length:
      typeof input.input_length === "number" && input.input_length >= 0
        ? Math.round(input.input_length)
        : Array.from(queryText).length,
    result_count: input.result_count >= 0 ? Math.round(input.result_count) : 0,
    degraded: input.degraded === true,
    created_at: nowIso(),
    title:
      typeof input.title === "string" && input.title.trim()
        ? truncatePreview(input.title, HISTORY_TITLE_MAX_CHARS)
        : undefined,
  };
  const deduped = existing.filter((item) => item.query_text !== queryText);
  return persistHistory(storage, [entry, ...deduped]);
}

// 删除单条历史，返回新列表。
export function removeHistoryEntry(
  storage: StorageLike,
  existing: SearchHistoryEntry[],
  id: string
): SearchHistoryEntry[] {
  return persistHistory(
    storage,
    existing.filter((item) => item.id !== id)
  );
}

// 清空全部历史。
export function clearHistory(storage: StorageLike): void {
  try {
    storage.removeItem(SEARCH_HISTORY_STORAGE_KEY);
  } catch {
    // 清除失败不应阻断主链路。
  }
}

// 返回可注入的存储实例：浏览器返回 window.localStorage，否则返回 null
// （SSR / 测试无 DOM 时）。访问 localStorage 本身可能抛异常（隐私模式），
// 因此包 try/catch。
export function getBrowserLocalStorage(): StorageLike | null {
  try {
    if (typeof window === "undefined" || !window.localStorage) {
      return null;
    }
    return window.localStorage;
  } catch {
    return null;
  }
}
