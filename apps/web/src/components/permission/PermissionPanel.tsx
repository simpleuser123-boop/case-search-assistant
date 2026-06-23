import { useEffect, useState } from "react";

import { isPermissionTieringEnabled } from "../../config/featureFlags";
import { getSession, subscribe } from "../../lib/sessionState";
import {
  grant,
  readObject,
  revoke,
  type GrantLevel,
  type SedimentObjectView,
} from "../../services/permissionApi";

// M5-4 权限分级面板（flag-gated）。
//
// 默认 false（isPermissionTieringEnabled=false）时直接渲染 null：不显示任何角色/授权
// 入口，不调用任何权限接口，页面与 M5-3 / M4 末态完全一致（owner 私有，无角色概念）。
//
// 红线：
//   - 只展示脱敏标识（owner_user_id_hash / team_id_hash）与短字段，不展示正文。
//   - 默认最小权限：未显式授权只有 owner 可见可改；越权读取后端返回 403，前端只提示被拒。
//   - 授权 / 撤销只对对象 owner 可用；权限不改变主排序 / 召回 / source selection。
//   - 未登录时只展示入口与登录提示，不调用权限接口、不执行授权动作。

export function PermissionPanel() {
  const enabled = isPermissionTieringEnabled();
  const [session, setSessionLocal] = useState(getSession());
  const [objectId, setObjectId] = useState("");
  const [granteeId, setGranteeId] = useState("");
  const [level, setLevel] = useState<GrantLevel>("viewer");
  const [status, setStatus] = useState<string | null>(null);
  const [view, setView] = useState<SedimentObjectView | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!enabled) {
      return;
    }
    return subscribe((next) => setSessionLocal(next));
  }, [enabled]);

  // 关闭态：不渲染任何权限 UI。回到 M5-3 / M4 末态的硬保证。
  if (!enabled) {
    return null;
  }
  if (!session) {
    return (
      <section
        aria-label="权限分级"
        className="rounded-[6px] border border-[var(--color-border)] bg-[var(--color-surface)] px-4 py-3 text-sm text-[var(--color-text)]"
      >
        <p className="font-medium">权限分级与对象级访问控制</p>
        <p className="mt-0.5 text-xs text-[var(--color-text-muted)]">
          默认最小权限：未显式授权只有所有者可见可改；越权访问被拒绝并记录审计。
        </p>
        <p className="mt-3 text-xs text-[var(--color-text-muted)]">
          请先登录账号，再查看对象权限、执行授权、撤销或受控读取。
        </p>
      </section>
    );
  }

  async function handleGrant() {
    if (busy || !objectId.trim() || !granteeId.trim()) return;
    setBusy(true);
    setStatus(null);
    try {
      const result = await grant(objectId.trim(), granteeId.trim(), level);
      if (result.ok) {
        setStatus("授权已生效。");
      } else if (result.reason === "denied") {
        setStatus("越权操作被拒绝（仅对象所有者可授权）。");
      } else if (result.reason === "disabled") {
        setStatus("权限分级未启用。");
      } else {
        setStatus("授权未通过。");
      }
    } finally {
      setBusy(false);
    }
  }

  async function handleRevoke() {
    if (busy || !objectId.trim() || !granteeId.trim()) return;
    setBusy(true);
    setStatus(null);
    try {
      const result = await revoke(objectId.trim(), granteeId.trim());
      setStatus(result.ok ? "授权已撤销。" : "撤销未通过。");
    } finally {
      setBusy(false);
    }
  }

  async function handleRead() {
    if (busy || !objectId.trim()) return;
    setBusy(true);
    setStatus(null);
    setView(null);
    try {
      const result = await readObject(objectId.trim());
      if (result.ok) {
        setView(result.data.object);
        setStatus(`读取成功（有效权限：${result.data.effective_level ?? "-"}）。`);
      } else if (result.reason === "denied") {
        setStatus("越权访问被拒绝（无权读取该对象）。");
      } else if (result.reason === "disabled") {
        setStatus("权限分级未启用。");
      } else {
        setStatus("读取未通过。");
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <section
      aria-label="权限分级"
      className="rounded-[6px] border border-[var(--color-border)] bg-[var(--color-surface)] px-4 py-3 text-sm text-[var(--color-text)]"
    >
      <p className="font-medium">权限分级与对象级访问控制</p>
      <p className="mt-0.5 text-xs text-[var(--color-text-muted)]">
        默认最小权限：未显式授权只有所有者可见可改；越权访问被拒绝并记录审计。
      </p>

      <div className="mt-3 flex flex-col gap-2">
        <input
          type="text"
          value={objectId}
          onChange={(e) => setObjectId(e.target.value)}
          maxLength={64}
          placeholder="对象 ID（收藏/清单/报告）"
          className="rounded-[4px] border border-[var(--color-border)] px-2 py-1.5 text-sm"
        />
        <div className="flex gap-2">
          <input
            type="text"
            value={granteeId}
            onChange={(e) => setGranteeId(e.target.value)}
            maxLength={64}
            placeholder="被授权用户 ID"
            className="flex-1 rounded-[4px] border border-[var(--color-border)] px-2 py-1.5 text-sm"
          />
          <select
            aria-label="权限等级"
            value={level}
            onChange={(e) => setLevel(e.target.value as GrantLevel)}
            className="rounded-[4px] border border-[var(--color-border)] px-2 py-1.5 text-sm"
          >
            <option value="viewer">查看（viewer）</option>
            <option value="editor">编辑（editor）</option>
          </select>
        </div>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={handleGrant}
            disabled={busy}
            className="rounded-[4px] bg-[var(--color-text)] px-3 py-1.5 text-xs text-[var(--color-bg)]"
          >
            授权
          </button>
          <button
            type="button"
            onClick={handleRevoke}
            disabled={busy}
            className="rounded-[4px] border border-[var(--color-border)] px-3 py-1.5 text-xs"
          >
            撤销
          </button>
          <button
            type="button"
            onClick={handleRead}
            disabled={busy}
            className="rounded-[4px] border border-[var(--color-border)] px-3 py-1.5 text-xs"
          >
            受控读取
          </button>
        </div>
      </div>

      {status ? <p className="mt-2 text-xs text-[var(--color-text-muted)]">{status}</p> : null}
      {view ? (
        <dl className="mt-2 grid grid-cols-2 gap-1 text-xs text-[var(--color-text-muted)]">
          <dt>对象类型</dt>
          <dd>{view.object_type}</dd>
          <dt>可见性</dt>
          <dd>{view.visibility}</dd>
          <dt>所有者(脱敏)</dt>
          <dd className="truncate">{view.owner_user_id_hash}</dd>
        </dl>
      ) : null}
    </section>
  );
}
