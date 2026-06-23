# -*- coding: utf-8 -*-
"""M5-7 路线B：裁判文书网备份(马克数据网 CSV)→ 仅元数据语料(供 F19 数据门禁只读评估)。

红线（M5-7）：
- **只抽元数据**：案号/法院/层级/审级/案由/年份/地域/领域/来源链接；
  **绝不读取、绝不写出** `当事人`、`全文` 两列（正文+个人信息）。
- **不改产品数据**：输出到独立文件 tendency_corpus_meta.jsonl，不碰 cases.jsonl/向量库。
- 年份取自案号 `（YYYY）`（文件夹年份是公开批次，不可信；裁判日期列有占位值）。
- 流式读 zip 内 CSV，不解压；按 案号 去重。
"""
from __future__ import annotations
import zipfile, csv, io, json, re, glob, os, hashlib, argparse

# 全文列可能超大；调大单字段上限，仅为让 csv 能跳过它而不报错（我们不输出该列）
csv.field_size_limit(50_000_000)

RE_YEAR = re.compile(r"[（(]\s*(\d{4})")
SENSITIVE_COLS = {"当事人", "全文"}  # 永不输出

def court_level(c: str) -> str:
    if not c: return ""
    if "最高人民法院" in c: return "最高"
    if "高级人民法院" in c: return "高级"
    if "中级人民法院" in c: return "中级"
    if "人民法院" in c or "法院" in c: return "基层"
    return ""

def trial_level(raw: str, case_no: str) -> str:
    s = (raw or "") + " " + (case_no or "")
    if "二审" in s or "终" in s: return "二审"
    if "再审" in s or "审判监督" in s or "再" in s: return "再审"
    if "执行" in s or "非诉" in s or "执" in s: return "执行"
    if "一审" in s or "初" in s: return "一审"
    return ""

DOMAIN_MAP = {"刑事案件": "criminal", "民事案件": "civil", "行政案件": "administrative",
              "执行案件": "execution", "赔偿案件": "compensation"}

def domain_of(case_type: str, case_no: str) -> str:
    if case_type in DOMAIN_MAP: return DOMAIN_MAP[case_type]
    # 兜底：从案号的刑民行字推断
    if "刑" in case_no: return "criminal"
    if "民" in case_no: return "civil"
    if "行" in case_no: return "administrative"
    if "执" in case_no: return "execution"
    if "赔" in case_no: return "compensation"
    return "unknown"

def year_of(case_no: str, jdate: str) -> str:
    m = RE_YEAR.search(case_no or "")
    if m: return m.group(1)
    m2 = re.match(r"(\d{4})", (jdate or "").strip())
    return m2.group(1) if m2 else ""

def run(zip_paths, out_path, max_per_zip=0, append=False):
    seen = set()
    n_in = n_out = 0
    mode = "a" if append else "w"
    out = open(out_path, mode, encoding="utf-8")
    for zp in zip_paths:
        if not os.path.exists(zp): 
            print("MISSING", zp); continue
        zname = os.path.basename(zp)
        cnt = 0
        with zipfile.ZipFile(zp) as zf:
            for name in zf.namelist():
                if not name.lower().endswith(".csv"): continue
                if max_per_zip and cnt >= max_per_zip: break
                with zf.open(name) as raw:
                    r = csv.reader(io.TextIOWrapper(raw, encoding="utf-8", errors="replace"))
                    try: header = next(r)
                    except StopIteration: continue
                    idx = {c.strip().lstrip("﻿"): i for i, c in enumerate(header)}
                    gi = lambda k: idx.get(k, -1)
                    i_no, i_court, i_reg = gi("案号"), gi("法院"), gi("所属地区")
                    i_type, i_tri, i_cause = gi("案件类型"), gi("审理程序"), gi("案由")
                    i_date, i_link = gi("裁判日期"), gi("原始链接")
                    def cell(row, i):
                        return row[i].strip() if 0 <= i < len(row) else ""
                    for row in r:
                        if len(row) < len(header): continue
                        n_in += 1
                        case_no = cell(row, i_no)
                        court = cell(row, i_court)
                        key = hashlib.sha256((case_no + "|" + court).encode("utf-8")).hexdigest()
                        if not case_no or key in seen:
                            continue
                        seen.add(key)
                        ctype = cell(row, i_type)
                        yr = year_of(case_no, cell(row, i_date))
                        rec = {
                            "case_id": "ws_" + key[:16],
                            "case_no": case_no,
                            "court": court,
                            "court_level": court_level(court),
                            "trial_level": trial_level(cell(row, i_tri), case_no),
                            "case_cause": cell(row, i_cause),
                            "domain": domain_of(ctype, case_no),
                            "judgment_year": yr,
                            "region": cell(row, i_reg),
                            "source_url": cell(row, i_link),
                            "source_name": "马克数据网(裁判文书网备份)",
                            "source_updated_at": "2023-05-13",
                            "status": "active",
                        }
                        out.write(json.dumps(rec, ensure_ascii=False) + "\n")
                        n_out += 1
                        cnt += 1
                        if max_per_zip and cnt >= max_per_zip: break
        print(f"{zname}: kept {cnt}")
    out.close()
    print(f"TOTAL in={n_in} out(unique)={n_out} -> {out_path}")
    return n_out

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--glob", default="/sessions/sleepy-adoring-feynman/mnt/Downloads/**/*裁判文书*.zip")
    ap.add_argument("--years", default="", help="逗号分隔年份过滤，如 1985,2001")
    ap.add_argument("--out", required=True)
    ap.add_argument("--max-per-zip", type=int, default=0)
    ap.add_argument("--append", action="store_true")
    a = ap.parse_args()
    allz = {os.path.basename(p): p for p in glob.glob(a.glob, recursive=True)}
    if a.years:
        want = set(a.years.split(","))
        zips = [p for n, p in sorted(allz.items()) if any(y in n for y in want)]
    else:
        zips = [p for _, p in sorted(allz.items())]
    run(zips, a.out, a.max_per_zip, a.append)
