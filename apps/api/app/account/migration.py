"""E-2b shim — 自我替换为 app.kernel.identity.account.migration（同一模块对象，零行为分叉）。"""
import importlib as _il, sys as _s
_s.modules[__name__] = _il.import_module("app.kernel.identity.account.migration")
