import { createBrowserRouter, type RouteObject } from "react-router-dom";

import {
  isCasebookEnabled,
  isDraftingEnabled,
  isIntakeEnabled,
  isStatuteSearchEnabled,
} from "../config/featureFlags";
import { CasebookPage } from "../pages/CasebookPage";
import { DraftingPage } from "../pages/DraftingPage";
import { HomePage } from "../pages/HomePage";
import { IntakePage } from "../pages/IntakePage";
import { SearchPage } from "../pages/SearchPage";
import { StatutePage } from "../pages/StatutePage";

// Day 2 6.1：/search 仅作为提交后的占位承接，完整结果页在 6.2 接入。
const routes: RouteObject[] = [
  {
    path: "/",
    element: <HomePage />,
  },
  {
    path: "/search",
    element: <SearchPage />,
  },
];

// E4-4：案情录入端路由严格受 VITE_ENABLE_INTAKE 门控。默认 off 时不注册 /intake 路由、
// 不渲染任何录入入口；IntakePage 内部再判一次 flag（双重门控），off 即渲染 null。
if (isIntakeEnabled()) {
  routes.push({
    path: "/intake",
    element: <IntakePage />,
  });
}

// E5-5：法条检索路由严格受 VITE_ENABLE_STATUTE_SEARCH 门控。默认 off 时不注册 /statute 路由、
// 不渲染任何法条检索入口；StatutePage 内部再判一次 flag（双重门控），off 即渲染 null。
// 与 VITE_ENABLE_INTAKE / VITE_ENABLE_M1_M5_ACCEPTANCE 正交，互不联动。
if (isStatuteSearchEnabled()) {
  routes.push({
    path: "/statute",
    element: <StatutePage />,
  });
}

// E6-3：文书工作台路由严格受 VITE_ENABLE_DRAFTING 门控。默认 off 时不注册 /drafting 路由、
// 不渲染任何文书工作台入口；DraftingPage 内部再判一次 flag（双重门控），off 即渲染 null。
// 与 VITE_ENABLE_INTAKE / VITE_ENABLE_STATUTE_SEARCH / VITE_ENABLE_M1_M5_ACCEPTANCE 正交，互不联动。
if (isDraftingEnabled()) {
  routes.push({
    path: "/drafting",
    element: <DraftingPage />,
  });
}

// E7-3：案件协作工作台路由严格受 VITE_ENABLE_CASEBOOK 门控。默认 off 时不注册 /casebook 路由、
// 不渲染任何协作工作台入口；CasebookPage 内部再判一次 flag（双重门控），off 即渲染 null。
// 与 VITE_ENABLE_INTAKE / VITE_ENABLE_STATUTE_SEARCH / VITE_ENABLE_DRAFTING /
// VITE_ENABLE_M1_M5_ACCEPTANCE 正交，互不联动。
if (isCasebookEnabled()) {
  routes.push({
    path: "/casebook",
    element: <CasebookPage />,
  });
}

export const router = createBrowserRouter(routes);
