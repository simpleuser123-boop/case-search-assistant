import { useEffect, useState } from "react";

import { isBulkImportEnabled } from "../../config/featureFlags";
import { getSession, subscribe } from "../../lib/sessionState";
import { runBulkImport, type BulkImportResult } from "../../services/bulkImportApi";

// M5-6 批量导入面板（flag-gated）。
//
// 默认 false（isBulkImportEnabled=false）时直接渲染 null：不显示任何导入入口，
// 不调用任何导入接口，页面回到 M5-5 / M4 末态。
//
// 红线：
//   - 导入只上送元数据 / 引用 / 来源锚点 / 用户自填短字段，绝不上送正文或原始案情。
//   - 缺锚点 / 含正文 / 缺 case_id 的项被后端降级或拒绝（reason_code 标注），不伪造锚点。
//   - 导入对象默认归属当前 owner、默认私有；导入不改变主排序 / 召回 / source selection。
//   - 未登录时只展示入口与登录提示，不调用导入接口、不执行导入动作。
//
// 输入说明：粘贴每行一条「case_id, 案号, 法院, source_chunk_id」的 CSV 风格清单。
// 解析只取这些白名单字段；不接受任何正文列。source_chunk_id 用于和 case_id 组成来源锚点。

type ParsedItem = {
  caseId: string;
  caseNumber?: string;
  court?: string;
  sourceAnchors?: Array<{ case_id: string; source_chunk_id: string }>;
};

// 把粘贴文本解析成白名单导入项。绝不读取/保留正文列：超出已知列的字段一律忽略。
function parseItems(text: string): ParsedItem[] {
  const items: ParsedItem[] = [];
  for (const rawLine of text.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line) continue;
    const cols = line.split(",").map((c) => c.trim());
    const caseId = cols[0];
    if (!caseId) continue;
    const item: ParsedItem = { caseId: caseId.slice(0, 120) };
    if (cols[1]) item.caseNumber = cols[1].slice(0, 120);
    if (cols[2]) item.court = cols[2].slice(0, 120);
    const chunkId = cols[3];
    if (chunkId) {
      item.sourceAnchors = [{ case_id: caseId.slice(0, 120), source_chunk_id: chunkId.slice(0, 120) }];
    }
    items.push(item);
  }
  return items;
}

function describeReason(reasonCode?: string): string {
  switch (reasonCode) {
    case "missing_source_anchor":
      return "部分项缺少来源锚点，AI 内容无法溯源，已被拒绝（未伪造锚点）。";
    case "invalid_source_anchor":
      return "部分项来源锚点不完整（需 case_id + source_chunk_id），已被拒绝。";
    case "forbidden_body_field":
      return "检测到正文 / 凭据字段，已拒绝（导入只接受元数据与引用）。";
    case "duplicate_case_id":
      return "部分项与已有记录重复（按 case_id 去重），已跳过。";
    case "missing_case_id":
      return "部分项缺少 case_id，已被拒绝。";
    case "empty_batch":
      return "没有可导入的有效项。";
    case "batch_too_large":
      return "单批导入项过多，请拆分后重试。";
    default:
      return "导入未完全成功，请查看结果明细。";
  }
}

export function BulkImportPanel() {
  const enabled = isBulkImportEnabled();
  const [session, setSessionLocal] = useState(getSession());
  const [text, setText] = useState("");
  const [objectType, setObjectType] = useState<"case_favorite" | "case_list" | "report_template">("case_favorite");
  const [result, setResult] = useState<BulkImportResult | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!enabled) {
      return;
    }
    return subscribe((next) => setSessionLocal(next));
  }, [enabled]);

  // 关闭态：不渲染任何导入 UI。回到 M5-5 / M4 末态的硬保证。
  if (!enabled) {
    return null;
  }
  if (!session) {
    return (
      <section
        aria-label="批量导入"
        className="rounded-[6px] border border-[var(--color-border)] bg-[var(--color-surface)] px-4 py-3 text-sm text-[var(--color-text)]"
      >
        <p className="font-medium">批量导入</p>
        <p className="mt-0.5 text-xs text-[var(--color-text-muted)]">
          把既有清单批量导入到你的私有空间。每行格式：case_id, 案号, 法院, source_chunk_id。
          只导入元数据与引用，绝不导入正文；缺来源锚点的清单/报告项会被拒绝。
        </p>
        <p className="mt-3 text-xs text-[var(--color-text-muted)]">
          请先登录账号，再执行批量导入；未登录时不会解析或上送任何清单内容。
        </p>
      </section>
    );
  }

  async function handleImport() {
    if (busy) return;
    const items = parseItems(text);
    if (items.length === 0) {
      setStatus("没有可导入的有效项（每行至少需要 case_id）。");
      setResult(null);
      return;
    }
    setBusy(true);
    setStatus(null);
    setResult(null);
    try {
      const res = await runBulkImport({ sourceType: "case_list_file", objectType, items });
      if (res.ok) {
        setResult(res.data);
        if (res.data.degrade_reason) {
          setStatus(describeReason(res.data.degrade_reason));
        } else {
          setStatus(null);
        }
      } else if (res.reason === "disabled") {
        setStatus("批量导入未启用。");
      } else {
        setStatus(describeReason(res.reasonCode));
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <section
      aria-label="批量导入"
      className="rounded-[6px] border border-[var(--color-border)] bg-[var(--color-surface)] px-4 py-3 text-sm text-[var(--color-text)]"
    >
      <p className="font-medium">批量导入</p>
      <p className="mt-0.5 text-xs text-[var(--color-text-muted)]">
        把既有清单批量导入到你的私有空间。每行格式：case_id, 案号, 法院, source_chunk_id。
        只导入元数据与引用，绝不导入正文；缺来源锚点的清单/报告项会被拒绝。
      </p>

      <div className="mt-3 flex flex-col gap-2">
        <label className="text-xs text-[var(--color-text-muted)]">
          导入类型
          <select
            value={objectType}
            onChange={(e) => setObjectType(e.target.value as typeof objectType)}
            className="ml-2 rounded-[4px] border border-[var(--color-border)] px-2 py-1 text-sm"
          >
            <option value="case_favorite">案例收藏</option>
            <option value="case_list">类案清单</option>
            <option value="report_template">报告模板</option>
          </select>
        </label>
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          rows={5}
          placeholder={"c_001, (2021)京01民终123号, 北京一中院, chunk_7\nc_002, (2021)沪01民终456号, 上海一中院, chunk_3"}
          className="rounded-[4px] border border-[var(--color-border)] px-2 py-1.5 font-mono text-xs"
        />
        <button
          type="button"
          onClick={handleImport}
          disabled={busy}
          className="self-start rounded-[4px] bg-[var(--color-text)] px-3 py-1.5 text-xs text-[var(--color-bg)]"
        >
          {busy ? "导入中…" : "开始导入"}
        </button>
      </div>

      {status ? <p className="mt-2 text-xs text-[var(--color-text-muted)]">{status}</p> : null}

      {result ? (
        <div className="mt-3 rounded-[4px] border border-[var(--color-border)] px-3 py-2 text-xs">
          <p>
            状态：{result.import_status}；共 {result.item_count} 项，成功 {result.imported_count}，
            拒绝/降级 {result.rejected_count}，去重跳过 {result.duplicate_count}。
          </p>
          <ul className="mt-1 list-disc pl-4">
            {result.outcomes.map((o, i) => (
              <li key={`${o.case_id ?? "?"}-${i}`}>
                {o.case_id ?? "(无 case_id)"}：{o.ok ? "已导入" : `跳过（${o.reason_code}）`}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </section>
  );
}
