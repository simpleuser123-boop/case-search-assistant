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

export function AccountPanel() {
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
    setBusy(true);
    setStatus(null);
    try {
      if (mode === "register") {
        const result = await register({ loginName, password, displayName });
        clearPasswordField();
        if (!result.ok) {
          setStatus(result.reason === "disabled" ? "账号体系未启用。" : "注册未通过，请检查输入。");
          return;
        }
        setStatus("注册成功，请登录。");
        setMode("login");
        return;
      }
      const result = await login({ loginName, password });
      clearPasswordField();
      if (!result.ok) {
        setStatus(result.reason === "disabled" ? "账号体系未启用。" : "登录未通过。");
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
        className="rounded-[6px] border border-[var(--color-border)] bg-[var(--color-surface)] px-4 py-3 text-sm text-[var(--color-text)]"
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
      className="rounded-[6px] border border-[var(--color-border)] bg-[var(--color-surface)] px-4 py-3 text-sm text-[var(--color-text)]"
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
              maxLength={60}
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
