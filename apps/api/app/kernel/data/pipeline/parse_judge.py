# -*- coding: utf-8 -*-
"""4.3 裁判文书解析 + 切分管道（JuDGE all.json -> CaseDocument / CaseChunk）。

设计依据：落地设计文档/04-数据层设计.md §3、§5、§8。
- 输入：JuDGE all.json（list，每条含 Fact / Full Document / Reasoning / Judgment / Crime Type / Law Articles 等）
- 输出：cases.jsonl（CaseDocument 元数据）、chunks.jsonl（CaseChunk）、quality_report.json（质量门禁）
- 全程不打印文书正文，仅回统计与抽样。
"""
from __future__ import annotations
import re, json, hashlib, argparse, datetime
from pathlib import Path

# ---------- 元数据正则 ----------
# 案号：（2019）豫0782刑初325号
RE_CASE_NO = re.compile(r"[（(]\s*\d{4}\s*[)）][一-龥]{1,6}\d{0,6}[刑民行赔执][初终再监终字].{0,8}?号")
# 法院：取首个以“人民法院/法院”结尾的机构名
RE_COURT = re.compile(r"[一-龥]{2,15}?(?:人民法院|法院)")
# 裁判日期：支持阿拉伯数字与中文数字两种
RE_DATE_ARAB = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")
RE_DATE_CN = re.compile(r"([〇零一二三四五六七八九]{4})\s*年\s*([元一二三四五六七八九十]{1,3})\s*月\s*([一二三四五六七八九十]{1,3})\s*日")
# 本院查明 / 经审理查明 段落起点（扩充触发词，提升识别率）
RE_COURT_FOUND = re.compile(r"(经(?:本院)?审理查明|本院查明|经审理查明|审理查明|经审理认定|经查明|查明[:：])")

CN_NUM = {"〇":0,"零":0,"一":1,"二":2,"三":3,"四":4,"五":5,"六":6,"七":7,"八":8,"九":9,"十":10,"元":1}

def cn_year(s: str) -> int:
    return int("".join(str(CN_NUM[c]) for c in s)) if all(c in CN_NUM for c in s) else 0

def cn_md(s: str) -> int:
    # 处理 十、十X、X十、X十X、X
    if not s: return 0
    if s == "十": return 10
    if s.startswith("十"): return 10 + CN_NUM.get(s[1], 0)
    if "十" in s:
        a, _, b = s.partition("十")
        return CN_NUM.get(a, 0) * 10 + (CN_NUM.get(b, 0) if b else 0)
    return CN_NUM.get(s, 0)

def extract_date(text: str):
    """裁判日期取全文最后一个日期（落款日期在文末；首个日期常是被告出生日期）。"""
    cands = []
    for m in RE_DATE_ARAB.finditer(text):
        cands.append((m.start(), int(m.group(1)), int(m.group(2)), int(m.group(3))))
    for m in RE_DATE_CN.finditer(text):
        cands.append((m.start(), cn_year(m.group(1)), cn_md(m.group(2)), cn_md(m.group(3))))
    if not cands:
        return None
    cands.sort(key=lambda x: x[0])  # 按出现位置
    for _, y, mo, d in reversed(cands):  # 从最后一个有效日期往前取
        try:
            return datetime.date(y, mo, d).isoformat()
        except ValueError:
            continue
    return None

def court_level(court: str) -> str:
    if not court: return ""
    if "最高人民法院" in court: return "最高"
    if "高级人民法院" in court: return "高级"
    if "中级人民法院" in court: return "中级"
    return "基层"

def trial_level(case_no: str) -> str:
    if not case_no: return ""
    if "初" in case_no: return "一审"
    if "终" in case_no: return "二审"
    if "再" in case_no: return "再审"
    if "执" in case_no: return "执行"
    return ""

# 省/直辖市 -> region 粗提取（从法院名前缀）
PROVS = ["北京市","天津市","上海市","重庆市","河北省","山西省","辽宁省","吉林省","黑龙江省",
         "江苏省","浙江省","安徽省","福建省","江西省","山东省","河南省","湖北省","湖南省",
         "广东省","海南省","四川省","贵州省","云南省","陕西省","甘肃省","青海省","台湾省",
         "内蒙古自治区","广西壮族自治区","西藏自治区","宁夏回族自治区","新疆维吾尔自治区"]

def extract_region(court: str) -> str:
    for p in PROVS:
        if court.startswith(p):
            return p
    return ""

# ---------- 切分 ----------
CHUNK_MIN, CHUNK_MAX, OVERLAP = 600, 1000, 100

def split_long(text: str, max_len: int = CHUNK_MAX, overlap: int = OVERLAP):
    """超长段落按句末标点切，控制 max_len，相邻保留 overlap 字重叠。"""
    text = text.strip()
    if len(text) <= max_len:
        return [text] if text else []
    sents = re.split(r"(?<=[。；;！？!?])", text)
    chunks, buf = [], ""
    for s in sents:
        if len(buf) + len(s) <= max_len:
            buf += s
        else:
            if buf:
                chunks.append(buf)
            buf = (buf[-overlap:] if buf else "") + s
            while len(buf) > max_len:
                chunks.append(buf[:max_len])
                buf = buf[max_len - overlap:]
    if buf:
        chunks.append(buf)
    return chunks

def build_chunks(case_id: str, full_doc: str, fact: str, reasoning: str, judgment: str):
    """优先用 JuDGE 已切好的字段；court_found 从全文正则定位。绑定 offset。"""
    chunks = []
    seq = 0

    def add(ctype: str, seg_text: str, src_text: str):
        nonlocal seq
        for piece in split_long(seg_text):
            piece = piece.strip()
            if not piece:
                continue
            off = src_text.find(piece[:30]) if src_text else -1
            start = off if off >= 0 else -1
            end = (start + len(piece)) if start >= 0 else -1
            chunks.append({
                "chunk_id": f"{case_id}_c{seq:03d}",
                "case_id": case_id,
                "chunk_type": ctype,
                "text": piece,
                "start_offset": start,
                "end_offset": end,
                "quality_score": round(min(1.0, len(piece) / CHUNK_MAX), 3),
            })
            seq += 1

    # fact 事实
    if fact and fact.strip():
        add("fact", fact, full_doc)
    # court_found 本院查明：从全文截取“查明”到“本院认为/Reasoning起点”之间
    if full_doc:
        mf = RE_COURT_FOUND.search(full_doc)
        if mf:
            tail = full_doc[mf.start():]
            cut = re.search(r"本院认为", tail)
            seg = tail[: cut.start()] if cut else tail[:CHUNK_MAX]
            add("court_found", seg, full_doc)
    # court_opinion 本院认为
    if reasoning and reasoning.strip():
        add("court_opinion", reasoning, full_doc)
    # judgment_result 裁判结果
    if judgment and judgment.strip():
        add("judgment_result", judgment, full_doc)
    return chunks

# ---------- 主流程 ----------
def run(input_path: str, out_dir: str, limit: int = 0, sample: int = 0):
    inp = Path(input_path)
    out = Path(out_dir); out.mkdir(parents=True, exist_ok=True)
    data = json.load(open(inp, encoding="utf-8"))
    if limit:
        data = data[:limit]

    seen_hash = set()
    n = len(data)
    n_caseno = n_court = n_date = n_found = 0
    dup = 0
    empty_chunk = 0
    total_chunks = 0
    type_counter = {}
    cases_f = open(out / "cases.jsonl", "w", encoding="utf-8")
    chunks_f = open(out / "chunks.jsonl", "w", encoding="utf-8")
    samples = []

    for i, rec in enumerate(data):
        cid = rec.get("CaseId") or rec.get("CaseID") or f"case_{i:05d}"
        full_doc = rec.get("Full Document", "") or ""
        fact = rec.get("Fact", "") or ""
        reasoning = rec.get("Reasoning", "") or ""
        judgment = rec.get("Judgment", "") or ""
        crime = rec.get("Crime Type", []) or []

        m_no = RE_CASE_NO.search(full_doc)
        case_no = m_no.group(0) if m_no else ""
        court = ""
        m_ct = RE_COURT.search(full_doc[:80]) or RE_COURT.search(full_doc)
        if m_ct:
            court = m_ct.group(0)
        jdate = extract_date(full_doc) or ""
        region = extract_region(court)
        clevel = court_level(court)
        tlevel = trial_level(case_no)
        text_hash = hashlib.sha256((full_doc or fact).encode("utf-8")).hexdigest()
        status = "active"
        if text_hash in seen_hash:
            status = "duplicate"; dup += 1
        else:
            seen_hash.add(text_hash)

        if case_no: n_caseno += 1
        if court: n_court += 1
        if jdate: n_date += 1
        if RE_COURT_FOUND.search(full_doc): n_found += 1

        doc = {
            "case_id": cid, "case_no": case_no, "title": court + ("刑事判决书" if court else ""),
            "court": court, "court_level": clevel, "trial_level": tlevel,
            "case_cause": (crime[-1] if crime else ""), "crime_type": crime,
            "law_articles": rec.get("Law Articles", []),
            "judgment_date": jdate, "region": region,
            "source_url": "", "source_name": "JuDGE", "text_hash": text_hash, "status": status,
        }
        cases_f.write(json.dumps(doc, ensure_ascii=False) + "\n")

        if status == "duplicate":
            continue
        cks = build_chunks(cid, full_doc, fact, reasoning, judgment)
        for ck in cks:
            if not ck["text"].strip():
                empty_chunk += 1
            type_counter[ck["chunk_type"]] = type_counter.get(ck["chunk_type"], 0) + 1
            chunks_f.write(json.dumps(ck, ensure_ascii=False) + "\n")
        total_chunks += len(cks)
        if sample and len(samples) < sample:
            samples.append({"case_id": cid, "case_no": case_no, "court": court,
                            "court_level": clevel, "trial_level": tlevel,
                            "judgment_date": jdate, "region": region,
                            "case_cause": doc["case_cause"], "n_chunks": len(cks),
                            "chunk_types": [c["chunk_type"] for c in cks]})
    cases_f.close(); chunks_f.close()

    def miss(x): return round((n - x) / n * 100, 2) if n else 0
    report = {
        "total_cases": n, "total_chunks": total_chunks,
        "dup_count": dup, "dup_rate_pct": round(dup / n * 100, 2) if n else 0,
        "case_no_missing_pct": miss(n_caseno),
        "court_missing_pct": miss(n_court),
        "date_missing_pct": miss(n_date),
        "court_found_recall_pct": round(n_found / n * 100, 2) if n else 0,
        "empty_chunk_rate_pct": round(empty_chunk / total_chunks * 100, 2) if total_chunks else 0,
        "chunk_type_dist": type_counter,
        "avg_chunks_per_case": round(total_chunks / max(1, n - dup), 2),
    }
    json.dump(report, open(out / "quality_report.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    return report, samples

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--sample", type=int, default=0)
    args = ap.parse_args()
    rep, samp = run(args.input, args.out, args.limit, args.sample)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    if samp:
        print("\n=== SAMPLES ===")
        print(json.dumps(samp, ensure_ascii=False, indent=2))
