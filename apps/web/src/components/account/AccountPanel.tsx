import { useEffect, useState } from "react";

import { isAccountSystemEnabled } from "../../config/featureFlags";
import { login, logout, register } from "../../services/authApi";
import {
  getSession,
  subscribe,
  type SessionState,
} from "../../lib/sessionState";

// M5-2 账号面板（flag-gated）。
//
// 默认 false（isAccountSystemEnabled=false）时直接渲染 null：不显示任何登录/注册
// 入口，不调用任何账号接口，页面与 M4 单用户私有末态完全一致。
//
// 凭据红线：
//   - login_name / password 由用户在本表单输入；组件不预填、不代填、不保存密码。
//   - 密码字段提交后即从本地 state 清空；绝不写 localStorage、不打日志、不回显。
//   - 会话令牌只交给 sessionState（仅内存）保存，本组件不接触持久层。
//   - 账号入口不参与、不改变主检索 / 排序 / 召回。

type Mode = "login" | "register";

type AccountPanelProps = {
  compact?: boolean;
};

const LOGIN_NAME_MIN_LENGTH = 3;
const PASSWORD_MIN_LENGTH = 8;
const DISPLAY_NAME_MAX_LENGTH = 60;

function validateAuthInput(mode: Mode, loginName: string, password: string, displayName: string) {
  if (loginName.trim().length < LOGIN_NAME_MIN_LENGTH) {
    return "登录名至少 3 个字符。";
  }
  if (password.length < PASSWORD_MIN_LENGTH) {
    return "密码至少 8 位。";
  }
  if (mode === "register" && displayName.length > DISPLAY_NAME_MAX_LENGTH) {
    return "显示名最多 60 个字符。";
  }
  return null;
}

function authFailureMessage(
  action: "login" | "register",
  reason: "disabled" | "rejected" | "network_error" | "http_error"
) {
  if (reason === "disabled") return "账号体系未启用。";
  if (reason === "network_error") return "无法连接认证服务，请确认后端服务已启动。";
  if (reason === "http_error") return "认证服务暂不可用，请确认数据库和后端服务正常。";
  return action === "register"
    ? "注册未通过：登录名可能已存在，或输入不符合规则。"
    : "登录未通过：请检查登录名和密码。";
}

export function AccountPanel({ compact = false }: AccountPanelProps) {
  const enabled = isAccountSystemEnabled();
  const [session, setSessionLocal] = useState<SessionState | null>(getSession());
  const [mode, setMode] = useState<Mode>("login");
  const [loginName, setLoginName] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [status, setStatus] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!enabled) {
      return;
    }
    return subscribe((next) => setSessionLocal(next));
  }, [enabled]);

  // 关闭态：不渲染任何账号 UI。这是回到 M4 末态的硬保证。
  if (!enabled) {
    return null;
  }

  function clearPasswordField() {
    // 提交后立即丢弃明文密码。
    setPassword("");
  }

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (busy) return;
    setStatus(null);

    const inputError = validateAuthInput(mode, loginName, password, displayName);
    if (inputError) {
      clearPasswordField();
      setStatus(inputError);
      return;
    }

    setBusy(true);
    try {
      if (mode === "register") {
        const result = await register({ loginName, password, displayName });
        clearPasswordField();
        if (!result.ok) {
          setStatus(authFailureMessage("register", result.reason));
          return;
        }
        setStatus("注册成功，请登录。");
        setMode("login");
        return;
      }
      const result = await login({ loginName, password });
      clearPasswordField();
      if (!result.ok) {
        setStatus(authFailureMessage("login", result.reason));
        return;
      }
      setStatus(null);
    } finally {
      setBusy(false);
    }
  }

  async function handleLogout() {
    setBusy(true);
    try {
      await logout();
      setStatus("已登出。");
    } finally {
      setBusy(false);
    }
  }

  if (session) {
    return (
      <section
        aria-label="账号"
        className={`rounded-[6px] border border-[var(--color-border)] bg-[var(--color-surface)] px-4 py-3 text-sm text-[var(--color-text)] ${
          compact ? "border-transparent bg-transparent px-0 py-0" : ""
        }`}
      >
        <div className="flex items-center justify-between gap-3">
          <div>
            <p className="font-medium">{session.account.display_name || session.account.user_id}</p>
            <p className="mt-0.5 text-xs text-[var(--color-text-muted)]">
              已登录 · {session.account.auth_provider}
            </p>
          </div>
          <button
            type="button"
            onClick={handleLogout}
            disabled={busy}
            className="rounded-[4px] border border-[var(--color-border)] px-3 py-1.5 text-xs hover:bg-[var(--color-bg)]"
          >
            登出
          </button>
        </div>
        <p className="mt-2 text-xs text-[var(--color-text-muted)]">
          沉淀仍在本浏览器本地；登录后可显式认领到此账号（默认不自动迁移）。
        </p>
        {status ? <p className="mt-1 text-xs text-[var(--color-text-muted)]">{status}</p> : null}
      </section>
    );
  }

  return (
    <section
      aria-label="账号"
      className={`rounded-[6px] border border-[var(--color-border)] bg-[var(--color-surface)] px-4 py-3 text-sm text-[var(--color-text)] ${
        compact ? "border-transparent bg-transparent px-0 py-0" : ""
      }`}
    >
      <div className="mb-2 flex gap-2 text-xs">
        <button
          type="button"
          onClick={() => setMode("login")}
          className={mode === "login" ? "font-semibold underline" : "text-[var(--color-text-muted)]"}
        >
          登录
        </button>
        <button
          type="button"
          onClick={() => setMode("register")}
          className={mode === "register" ? "font-semibold underline" : "text-[var(--color-text-muted)]"}
        >
          注册
        </button>
      </div>
      <form onSubmit={handleSubmit} className="flex flex-col gap-2">
        <label className="flex flex-col gap-1 text-xs">
          登录名
          <input
            type="text"
            autoComplete="username"
            value={loginName}
            onChange={(e) => setLoginName(e.target.value)}
            className="rounded-[4px] border border-[var(--color-border)] px-2 py-1.5 text-sm"
          />
        </label>
        {mode === "register" ? (
          <label className="flex flex-col gap-1 text-xs">
            显示名（可选）
            <input
              type="text"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              maxLength={DISPLAY_NAME_MAX_LENGTH}
              className="rounded-[4px] border border-[var(--color-border)] px-2 py-1.5 text-sm"
            />
          </label>
        ) : null}
        <label className="flex flex-col gap-1 text-xs">
          密码
          <input
            type="password"
            autoComplete={mode === "register" ? "new-password" : "current-password"}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="rounded-[4px] border border-[var(--color-border)] px-2 py-1.5 text-sm"
          />
        </label>
        <button
          type="submit"
          disabled={busy}
          className="mt-1 rounded-[4px] bg-[var(--color-text)] px-3 py-1.5 text-xs text-[var(--color-bg)]"
        >
          {mode === "register" ? "注册" : "登录"}
        </button>
      </form>
      <p className="mt-2 text-xs text-[var(--color-text-muted)]">
        凭据由你本人输入；密码仅以单向哈希存储，会话令牌只在本次会话内存中有效。
      </p>
      {status ? <p className="mt-1 text-xs text-[var(--color-text-muted)]">{status}</p> : null}
    </section>
  );
}

export function AccountDialogButton() {
  const enabled = isAccountSystemEnabled();
  const [session, setSessionLocal] = useState<SessionState | null>(getSession());
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (!enabled) {
      return;
    }
    return subscribe((next) => setSessionLocal(next));
  }, [enabled]);

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

  if (!enabled) {
    return null;
  }

  const accountLabel = session?.account.display_name || session?.account.user_id;

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="inline-flex items-center gap-2 rounded-[8px] border border-[var(--color-border-strong)] bg-[var(--color-surface)] px-3 py-1.5 text-xs font-medium text-[var(--color-brand)] transition hover:bg-[var(--color-brand-soft)] active:translate-y-px"
        aria-haspopup="dialog"
      >
        <span
          aria-hidden="true"
          className={`h-1.5 w-1.5 rounded-full ${
            session ? "bg-[var(--color-success)]" : "bg-[var(--color-text-subtle)]"
          }`}
        />
        {session ? accountLabel : "登录"}
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
            aria-labelledby="account-dialog-title"
            className="w-full max-w-[420px] rounded-[8px] border border-[var(--color-border)] bg-[var(--color-surface)] p-5 text-[var(--color-text)] shadow-[0_24px_80px_-32px_rgba(15,23,42,0.45)]"
            onMouseDown={(event) => event.stopPropagation()}
          >
            <div className="mb-4 flex items-start justify-between gap-4">
              <div>
                <h2 id="account-dialog-title" className="text-base font-semibold">
                  登录工作台
                </h2>
                <p className="mt-1 text-xs leading-5 text-[var(--color-text-muted)]">
                  账号只用于显式登录和认领，本次检索排序不受影响。
                </p>
              </div>
              <button
                type="button"
                onClick={() => setOpen(false)}
                className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-[6px] border border-[var(--color-border)] text-lg leading-none text-[var(--color-text-muted)] transition hover:bg-[var(--color-surface-muted)] active:translate-y-px"
                aria-label="关闭登录工作台"
              >
                ×
              </button>
            </div>
            <AccountPanel compact />
          </section>
        </div>
      ) : null}
    </>
  );
}
