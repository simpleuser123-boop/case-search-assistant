import { useEffect, useId, useState } from "react";

import {
  isPermissionTieringEnabled,
  isTeamSharingEnabled,
  isTeamWorkspaceEnabled,
} from "../../config/featureFlags";
import { PermissionPanel } from "../permission/PermissionPanel";
import { SharingPanel } from "../sharing/SharingPanel";
import { TeamWorkspacePanel } from "../team/TeamWorkspacePanel";

function hasSettingsCapability() {
  return isTeamWorkspaceEnabled() || isPermissionTieringEnabled() || isTeamSharingEnabled();
}

function SettingsContent() {
  if (!hasSettingsCapability()) {
    return (
      <div className="rounded-[6px] border border-[var(--color-border)] bg-[var(--color-bg)] px-4 py-3 text-xs leading-5 text-[var(--color-text-muted)]">
        当前没有已启用的团队协作设置项。
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <TeamWorkspacePanel />
      <PermissionPanel />
      <SharingPanel />
    </div>
  );
}

export function SettingsDialogButton() {
  const [open, setOpen] = useState(false);
  const titleId = useId();

  useEffect(() => {
    if (!open) {
      return;
    }

    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setOpen(false);
      }
    }

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [open]);

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="inline-flex items-center gap-2 rounded-[8px] border border-[var(--color-border-strong)] bg-[var(--color-surface)] px-3 py-1.5 text-xs font-medium text-[var(--color-brand)] transition hover:bg-[var(--color-brand-soft)] active:translate-y-px"
        aria-haspopup="dialog"
        aria-expanded={open}
      >
        设置
      </button>

      {open ? (
        <div
          className="fixed inset-0 z-40 flex items-start justify-center bg-[#111827]/35 px-4 py-16 backdrop-blur-sm sm:items-center sm:py-6"
          role="presentation"
          onMouseDown={() => setOpen(false)}
        >
          <section
            role="dialog"
            aria-modal="true"
            aria-labelledby={titleId}
            className="max-h-[calc(100dvh-4rem)] w-full max-w-[720px] overflow-y-auto rounded-[8px] border border-[var(--color-border)] bg-[var(--color-surface)] p-5 text-[var(--color-text)] shadow-[0_24px_80px_-32px_rgba(15,23,42,0.45)]"
            onMouseDown={(event) => event.stopPropagation()}
          >
            <div className="mb-4 flex items-start justify-between gap-4">
              <div>
                <h2 id={titleId} className="text-base font-semibold">
                  设置
                </h2>
                <p className="mt-1 text-xs leading-5 text-[var(--color-text-muted)]">
                  团队空间、权限分级、沉淀同步与团队共享集中在这里管理。
                </p>
              </div>
              <button
                type="button"
                onClick={() => setOpen(false)}
                className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-[6px] border border-[var(--color-border)] text-lg leading-none text-[var(--color-text-muted)] transition hover:bg-[var(--color-surface-muted)] active:translate-y-px"
                aria-label="关闭设置"
              >
                ×
              </button>
            </div>
            <SettingsContent />
          </section>
        </div>
      ) : null}
    </>
  );
}

export function M5WorkspacePanel() {
  if (!hasSettingsCapability()) {
    return null;
  }

  return (
    <section className="mx-auto w-full max-w-[760px] space-y-3" aria-label="设置">
      <div className="rounded-[8px] border border-[var(--color-border)] bg-[var(--color-surface)] p-4 text-sm leading-6">
        <h2 className="font-semibold text-[var(--color-text)]">设置</h2>
        <p className="mt-1 text-xs leading-5 text-[var(--color-text-muted)]">
          团队空间、权限分级、沉淀同步与团队共享均受 feature flag 控制。
        </p>
      </div>
      <SettingsContent />
    </section>
  );
}
