// M4-4 类案清单组装（F17）。
//
// 隐私边界（M4-1 合同 / 止损线）：
//   - 清单只保存**引用与用户自填短字段**：list_id / list_title（用户自填）、
//     每项的 case_id + 元数据（case_number / court / trial_level / case_cause /
//     judgment_date）+ source_anchors（仅 case_id + source_chunk_id + anchor_type，
//     无正文）+ 用户自填 note / tag，以及时间戳与 list_status。
//   - 绝不复制并存储案例正文、摘要正文、要旨正文、chunk 正文、裁判文书长正文，
//     也不存储原始 query 或任何自由长文本。
//   - 清单只存在于**浏览器本地**（localStorage），可增项、可删项、可手动排序、
//     可删除整张清单；绝不上送后端持久层、日志、开发报告或测试快照。
//   - 清单**不作为运行时排序 / 召回 / source selection 的输入**；用户手动排序
//     只影响清单展示，绝不回写主结果排序；不按 case id / qrels / label / relevance
//     特判。
//   - 日志只记录 count / status / reason_code，不记录正文、案号、note、tag、title。
//
// 本模块是纯函数 + 可注入 storage，便于在无真实浏览器下单测。所有读写都包了
// try/catch：本地存储异常（隐私模式 / 配额 / JSON 损坏）只做安全降级，绝不破坏
// 主检索 / 阅读链路。

// 清单项用的来源锚点：只保留可追溯所需的最小标识字段，绝不含正文。
// case_id + source_chunk_id 是 M4-1 合同要求的锚点最小字段。
export type ListSourceAnchor = {
  case_id: string;
  source_chunk_id: string;
  anchor_type?: string;
  chunk_type?: string | null;
};

// 单个清单项。全部为引用 / 元数据 / 锚点 / 用户自填短字段，零正文。
export type CaseListItem = {
  // case_id: 案例稳定标识，清单内去重与回跳详情用。
  case_id: string;
  // case_number / court / trial_level / case_cause / judgment_date: 案例元数据。
  case_number: string;
  court: string;
  trial_level: string;
  case_cause: string;
  judgment_date: string;
  // source_anchors: 可选，用于追溯案例侧 AI 内容；只含锚点标识字段，无正文。
  source_anchors: ListSourceAnchor[];
  // note / tag: 用户自填短字段（截断到上限，仅本地、可清除）。
  note: string;
  tag: string;
  // added_at: 加入清单时间戳（ISO 字符串）。
  added_at: string;
};

export type CaseListStatus = "active" | "archived";

// 一张类案清单。list_title 为用户自填短字段；items 为引用集合。
export type CaseListRecord = {
  list_id: string;
  list_title: string;
  items: CaseListItem[];
  created_at: string;
  updated_at: string;
  list_status: CaseListStatus;
};

// 注入式存储抽象。生产用 window.localStorage；测试可传内存实现。
export type StorageLike = {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
};

export const CASE_LIST_STORAGE_KEY = "case-search:m4:case-list:v1";

// 本地上限，防止无限增长。
export const MAX_LISTS = 50;
export const MAX_ITEMS_PER_LIST = 200;
// 用户自填字段最大可视字符数（短字段约束）。
export const LIST_TITLE_MAX_CHARS = 40;
export const LIST_NOTE_MAX_CHARS = 120;
export const LIST_TAG_MAX_CHARS = 24;
// 每个清单项保留的来源锚点上限。
export const LIST_ANCHOR_MAX = 4;

export type CaseListReasonCode =
  | "missing_case_id"
  | "missing_list_id"
  | "already_in_list"
  | "list_not_found"
  | "item_not_found"
  | "storage_unavailable"
  | "list_limit_reached"
  | "item_limit_reached"
  | "invalid_order";

function nowIso(): string {
  return new Date().toISOString();
}

function clean(value: string | null | undefined): string {
  return (value || "").trim();
}

// 生成清单 id：优先用 crypto.randomUUID，回退到时间戳 + 随机串。仅本地标识，无语义。
function generateListId(): string {
  try {
    if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
      return `list_${crypto.randomUUID()}`;
    }
  } catch {
    // 忽略，走回退。
  }
  return `list_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`;
}

// 把用户自填短字段折叠空白并截断到上限，避免长正文混入清单存储。
export function truncateShortField(value: string | null | undefined, maxChars: number): string {
  const collapsed = (value || "").replace(/\s+/g, " ").trim();
  const chars = Array.from(collapsed);
  if (chars.length <= maxChars) {
    return collapsed;
  }
  return chars.slice(0, maxChars).join("");
}

// 结构化的元数据来源视图。SearchResultItem / CaseDetailResponse / 收藏记录都满足它，
// 故同一构造器可服务结果卡片、收藏列表、对比视图。只读元数据字段，绝不读正文。
export type ListItemMetadataSource = {
  case_id?: string | null;
  case_no?: string | null;
  case_number?: string | null;
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
// 仅取归属本案的锚点，避免跨案锚点污染追溯。最多保留 LIST_ANCHOR_MAX 条。
export function sanitizeListAnchors(
  source: ListItemMetadataSource | null | undefined,
  caseId: string
): ListSourceAnchor[] {
  const anchors = source?.source_anchors || [];
  const result: ListSourceAnchor[] = [];
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
    if (result.length >= LIST_ANCHOR_MAX) {
      break;
    }
  }
  return result;
}

// 从任意案例元数据来源构造一个清单项（元数据 + 锚点 + 用户自填短字段）。
// trial_level 缺失时回退 court_level；case_number 缺失时回退 case_no。绝不读正文。
export function buildListItem(
  source: ListItemMetadataSource | null | undefined,
  userFields?: { note?: string; tag?: string }
): CaseListItem {
  const caseId = clean(source?.case_id);
  return {
    case_id: caseId,
    case_number: clean(source?.case_number) || clean(source?.case_no),
    court: clean(source?.court),
    trial_level: clean(source?.trial_level) || clean(source?.court_level),
    case_cause: clean(source?.case_cause),
    judgment_date: clean(source?.judgment_date),
    source_anchors: sanitizeListAnchors(source, caseId),
    note: truncateShortField(userFields?.note, LIST_NOTE_MAX_CHARS),
    tag: truncateShortField(userFields?.tag, LIST_TAG_MAX_CHARS),
    added_at: nowIso(),
  };
}

// ---------- 反序列化清洗（只接受白名单字段，丢弃潜在正文）----------

function sanitizeAnchorList(value: unknown): ListSourceAnchor[] {
  if (!Array.isArray(value)) {
    return [];
  }
  const result: ListSourceAnchor[] = [];
  for (const raw of value) {
    if (!raw || typeof raw !== "object") {
      continue;
    }
    const anchor = raw as Partial<ListSourceAnchor>;
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
    if (result.length >= LIST_ANCHOR_MAX) {
      break;
    }
  }
  return result;
}

// 清洗单个清单项：缺 case_id 视为无效；只重建白名单键，主动丢弃任何从旧版本 /
// 篡改数据混入的非白名单键（含潜在正文字段）。
function sanitizeItem(value: unknown): CaseListItem | null {
  if (!value || typeof value !== "object") {
    return null;
  }
  const item = value as Partial<CaseListItem>;
  const caseId = clean(item.case_id);
  if (!caseId) {
    return null;
  }
  return {
    case_id: caseId,
    case_number: clean(item.case_number),
    court: clean(item.court),
    trial_level: clean(item.trial_level),
    case_cause: clean(item.case_cause),
    judgment_date: clean(item.judgment_date),
    source_anchors: sanitizeAnchorList(item.source_anchors),
    note: truncateShortField(item.note, LIST_NOTE_MAX_CHARS),
    tag: truncateShortField(item.tag, LIST_TAG_MAX_CHARS),
    added_at: typeof item.added_at === "string" ? item.added_at : nowIso(),
  };
}

// 清洗单张清单：缺 list_id 视为无效；items 去重（同 case_id 保留先出现者）、
// 截断到上限；只接受白名单键，丢弃潜在正文。
function sanitizeList(value: unknown): CaseListRecord | null {
  if (!value || typeof value !== "object") {
    return null;
  }
  const record = value as Partial<CaseListRecord>;
  const listId = clean(record.list_id);
  if (!listId) {
    return null;
  }
  const rawItems = Array.isArray(record.items) ? record.items : [];
  const items: CaseListItem[] = [];
  const seen = new Set<string>();
  for (const raw of rawItems) {
    const item = sanitizeItem(raw);
    if (!item || seen.has(item.case_id)) {
      continue;
    }
    seen.add(item.case_id);
    items.push(item);
    if (items.length >= MAX_ITEMS_PER_LIST) {
      break;
    }
  }
  const createdAt = typeof record.created_at === "string" ? record.created_at : nowIso();
  return {
    list_id: listId,
    list_title: truncateShortField(record.list_title, LIST_TITLE_MAX_CHARS),
    items,
    created_at: createdAt,
    updated_at: typeof record.updated_at === "string" ? record.updated_at : createdAt,
    list_status: record.list_status === "archived" ? "archived" : "active",
  };
}

// ---------- 读写（均仅本地、可清除）----------

// 读取本地全部清单，按 updated_at 倒序（最近更新在前）。解析失败返回空数组。
export function loadLists(storage: StorageLike): CaseListRecord[] {
  try {
    const raw = storage.getItem(CASE_LIST_STORAGE_KEY);
    if (!raw) {
      return [];
    }
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) {
      return [];
    }
    const sanitized = parsed
      .map(sanitizeList)
      .filter((entry): entry is CaseListRecord => entry !== null);
    // list_id 去重（保留先出现者），按更新时间倒序、截断到上限。
    const deduped: CaseListRecord[] = [];
    const seen = new Set<string>();
    for (const entry of sanitized) {
      if (seen.has(entry.list_id)) {
        continue;
      }
      seen.add(entry.list_id);
      deduped.push(entry);
    }
    return sortListsNewestFirst(deduped).slice(0, MAX_LISTS);
  } catch {
    return [];
  }
}

function sortListsNewestFirst(lists: CaseListRecord[]): CaseListRecord[] {
  return [...lists].sort((a, b) => b.updated_at.localeCompare(a.updated_at));
}

function persist(storage: StorageLike, lists: CaseListRecord[]): CaseListRecord[] {
  const capped = sortListsNewestFirst(lists).slice(0, MAX_LISTS);
  try {
    storage.setItem(CASE_LIST_STORAGE_KEY, JSON.stringify(capped));
  } catch {
    // 写入失败仍返回内存态，调用方据此更新 UI；下次写入再尝试落盘。
  }
  return capped;
}

// ---------- 清单 CRUD ----------

export type CreateListResult = {
  lists: CaseListRecord[];
  list?: CaseListRecord;
  created: boolean;
  reason?: CaseListReasonCode;
};

// 新建一张清单。可选带一个初始项（从结果/收藏/对比直接「新建清单并加入」）。
export function createList(
  storage: StorageLike,
  existing: CaseListRecord[],
  title?: string,
  initialItemSource?: ListItemMetadataSource | null
): CreateListResult {
  if (existing.length >= MAX_LISTS) {
    return { lists: existing, created: false, reason: "list_limit_reached" };
  }
  const ts = nowIso();
  const items: CaseListItem[] = [];
  if (initialItemSource) {
    const item = buildListItem(initialItemSource);
    if (item.case_id) {
      items.push(item);
    }
  }
  const record: CaseListRecord = {
    list_id: generateListId(),
    list_title: truncateShortField(title, LIST_TITLE_MAX_CHARS),
    items,
    created_at: ts,
    updated_at: ts,
    list_status: "active",
  };
  return { lists: persist(storage, [record, ...existing]), list: record, created: true };
}

function touch(list: CaseListRecord, items: CaseListItem[]): CaseListRecord {
  return { ...list, items, updated_at: nowIso() };
}

function replaceList(lists: CaseListRecord[], next: CaseListRecord): CaseListRecord[] {
  return lists.map((entry) => (entry.list_id === next.list_id ? next : entry));
}

export type ItemMutationResult = {
  lists: CaseListRecord[];
  changed: boolean;
  reason?: CaseListReasonCode;
};

// 向指定清单加入一个案例。同一案例在同一清单内去重（已存在则幂等，changed=false）。
// 超过单清单上限拒绝写入。绝不写入正文。
export function addItemToList(
  storage: StorageLike,
  existing: CaseListRecord[],
  listId: string,
  source: ListItemMetadataSource | null | undefined
): ItemMutationResult {
  const id = clean(listId);
  if (!id) {
    return { lists: existing, changed: false, reason: "missing_list_id" };
  }
  const target = existing.find((entry) => entry.list_id === id);
  if (!target) {
    return { lists: existing, changed: false, reason: "list_not_found" };
  }
  const item = buildListItem(source);
  if (!item.case_id) {
    return { lists: existing, changed: false, reason: "missing_case_id" };
  }
  if (target.items.some((entry) => entry.case_id === item.case_id)) {
    return { lists: existing, changed: false, reason: "already_in_list" };
  }
  if (target.items.length >= MAX_ITEMS_PER_LIST) {
    return { lists: existing, changed: false, reason: "item_limit_reached" };
  }
  const next = touch(target, [...target.items, item]);
  return { lists: persist(storage, replaceList(existing, next)), changed: true };
}

// 从指定清单删除一个案例项。
export function removeItemFromList(
  storage: StorageLike,
  existing: CaseListRecord[],
  listId: string,
  caseId: string
): ItemMutationResult {
  const id = clean(listId);
  const cid = clean(caseId);
  const target = existing.find((entry) => entry.list_id === id);
  if (!target) {
    return { lists: existing, changed: false, reason: "list_not_found" };
  }
  if (!target.items.some((entry) => entry.case_id === cid)) {
    return { lists: existing, changed: false, reason: "item_not_found" };
  }
  const next = touch(
    target,
    target.items.filter((entry) => entry.case_id !== cid)
  );
  return { lists: persist(storage, replaceList(existing, next)), changed: true };
}

// 用户手动排序：把整张清单的 items 重排为给定的 case_id 顺序。
// 仅影响清单展示，绝不回写主结果排序 / 召回 / source selection。
// 给定顺序必须是当前 items 的一个排列（同集合、同长度），否则拒绝（invalid_order）。
export function reorderListItems(
  storage: StorageLike,
  existing: CaseListRecord[],
  listId: string,
  orderedCaseIds: string[]
): ItemMutationResult {
  const id = clean(listId);
  const target = existing.find((entry) => entry.list_id === id);
  if (!target) {
    return { lists: existing, changed: false, reason: "list_not_found" };
  }
  const current = target.items;
  const requested = orderedCaseIds.map(clean).filter(Boolean);
  if (requested.length !== current.length) {
    return { lists: existing, changed: false, reason: "invalid_order" };
  }
  const byId = new Map(current.map((entry) => [entry.case_id, entry]));
  const reordered: CaseListItem[] = [];
  const used = new Set<string>();
  for (const cid of requested) {
    const item = byId.get(cid);
    if (!item || used.has(cid)) {
      return { lists: existing, changed: false, reason: "invalid_order" };
    }
    used.add(cid);
    reordered.push(item);
  }
  const next = touch(target, reordered);
  return { lists: persist(storage, replaceList(existing, next)), changed: true };
}

// 把某项在清单内上移 / 下移一位（手动排序的便捷入口，仅影响展示）。
export function moveListItem(
  storage: StorageLike,
  existing: CaseListRecord[],
  listId: string,
  caseId: string,
  direction: "up" | "down"
): ItemMutationResult {
  const id = clean(listId);
  const cid = clean(caseId);
  const target = existing.find((entry) => entry.list_id === id);
  if (!target) {
    return { lists: existing, changed: false, reason: "list_not_found" };
  }
  const index = target.items.findIndex((entry) => entry.case_id === cid);
  if (index < 0) {
    return { lists: existing, changed: false, reason: "item_not_found" };
  }
  const swapWith = direction === "up" ? index - 1 : index + 1;
  if (swapWith < 0 || swapWith >= target.items.length) {
    return { lists: existing, changed: false };
  }
  const items = [...target.items];
  [items[index], items[swapWith]] = [items[swapWith], items[index]];
  const next = touch(target, items);
  return { lists: persist(storage, replaceList(existing, next)), changed: true };
}

// 更新某清单项的用户自填短字段（note / tag）。
export function updateListItemFields(
  storage: StorageLike,
  existing: CaseListRecord[],
  listId: string,
  caseId: string,
  userFields: { note?: string; tag?: string }
): ItemMutationResult {
  const id = clean(listId);
  const cid = clean(caseId);
  const target = existing.find((entry) => entry.list_id === id);
  if (!target) {
    return { lists: existing, changed: false, reason: "list_not_found" };
  }
  let changed = false;
  const items = target.items.map((entry) => {
    if (entry.case_id !== cid) {
      return entry;
    }
    changed = true;
    return {
      ...entry,
      note:
        userFields.note !== undefined
          ? truncateShortField(userFields.note, LIST_NOTE_MAX_CHARS)
          : entry.note,
      tag:
        userFields.tag !== undefined
          ? truncateShortField(userFields.tag, LIST_TAG_MAX_CHARS)
          : entry.tag,
    };
  });
  if (!changed) {
    return { lists: existing, changed: false, reason: "item_not_found" };
  }
  const next = touch(target, items);
  return { lists: persist(storage, replaceList(existing, next)), changed: true };
}

// 重命名清单（用户自填短字段）。
export function renameList(
  storage: StorageLike,
  existing: CaseListRecord[],
  listId: string,
  title: string
): ItemMutationResult {
  const id = clean(listId);
  const target = existing.find((entry) => entry.list_id === id);
  if (!target) {
    return { lists: existing, changed: false, reason: "list_not_found" };
  }
  const next: CaseListRecord = {
    ...target,
    list_title: truncateShortField(title, LIST_TITLE_MAX_CHARS),
    updated_at: nowIso(),
  };
  return { lists: persist(storage, replaceList(existing, next)), changed: true };
}

// 删除整张清单。
export function deleteList(
  storage: StorageLike,
  existing: CaseListRecord[],
  listId: string
): CaseListRecord[] {
  const id = clean(listId);
  if (!id) {
    return existing;
  }
  return persist(
    storage,
    existing.filter((entry) => entry.list_id !== id)
  );
}

// 清空全部清单。
export function clearLists(storage: StorageLike): void {
  try {
    storage.removeItem(CASE_LIST_STORAGE_KEY);
  } catch {
    // 清除失败不应阻断主链路。
  }
}

// 只读：某案例当前在哪些清单内（用于结果卡片展示「已在 N 个清单」状态）。
// 只返回引用关系，绝不回写排序特征——不参与主排序 / 召回 / source selection。
export function listIdsContainingCase(
  lists: CaseListRecord[],
  caseId: string
): string[] {
  const id = clean(caseId);
  if (!id) {
    return [];
  }
  return lists
    .filter((entry) => entry.items.some((item) => item.case_id === id))
    .map((entry) => entry.list_id);
}

// ---------- 脱敏日志 ----------
// 只记录 event / surface / status / reason_code / count，绝不含正文、案号、note、
// tag、title、query 或任何用户输入。
export type CaseListLogSurface =
  | "result_card"
  | "detail"
  | "compare"
  | "favorite_list"
  | "list_panel";
export type CaseListLogStatus =
  | "list_created"
  | "item_added"
  | "item_removed"
  | "reordered"
  | "fields_updated"
  | "list_renamed"
  | "list_deleted"
  | "cleared"
  | "noop";

export type CaseListActionLog = {
  event: "case_list_action";
  surface: CaseListLogSurface;
  status: CaseListLogStatus;
  reason_code: CaseListReasonCode | null;
  count: number;
};

export function buildCaseListLog({
  surface,
  status,
  reason,
  count = 1,
}: {
  surface: CaseListLogSurface;
  status: CaseListLogStatus;
  reason?: CaseListReasonCode | null;
  count?: number;
}): CaseListActionLog {
  return {
    event: "case_list_action",
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

export const CASE_LIST_REASON_CODES: readonly CaseListReasonCode[] = [
  "missing_case_id",
  "missing_list_id",
  "already_in_list",
  "list_not_found",
  "item_not_found",
  "storage_unavailable",
  "list_limit_reached",
  "item_limit_reached",
  "invalid_order",
];

