import { useEffect, useState } from "react";

import {
  isAccountSystemEnabled,
  isBillingEnabled,
  isBulkImportEnabled,
  isPermissionTieringEnabled,
  isTeamSharingEnabled,
  isTeamWorkspaceEnabled,
  isTendencyAnalysisEnabled,
} from "../../config/featureFlags";
import { getSession, subscribe, type SessionState } from "../../lib/sessionState";
import { AccountPanel } from "../account/AccountPanel";
import { BillingPanel } from "../billing/BillingPanel";
import { BulkImportPanel } from "../bulkImport/BulkImportPanel";
import { PermissionPanel } from "../permission/PermissionPanel";
import { SharingPanel } from "../sharing/SharingPanel";
import { TeamWorkspacePanel } from "../team/TeamWorkspacePanel";
import { TendencyAnalysisPanel } from "../tendency/TendencyAnalysisPanel";

export function M5WorkspacePanel() {
  const [session, setSessionLocal] = useState<SessionState | null>(getSession());
  const hasVisibleCapability =
    isAccountSystemEnabled() ||
    isTeamWorkspaceEnabled() ||
    isPermissionTieringEnabled() ||
    isTeamSharingEnabled() ||
    isBulkImportEnabled() ||
    isTendencyAnalysisEnabled() ||
    isBillingEnabled();

  useEffect(() => subscribe((next) => setSessionLocal(next)), []);

  if (!hasVisibleCapability) {
    return null;
  }

  return (
    <section className="space-y-3" aria-label="M5 商业化工作台">
      <div className="rounded-[8px] border border-[var(--color-border)] bg-[var(--color-surface)] p-4 text-sm leading-6">
        <h2 className="font-semibold text-[var(--color-text)]">M5 商业化工作台</h2>
        <p className="mt-1 text-xs leading-5 text-[var(--color-text-muted)]">
          账号、团队、权限、共享、导入、倾向分析和计费能力均受 feature flag 控制；关闭时不渲染入口。
        </p>
      </div>
      <AccountPanel />
      <TeamWorkspacePanel />
      <PermissionPanel />
      <SharingPanel />
      <BulkImportPanel />
      <TendencyAnalysisPanel />
      <BillingPanel sessionToken={session?.sessionToken ?? null} />
    </section>
  );
}
