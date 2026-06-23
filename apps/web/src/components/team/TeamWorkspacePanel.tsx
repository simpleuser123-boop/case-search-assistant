import { useEffect, useState } from "react";

import { isTeamWorkspaceEnabled } from "../../config/featureFlags";
import { getSession, subscribe, type SessionState } from "../../lib/sessionState";
import { createTeam, listTeams, type TeamView } from "../../services/teamApi";

// M5-3 团队空间面板（flag-gated）。
//
// 默认 false（isTeamWorkspaceEnabled=false）时直接渲染 null：不显示任何团队切换/
// 成员入口，不调用任何团队接口，页面与 M5-2 / M4 单用户私有末态完全一致。
//
// 隔离 / 隐私红线：
//   - 只展示团队的脱敏标识（team_id_hash）与用户自填短名（team_name），不展示正文。
//   - 沉淀对象按 team_id 强隔离由后端保证；前端切换团队只切换查询上下文，
//     不改变主排序 / 召回 / source selection。
//   - team_id 为空（未选团队）时等同单用户私有态。
//   - 未登录时只展示入口与登录提示，不调用团队接口、不创建团队；登录后才显示团队操作。

export function TeamWorkspacePanel() {
  const enabled = isTeamWorkspaceEnabled();
  const [session, setSessionLocal] = useState<SessionState | null>(getSession());
  const [teams, setTeams] = useState<TeamView[]>([]);
  const [activeTeamId, setActiveTeamId] = useState<string | null>(null);
  const [newTeamName, setNewTeamName] = useState("");
  const [status, setStatus] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!enabled) {
      return;
    }
    return subscribe((next) => setSessionLocal(next));
  }, [enabled]);

  useEffect(() => {
    if (!enabled || !session) {
      return;
    }
    let cancelled = false;
    void (async () => {
      const result = await listTeams();
      if (cancelled) return;
      if (result.ok) {
        setTeams(result.data.teams ?? []);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [enabled, session]);

  // 关闭态：不渲染任何团队 UI。回到 M5-2 / M4 末态的硬保证。
  if (!enabled) {
    return null;
  }

  if (!session) {
    return (
      <section
        aria-label="团队空间"
        className="rounded-[6px] border border-[var(--color-border)] bg-[var(--color-surface)] px-4 py-3 text-sm text-[var(--color-text)]"
      >
        <p className="font-medium">团队空间</p>
        <p className="mt-0.5 text-xs text-[var(--color-text-muted)]">
          沉淀按团队强隔离，跨团队默认不可见；未选团队时等同单用户私有。
        </p>
        <p className="mt-3 text-xs text-[var(--color-text-muted)]">
          请先登录账号，再查看团队列表、创建团队或切换团队空间。
        </p>
      </section>
    );
  }

  async function handleCreate(event: React.FormEvent) {
    event.preventDefault();
    if (busy || !newTeamName.trim()) return;
    setBusy(true);
    setStatus(null);
    try {
      const result = await createTeam(newTeamName.trim());
      if (!result.ok) {
        setStatus(result.reason === "disabled" ? "团队空间未启用。" : "创建未通过。");
        return;
      }
      setNewTeamName("");
      const refreshed = await listTeams();
      if (refreshed.ok) setTeams(refreshed.data.teams ?? []);
      setStatus("团队已创建。");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section
      aria-label="团队空间"
      className="rounded-[6px] border border-[var(--color-border)] bg-[var(--color-surface)] px-4 py-3 text-sm text-[var(--color-text)]"
    >
      <p className="font-medium">团队空间</p>
      <p className="mt-0.5 text-xs text-[var(--color-text-muted)]">
        沉淀按团队强隔离，跨团队默认不可见；未选团队时等同单用户私有。
      </p>

      <div className="mt-3 flex flex-col gap-1.5">
        <button
          type="button"
          onClick={() => setActiveTeamId(null)}
          className={
            activeTeamId === null
              ? "rounded-[4px] border border-[var(--color-border)] bg-[var(--color-bg)] px-3 py-1.5 text-left text-xs font-semibold"
              : "rounded-[4px] border border-[var(--color-border)] px-3 py-1.5 text-left text-xs"
          }
        >
          个人私有（无团队）
        </button>
        {teams.map((team) => (
          <button
            key={team.team_id}
            type="button"
            onClick={() => setActiveTeamId(team.team_id)}
            className={
              activeTeamId === team.team_id
                ? "rounded-[4px] border border-[var(--color-border)] bg-[var(--color-bg)] px-3 py-1.5 text-left text-xs font-semibold"
                : "rounded-[4px] border border-[var(--color-border)] px-3 py-1.5 text-left text-xs"
            }
          >
            {team.team_name || team.team_id_hash}
          </button>
        ))}
      </div>

      <form onSubmit={handleCreate} className="mt-3 flex gap-2">
        <input
          type="text"
          value={newTeamName}
          onChange={(e) => setNewTeamName(e.target.value)}
          maxLength={60}
          placeholder="新团队名称"
          className="flex-1 rounded-[4px] border border-[var(--color-border)] px-2 py-1.5 text-sm"
        />
        <button
          type="submit"
          disabled={busy}
          className="rounded-[4px] bg-[var(--color-text)] px-3 py-1.5 text-xs text-[var(--color-bg)]"
        >
          创建
        </button>
      </form>
      {status ? <p className="mt-2 text-xs text-[var(--color-text-muted)]">{status}</p> : null}
    </section>
  );
}
