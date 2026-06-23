import { useEffect, useState } from "react";

import { isTeamSharingEnabled } from "../../config/featureFlags";
import { getSession, subscribe } from "../../lib/sessionState";
import { shareToTeam, unshare } from "../../services/sharingApi";

// M5-5 沉淀同步与团队共享面板（flag-gated）。
//
// 默认 false（isTeamSharingEnabled=false）时直接渲染 null：不显示任何同步/共享入口，
// 不调用任何同步/共享接口，页面回到 M4 本地沉淀末态（单用户、纯前端、不上送服务端）。
//
// 红线：
//   - 共享是显式动作，默认私有；只有对象 owner + 目标团队活跃成员可共享。
//   - 无来源锚点的 AI 内容承载型对象会被后端拒绝（reason_code=missing_source_anchor）。
//   - 只展示脱敏标识与短字段，不展示正文；同步/共享不改变主排序 / 召回 / source selection。
//   - 未登录时只展示入口与登录提示，不调用共享接口、不执行共享动作。

export function SharingPanel() {
  const enabled = isTeamSharingEnabled();
  const [session, setSessionLocal] = useState(getSession());
  const [objectId, setObjectId] = useState("");
  const [teamId, setTeamId] = useState("");
  const [status, setStatus] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!enabled) {
      return;
    }
    return subscribe((next) => setSessionLocal(next));
  }, [enabled]);

  // 关闭态：不渲染任何共享 UI。回到 M4 本地沉淀末态的硬保证。
  if (!enabled) {
    return null;
  }
  if (!session) {
    return (
      <section
        aria-label="沉淀同步与团队共享"
        className="rounded-[6px] border border-[var(--color-border)] bg-[var(--color-surface)] px-4 py-3 text-sm text-[var(--color-text)]"
      >
        <p className="font-medium">沉淀同步与团队共享</p>
        <p className="mt-0.5 text-xs text-[var(--color-text-muted)]">
          默认私有：同步只上送元数据 / 引用 / 来源锚点；共享需显式动作，无来源锚点的内容不可共享。
        </p>
        <p className="mt-3 text-xs text-[var(--color-text-muted)]">
          请先登录账号，再同步沉淀对象或共享到团队。
        </p>
      </section>
    );
  }

  function describeReason(reasonCode?: string): string {
    switch (reasonCode) {
      case "missing_source_anchor":
        return "该对象缺少来源锚点，AI 内容无法溯源，已拒绝共享。";
      case "invalid_source_anchor":
        return "来源锚点不完整（需 case_id + source_chunk_id），已拒绝共享。";
      case "not_owner":
        return "仅对象所有者可共享。";
      case "not_a_member":
        return "你不是该团队的活跃成员，无法共享至该团队。";
      case "object_not_found":
        return "未找到该对象，请先同步到服务端。";
      case "team_not_found":
        return "未找到该团队。";
      default:
        return "共享未通过。";
    }
  }

  async function handleShare() {
    if (busy || !objectId.trim() || !teamId.trim()) return;
    setBusy(true);
    setStatus(null);
    try {
      const result = await shareToTeam(objectId.trim(), teamId.trim());
      if (result.ok) {
        setStatus(`已共享给团队（可见性：${result.data.visibility}，溯源锚点 ${result.data.anchor_count} 条）。`);
      } else if (result.reason === "disabled") {
        setStatus("团队共享未启用。");
      } else {
        setStatus(describeReason(result.reasonCode));
      }
    } finally {
      setBusy(false);
    }
  }

  async function handleUnshare() {
    if (busy || !objectId.trim()) return;
    setBusy(true);
    setStatus(null);
    try {
      const result = await unshare(objectId.trim());
      if (result.ok) {
        setStatus("已取消共享，对象已降回所有者私有。");
      } else if (result.reason === "disabled") {
        setStatus("团队共享未启用。");
      } else {
        setStatus(describeReason(result.reasonCode));
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <section
      aria-label="沉淀同步与团队共享"
      className="rounded-[6px] border border-[var(--color-border)] bg-[var(--color-surface)] px-4 py-3 text-sm text-[var(--color-text)]"
    >
      <p className="font-medium">沉淀同步与团队共享</p>
      <p className="mt-0.5 text-xs text-[var(--color-text-muted)]">
        默认私有：同步只上送元数据 / 引用 / 来源锚点；共享需显式动作，无来源锚点的内容不可共享。
      </p>

      <div className="mt-3 flex flex-col gap-2">
        <input
          type="text"
          value={objectId}
          onChange={(e) => setObjectId(e.target.value)}
          maxLength={64}
          placeholder="对象 ID（已同步的收藏/清单/报告）"
          className="rounded-[4px] border border-[var(--color-border)] px-2 py-1.5 text-sm"
        />
        <input
          type="text"
          value={teamId}
          onChange={(e) => setTeamId(e.target.value)}
          maxLength={64}
          placeholder="共享目标团队 ID"
          className="rounded-[4px] border border-[var(--color-border)] px-2 py-1.5 text-sm"
        />
        <div className="flex gap-2">
          <button
            type="button"
            onClick={handleShare}
            disabled={busy}
            className="rounded-[4px] bg-[var(--color-text)] px-3 py-1.5 text-xs text-[var(--color-bg)]"
          >
            共享给团队
          </button>
          <button
            type="button"
            onClick={handleUnshare}
            disabled={busy}
            className="rounded-[4px] border border-[var(--color-border)] px-3 py-1.5 text-xs"
          >
            取消共享
          </button>
        </div>
      </div>

      {status ? <p className="mt-2 text-xs text-[var(--color-text-muted)]">{status}</p> : null}
    </section>
  );
}
