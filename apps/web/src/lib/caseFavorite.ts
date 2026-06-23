// M4-3 案例收藏能力（F17）。
//
// 隐私边界（M4-1 合同 / 止损线）：
//   - 收藏只保存**元数据**（case_id / case_number / court / trial_level /
//     case_cause / judgment_date）、**来源锚点**（case_id + source_chunk_id +
//     anchor_type，用于回跳详情）和**用户自填短字段**（note / tag）。
//   - 绝不复制并存储案例正文、摘要正文、要旨正文、chunk 正文、裁判文书长正文，
//     也不存储原始 query 或任何自由长文本。
//   - 收藏只存在于**浏览器本地**（localStorage），可增、可删、可清空；
//     绝不上送后端持久层、日志、开发报告或测试快照。
//   - 收藏不参与、不改变主排序，不作为运行时排序 / 召回 / source selection 特征；
//     不按 case id / qrels / label / relevance 特判。
//   - 日志只记录 count / status / reason_code，不记录正文。
//
// 本模块是纯函数 + 可注入 storage，便于在无真实浏览器下单测。所有读写都包了
// try/catch：本地存储异常（隐私模式 / 配额 / JSON 损坏）只做安全降级，绝不破坏
// 主检索 / 阅读链路。

// 收藏用的来源锚点：只保留可回跳所需的最小标识字段，绝不含正文。
export type FavoriteSourceAnchor = {
  // case_id + source_chunk_id 是 M4-1 合同要求的锚点最小字段。
  case_id: string;
  source_chunk_id: string;
  // anchor_type / chunk_type 仅为短枚举型标识，用于回跳时定位片段类型，非正文。
  anchor_type?: string;
  chunk_type?: string | null;
};

// 单条收藏记录。全部为元数据 / 锚点 / 用户自填短字段，零正文。
export type CaseFavoriteRecord = {
  // case_id: 案例稳定标识，去重与回跳详情用。
  case_id: string;
  // case_number: 案号（元数据）。
  case_number: string;
  // court / trial_level / case_cause / judgment_date: 案例元数据。
  court: string;
  trial_level: string;
  case_cause: string;
  judgment_date: string;
  // source_anchors: 可选，用于回跳案例详情；只含锚点标识字段，无正文。
  source_anchors: FavoriteSourceAnchor[];
  // note: 用户自填短备注（截断到上限，仅本地、可清除）。
  note: string;
  // tag: 用户自填短标签（截断到上限，仅本地、可清除）。
  tag: string;
  // created_at: 收藏写入时间戳（ISO 字符串）。
  created_at: string;
  // favorite_status: 收藏状态，恒为 "favorited"（取消即从列表移除）。
  favorite_status: "favorited";
};

// 注入式存储抽象。生产用 window.localStorage；测试可传内存实现。
export type StorageLike = {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
};

export const CASE_FAVORITE_STORAGE_KEY = "case-search:m4:case-favorite:v1";

// 收藏本地上限，防止无限增长。
export const MAX_FAVORITE_ENTRIES = 200;
// 用户自填备注 / 标签的最大可视字符数（短字段约束）。
export const FAVORITE_NOTE_MAX_CHARS = 120;
export const FAVORITE_TAG_MAX_CHARS = 24;
// 每条收藏保留的来源锚点上限。
export const FAVORITE_ANCHOR_MAX = 4;

export type FavoriteReasonCode =
  | "missing_case_id"
  | "already_favorited"
  | "not_found"
  | "storage_unavailable"
  | "limit_reached";

function nowIso(): string {
  return new Date().toISOString();
}

function clean(value: string | null | undefined): string {
  return (value || "").trim();
}

// 把用户自填短字段折叠空白并截断到上限，避免长正文混入收藏存储。
export function truncateShortField(value: string | null | undefined, maxChars: number): string {
  const collapsed = (value || "").replace(/\s+/g, " ").trim();
  const chars = Array.from(collapsed);
  if (chars.length <= maxChars) {
    return collapsed;
  }
  return chars.slice(0, maxChars).join("");
}

// 结构化的元数据来源视图。SearchResultItem / CaseDetailResponse 都满足它，
// 故同一构造器可服务结果卡片、详情抽屉、对比视图。只读元数据字段，绝不读正文。
export type FavoriteMetadataSource = {
  case_id?: string | null;
  case_no?: string | null;
  court?: string | null;
  trial_level?: string | null;
  court_level?: string | null;
  case_cause?: string | null;
  judgment_date?: string | null;
  source_anchors?: Array<{
    case_id?: string | null;
    source_chunk_id?: string | null;
    anchor_type?: string | null;
    chunk_type?: string | null;
  } | null> | null;
};

// 只保留 case_id + source_chunk_id 齐全的锚点（M4-1 合同最小字段），其余丢弃。
// 仅取归属本案的锚点，避免跨案锚点污染回跳。最多保留 FAVORITE_ANCHOR_MAX 条。
export function sanitizeFavoriteAnchors(
  source: FavoriteMetadataSource | null | undefined,
  caseId: string
): FavoriteSourceAnchor[] {
  const anchors = source?.source_anchors || [];
  const result: FavoriteSourceAnchor[] = [];
  const seen = new Set<string>();
  for (const anchor of anchors) {
    const aCaseId = clean(anchor?.case_id);
    const chunkId = clean(anchor?.source_chunk_id);
    if (!aCaseId || !chunkId || aCaseId !== caseId) {
      continue;
    }
    const anchorType = clean(anchor?.anchor_type) || "case_record";
    const key = `${chunkId}:${anchorType}`;
    if (seen.has(key)) {
      continue;
    }
    seen.add(key);
    result.push({
      case_id: aCaseId,
      source_chunk_id: chunkId,
      anchor_type: anchorType,
      chunk_type:
        typeof anchor?.chunk_type === "string" && anchor.chunk_type.trim()
          ? anchor.chunk_type.trim()
          : null,
    });
    if (result.length >= FAVORITE_ANCHOR_MAX) {
      break;
    }
  }
  return result;
}

// 从任意案例元数据来源构造一条收藏记录（元数据 + 锚点 + 用户自填短字段）。
// trial_level 缺失时回退 court_level（数据模型两者并存）。绝不读取任何正文。
export function buildFavoriteRecord(
  source: FavoriteMetadataSource | null | undefined,
  userFields?: { note?: string; tag?: string }
): CaseFavoriteRecord {
  const caseId = clean(source?.case_id);
  return {
    case_id: caseId,
    case_number: clean(source?.case_no),
    court: clean(source?.court),
    trial_level: clean(source?.trial_level) || clean(source?.court_level),
    case_cause: clean(source?.case_cause),
    judgment_date: clean(source?.judgment_date),
    source_anchors: sanitizeFavoriteAnchors(source, caseId),
    note: truncateShortField(userFields?.note, FAVORITE_NOTE_MAX_CHARS),
    tag: truncateShortField(userFields?.tag, FAVORITE_TAG_MAX_CHARS),
    created_at: nowIso(),
    favorite_status: "favorited",
  };
}

function sanitizeAnchorList(value: unknown): FavoriteSourceAnchor[] {
  if (!Array.isArray(value)) {
    return [];
  }
  const result: FavoriteSourceAnchor[] = [];
  for (const raw of value) {
    if (!raw || typeof raw !== "object") {
      continue;
    }
    const anchor = raw as Partial<FavoriteSourceAnchor>;
    const caseId = clean(anchor.case_id);
    const chunkId = clean(anchor.source_chunk_id);
    if (!caseId || !chunkId) {
      continue;
    }
    result.push({
      case_id: caseId,
      source_chunk_id: chunkId,
      anchor_type: clean(anchor.anchor_type) || "case_record",
      chunk_type:
        typeof anchor.chunk_type === "string" && anchor.chunk_type.trim()
          ? anchor.chunk_type.trim()
          : null,
    });
    if (result.length >= FAVORITE_ANCHOR_MAX) {
      break;
    }
  }
  return result;
}

// 反序列化时清洗单条记录：缺 case_id 视为无效；只接受白名单字段，丢弃任何
// 可能从旧版本 / 篡改数据混入的非白名单键（含潜在正文字段）。
function sanitizeRecord(value: unknown): CaseFavoriteRecord | null {
  if (!value || typeof value !== "object") {
    return null;
  }
  const record = value as Partial<CaseFavoriteRecord>;
  const caseId = clean(record.case_id);
  if (!caseId) {
    return null;
  }
  return {
    case_id: caseId,
    case_number: clean(record.case_number),
    court: clean(record.court),
    trial_level: clean(record.trial_level),
    case_cause: clean(record.case_cause),
    judgment_date: clean(record.judgment_date),
    source_anchors: sanitizeAnchorList(record.source_anchors),
    note: truncateShortField(record.note, FAVORITE_NOTE_MAX_CHARS),
    tag: truncateShortField(record.tag, FAVORITE_TAG_MAX_CHARS),
    created_at: typeof record.created_at === "string" ? record.created_at : nowIso(),
    favorite_status: "favorited",
  };
}

// ---------- CRUD（均仅本地、可清除）----------

// 读取本地收藏列表，按 created_at 倒序（最近收藏在前）。解析失败返回空数组。
export function loadFavorites(storage: StorageLike): CaseFavoriteRecord[] {
  try {
    const raw = storage.getItem(CASE_FAVORITE_STORAGE_KEY);
    if (!raw) {
      return [];
    }
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) {
      return [];
    }
    const sanitized = parsed
      .map(sanitizeRecord)
      .filter((entry): entry is CaseFavoriteRecord => entry !== null);
    // 去重（同 case_id 取最先出现的一条），再按时间倒序、截断到上限。
    const deduped: CaseFavoriteRecord[] = [];
    const seen = new Set<string>();
    for (const entry of sanitized) {
      if (seen.has(entry.case_id)) {
        continue;
      }
      seen.add(entry.case_id);
      deduped.push(entry);
    }
    return sortNewestFirst(deduped).slice(0, MAX_FAVORITE_ENTRIES);
  } catch {
    return [];
  }
}

function sortNewestFirst(entries: CaseFavoriteRecord[]): CaseFavoriteRecord[] {
  return [...entries].sort((a, b) => b.created_at.localeCompare(a.created_at));
}

function persist(storage: StorageLike, entries: CaseFavoriteRecord[]): CaseFavoriteRecord[] {
  const capped = sortNewestFirst(entries).slice(0, MAX_FAVORITE_ENTRIES);
  try {
    storage.setItem(CASE_FAVORITE_STORAGE_KEY, JSON.stringify(capped));
  } catch {
    // 写入失败仍返回内存态，调用方据此更新 UI；下次写入再尝试落盘。
  }
  return capped;
}

export function isFavorited(entries: CaseFavoriteRecord[], caseId: string): boolean {
  const id = clean(caseId);
  return id ? entries.some((entry) => entry.case_id === id) : false;
}

export type AddFavoriteResult = {
  entries: CaseFavoriteRecord[];
  added: boolean;
  reason?: FavoriteReasonCode;
};

// 新增一条收藏并返回新列表。已收藏则幂等返回（added=false）。超过上限拒绝写入。
export function addFavorite(
  storage: StorageLike,
  existing: CaseFavoriteRecord[],
  source: FavoriteMetadataSource | null | undefined,
  userFields?: { note?: string; tag?: string }
): AddFavoriteResult {
  const record = buildFavoriteRecord(source, userFields);
  if (!record.case_id) {
    return { entries: existing, added: false, reason: "missing_case_id" };
  }
  if (existing.some((entry) => entry.case_id === record.case_id)) {
    return { entries: existing, added: false, reason: "already_favorited" };
  }
  if (existing.length >= MAX_FAVORITE_ENTRIES) {
    return { entries: existing, added: false, reason: "limit_reached" };
  }
  return { entries: persist(storage, [record, ...existing]), added: true };
}

// 取消收藏（按 case_id 删除），返回新列表。
export function removeFavorite(
  storage: StorageLike,
  existing: CaseFavoriteRecord[],
  caseId: string
): CaseFavoriteRecord[] {
  const id = clean(caseId);
  if (!id) {
    return existing;
  }
  return persist(
    storage,
    existing.filter((entry) => entry.case_id !== id)
  );
}

// 切换收藏状态：已收藏则取消，未收藏则新增。返回新列表与最终是否已收藏。
export function toggleFavorite(
  storage: StorageLike,
  existing: CaseFavoriteRecord[],
  source: FavoriteMetadataSource | null | undefined,
  userFields?: { note?: string; tag?: string }
): { entries: CaseFavoriteRecord[]; favorited: boolean; reason?: FavoriteReasonCode } {
  const caseId = clean(source?.case_id);
  if (!caseId) {
    return { entries: existing, favorited: false, reason: "missing_case_id" };
  }
  if (existing.some((entry) => entry.case_id === caseId)) {
    return { entries: removeFavorite(storage, existing, caseId), favorited: false };
  }
  const result = addFavorite(storage, existing, source, userFields);
  return { entries: result.entries, favorited: result.added, reason: result.reason };
}

// 更新某条收藏的用户自填短字段（note / tag），返回新列表。
export function updateFavoriteFields(
  storage: StorageLike,
  existing: CaseFavoriteRecord[],
  caseId: string,
  userFields: { note?: string; tag?: string }
): CaseFavoriteRecord[] {
  const id = clean(caseId);
  if (!id) {
    return existing;
  }
  let changed = false;
  const next = existing.map((entry) => {
    if (entry.case_id !== id) {
      return entry;
    }
    changed = true;
    return {
      ...entry,
      note:
        userFields.note !== undefined
          ? truncateShortField(userFields.note, FAVORITE_NOTE_MAX_CHARS)
          : entry.note,
      tag:
        userFields.tag !== undefined
          ? truncateShortField(userFields.tag, FAVORITE_TAG_MAX_CHARS)
          : entry.tag,
    };
  });
  return changed ? persist(storage, next) : existing;
}

// 清空全部收藏。
export function clearFavorites(storage: StorageLike): void {
  try {
    storage.removeItem(CASE_FAVORITE_STORAGE_KEY);
  } catch {
    // 清除失败不应阻断主链路。
  }
}

// 脱敏日志：只记录 event / surface / status / reason_code / count，绝不含正文、
// 案号、note、tag、query 或任何用户输入。
export type FavoriteLogSurface = "result_card" | "detail" | "compare" | "favorite_list";
export type FavoriteLogStatus = "favorited" | "unfavorited" | "cleared" | "noop";

export type FavoriteActionLog = {
  event: "case_favorite_action";
  surface: FavoriteLogSurface;
  status: FavoriteLogStatus;
  reason_code: FavoriteReasonCode | null;
  count: number;
};

export function buildFavoriteLog({
  surface,
  status,
  reason,
  count = 1,
}: {
  surface: FavoriteLogSurface;
  status: FavoriteLogStatus;
  reason?: FavoriteReasonCode | null;
  count?: number;
}): FavoriteActionLog {
  return {
    event: "case_favorite_action",
    surface,
    status,
    reason_code: reason ?? null,
    count: count > 0 ? Math.round(count) : 1,
  };
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

export const FAVORITE_REASON_CODES: readonly FavoriteReasonCode[] = [
  "missing_case_id",
  "already_favorited",
  "not_found",
  "storage_unavailable",
  "limit_reached",
];
