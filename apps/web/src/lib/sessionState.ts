// M5-2 账号会话运行态（前端）。
//
// 凭据红线（M5-1 合同 / credential_security_redlines）：
//   - 会话令牌（session_token）只保存在**内存运行态**（本模块的闭包变量），
//     绝不写入 localStorage / sessionStorage / cookie-by-JS / 任何持久层，
//     刷新页面即丢失（需重新登录），从根本上避免令牌被本地持久化窃取。
//   - 本模块不保存、不回显密码；密码只在登录/注册表单里短暂存在，提交后即丢弃。
//   - 账号公开视图只含 user_id / display_name / account_status / auth_provider，
//     零正文、零凭据。
//   - 账号体系不参与、不改变主排序 / 召回 / source selection。
//
// 纯内存 + 可注入，便于在无真实浏览器下单测。

export type PublicAccount = {
  user_id: string;
  display_name: string;
  account_status: string;
  auth_provider: string;
};

// 运行态会话：token 只在内存里，expiresAt 仅用于前端提示，不做权威校验（以后端为准）。
export type SessionState = {
  account: PublicAccount;
  // sessionToken 仅内存保存，绝不持久化。
  sessionToken: string;
  expiresAt: string | null;
};

type Listener = (state: SessionState | null) => void;

// 模块级内存态。无任何 storage 读写。
let currentSession: SessionState | null = null;
const listeners = new Set<Listener>();

export function getSession(): SessionState | null {
  return currentSession;
}

export function isLoggedIn(): boolean {
  return currentSession !== null;
}

// 仅供受保护请求构造 Authorization 头用；不落任何日志/存储。
export function getAuthHeader(): Record<string, string> {
  if (!currentSession) {
    return {};
  }
  return { Authorization: `Bearer ${currentSession.sessionToken}` };
}

export function setSession(state: SessionState | null): void {
  currentSession = state;
  for (const listener of listeners) {
    listener(currentSession);
  }
}

export function clearSession(): void {
  setSession(null);
}

export function subscribe(listener: Listener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}
