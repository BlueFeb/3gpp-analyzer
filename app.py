import streamlit as st
import streamlit.components.v1 as components
import os
import tempfile
import zipfile
import requests
import numpy as np
import re
import io
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from openpyxl import load_workbook
from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph
from sklearn.cluster import AgglomerativeClustering
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

# ==========================================
# 0. 구글 애드센스 광고 영역 (텍스트 숨김 처리)
# ==========================================
def show_adsense():
    """
    구글 애드센스 코드를 삽입하는 영역입니다.
    현재는 화면에 아무런 글자도 보이지 않는 빈 공간(투명)으로 처리되어 있습니다.
    """
    html_code = """
    <div style="width: 100%; height: 90px; background-color: transparent;">
        </div>
    """
    components.html(html_code, height=100)

# ==========================================
# 1. 환경 설정 및 세션 초기화
# ==========================================
st.set_page_config(page_title="3GPP AI Analyzer Pro", page_icon="📡", layout="wide")

if "authenticated" not in st.session_state:
    st.session_state.authenticated = False
if "log_text" not in st.session_state:
    st.session_state.log_text = ""
if "process_done" not in st.session_state:
    st.session_state.process_done = False
if "out1_bio" not in st.session_state:
    st.session_state.out1_bio = None
if "out2_bio" not in st.session_state:
    st.session_state.out2_bio = None
if "extracted_data" not in st.session_state:
    st.session_state.extracted_data = []

def append_log(text):
    st.session_state.log_text += f"{text}\n"

INTERNAL_PROXY = {"http": "http://10.112.1.184:8080", "https": "http://10.112.1.184:8080"}
INTERNAL_PIN_URL = (
    "https://raw.github.sec.samsung.net/gist/bh14-jung/"
    "d3cfd4262296e61a66ddcaaf045657ed/raw/"
    "67237fa5e0f0f8b781d984c420c45ebede27adf9/"
    "gistfile1.txt?token=AAAAOIJ4CH7DR5P4CEMRHZLIY6KJM"
)
EXTERNAL_PIN_CSV_URL = (
    "https://docs.google.com/spreadsheets/d/e/"
    "2PACX-1vTsSe2LFcO129jJVigl6e9TzHVz8Iaoozasee_4bD1RTwoRS5DTSv-"
    "FdO7dwrPcJ6t7wmQ0-s7197g5/pub?gid=0&single=true&output=csv"
)
FALLBACK_PIN = "2510"
USE_PROXY = False

def detect_network():
    global USE_PROXY
    try:
        requests.get(INTERNAL_PIN_URL, proxies=INTERNAL_PROXY, timeout=3, verify=False).raise_for_status()
        USE_PROXY = True
    except:
        USE_PROXY = False

def fetch_remote_pin():
    if USE_PROXY:
        try:
            r = requests.get(INTERNAL_PIN_URL, proxies=INTERNAL_PROXY, timeout=5, verify=False)
            r.raise_for_status()
            p = r.text.strip().splitlines()[0].strip()
            if p.isdigit() and len(p) == 4:
                return p
        except:
            pass
    try:
        r = requests.get(EXTERNAL_PIN_CSV_URL, timeout=5)
        r.raise_for_status()
        first = r.text.strip().splitlines()[0]
        p = first.split(",")[0].strip()
        if p.isdigit() and len(p) == 4:
            return p
    except:
        pass
    return FALLBACK_PIN

# ==========================================
# 2. 문서 처리 및 핵심 로직
# ==========================================
def read_excel_from_bytes(uploaded_file):
    wb = load_workbook(uploaded_file, read_only=False, data_only=True)
    ws = wb.active
    entries = []
    for row in ws.iter_rows(min_row=2):
        cell = row[0]
        comp = row[2] if len(row) > 2 else None
        docid = str(cell.value).strip() if cell.value else ""
        company = str(comp.value).strip() if comp and comp.value else ""
        
        if not docid: continue
        
        if getattr(cell, "hyperlink", None) and cell.hyperlink.target:
            link = cell.hyperlink.target
        else:
            link = f"https://www.3gpp.org/ftp/tsg_ran/WG1_RL1/TSGR1_122/Docs/{docid}.zip"
        entries.append({"doc": docid, "company": company, "link": link})
    return entries

def clone_paragraph(src, dest):
    np_para = dest.add_paragraph("", style=src.style)
    for r in src.runs:
        nr = np_para.add_run(r.text)
        nr.bold = r.bold
        nr.italic = r.italic
        nr.underline = r.underline
        if hasattr(r.font, "name") and r.font.name:
            nr.font.name = r.font.name
        if hasattr(r.font, "size") and r.font.size:
            nr.font.size = r.font.size
        if hasattr(r.font, "color") and getattr(r.font.color, "rgb", None):
            nr.font.color.rgb = r.font.color.rgb
    return np_para

def repackage_docm_to_docx(path, td):
    ud = os.path.join(td, "docm_unzip")
    os.makedirs(ud, exist_ok=True)
    with zipfile.ZipFile(path, 'r') as zf: 
        zf.extractall(ud)
    tf = os.path.join(ud, "[Content_Types].xml")
    t = open(tf, 'r', encoding='utf-8').read()
    t = t.replace(
        'application/vnd.ms-word.document.macroEnabled.main+xml',
        'application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml'
    )
    open(tf, 'w', encoding='utf-8').write(t)
    rp = os.path.join(td, "repack.zip")
    with zipfile.ZipFile(rp, 'w', zipfile.ZIP_DEFLATED) as zf:
        for r, _, fs in os.walk(ud):
            for f in fs:
                full = os.path.join(r, f)
                arc = os.path.relpath(full, ud)
                zf.write(full, arc)
    out = os.path.join(td, "repack.docx")
    os.rename(rp, out)
    return out

def _download_doc(entry, td, headers):
    try:
        kwargs = {"headers": headers, "timeout": 60, "verify": False}
        if USE_PROXY: kwargs["proxies"] = INTERNAL_PROXY
        r = requests.get(entry["link"], **kwargs)
        r.raise_for_status()
        fp = os.path.join(td.name, f"{entry['doc']}.zip")
        with open(fp, "wb") as f:
            f.write(r.content)
        return entry, fp, None
    except Exception as ex:
        return entry, None, str(ex)

def extract_all_conclusions(entries, status_elem, progress_elem, log_func):
    td = tempfile.TemporaryDirectory()
    log_func(f"임시 디렉터리 생성: {td.name}")

    od = Document()
    od.add_heading("3GPP Conclusions", level=0)
    
    cps = [re.compile(r"^(?:#\s*)?(?:\d+\.?\s*)?(conclusions?)\s*$", re.I), re.compile(r"^(?:#\s*)?(?:\d+\.?\s*)?(summary)\s*$", re.I)]
    eps = [re.compile(r"^(?:#\s*)?(?:\d+\.?\s*)?(references?|appendix|acknowledgment)\s*$", re.I)]
    headers = {"User-Agent": "Mozilla/5.0"}

    download_results = []
    extracted_list = []
    total = len(entries)
    
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(_download_doc, e, td, headers): e for e in entries}
        for i, fut in enumerate(as_completed(futures), start=1):
            e, fp, err = fut.result()
            download_results.append((e, fp, err))
            progress_elem.progress(i / total)
            status_elem.text(f"Downloaded [{i}/{total}]: {e['doc']}")
            log_func(f"[{i}/{total}] Downloaded: {e['doc']}")

    for idx, (e, fp, err) in enumerate(download_results, start=1):
        status_elem.text(f"Extracting [{idx}/{total}]: {e['doc']}")
        doc_text_buffer = []

        tbl = od.add_table(rows=4, cols=2, style="Table Grid")
        tbl.cell(0, 0).text, tbl.cell(0, 1).text = "Document", e["doc"]
        tbl.cell(1, 0).text, tbl.cell(1, 1).text = "Link", e["link"]
        tbl.cell(2, 0).text, tbl.cell(2, 1).text = "Company", e["company"]
        tbl.cell(3, 0).text = "Title"

        try:
            if err or not fp: raise Exception(err or "Download failed")

            ed = os.path.join(td.name, e["doc"])
            os.makedirs(ed, exist_ok=True)
            with zipfile.ZipFile(fp) as zf:
                zf.extractall(ed)

            src_path = None
            for ext in ("*.docx", "*.docm", "*.doc"):
                src_path = next(Path(ed).rglob(ext), None)
                if src_path: break

            if not src_path:
                od.add_paragraph("DOC 파일을 찾을 수 없습니다.")
                log_func(f"{e['doc']} 없음")
                continue

            file_path_str = str(src_path)
            
            # .docm 파일 처리
            if src_path.suffix.lower() == ".docm":
                try:
                    file_path_str = repackage_docm_to_docx(file_path_str, td.name)
                except Exception as ex:
                    log_func(f"{e['doc']} docm 변환 오류: {ex}")
            
            try:
                sd = Document(file_path_str)
            except Exception as ex:
                od.add_paragraph(f"문서를 열 수 없습니다 (구형 .doc 파일이거나 손상됨): {ex}")
                log_func(f"{e['doc']} 문서 파싱 에러: {ex}")
                continue
                
            title = ""
            for p in sd.paragraphs:
                t = p.text.strip()
                if t.lower().startswith("title:"):
                    title = t.split(":", 1)[1].strip()
                    break
            if not title:
                title = sd.core_properties.title or ""
            tbl.cell(3, 1).text = title

            paras = sd.paragraphs
            start = None
            for pat in cps:
                for j, p in enumerate(paras):
                    if pat.match(p.text.strip()):
                        start = j
                        break
                if start is not None: break

            if start is None:
                od.add_paragraph("결론 섹션 없음")
                log_func(f"{e['doc']} 결론없음")
            else:
                end = len(paras)
                for ep in eps:
                    for j, p in enumerate(paras[start + 1 :], start + 1):
                        if ep.match(p.text.strip()):
                            end = j
                            break
                    if end < len(paras): break
                for j in range(start + 1, end):
                    clone_paragraph(paras[j], od)
                    doc_text_buffer.append(paras[j].text)
                log_func(f"{e['doc']} 추출 완료")

            extracted_list.append({
                "doc": e["doc"], "company": e["company"], "link": e["link"], 
                "title": title, "content": "\n".join(doc_text_buffer) if doc_text_buffer else "Conclusion 섹션을 찾지 못했습니다."
            })

        except Exception as ex:
            od.add_paragraph(f"오류 - {e['doc']}: {ex}")
            log_func(str(ex))

        if idx < len(download_results):
            od.add_page_break()

    st.session_state.extracted_data = extracted_list
    bio = io.BytesIO()
    od.save(bio)
    bio.seek(0)
    td.cleanup()
    return bio

class TFIDFEmbedder:
    def __init__(self, max_features=3000, ngram_range=(1, 2)):
        self.v = TfidfVectorizer(
            max_features=max_features, ngram_range=ngram_range,
            lowercase=True, stop_words="english", strip_accents="unicode",
            token_pattern=r"\b[a-zA-Z]{2,}\b",
        )
        self.fitted = False

    def encode(self, texts):
        if isinstance(texts, str): texts = [texts]
        proc = [re.sub(r"\s+", " ", re.sub(r"[^\w\s\-]", " ", t.lower())).strip() for t in texts]
        if not self.fitted:
            self.v.fit(proc)
            self.fitted = True
        return self.v.transform(proc).toarray()

def parse_and_summarize(in_bio, status_elem, log_func):
    d = Document(in_bio)
    props, pcs, cur = [], {}, None

    for el in d.element.body:
        if el.tag.endswith("tbl"):
            tbl = Table(el, d)
            for r in tbl.rows:
                if r.cells[0].text.strip() == "Company":
                    cur = r.cells[1].text.strip()
        elif el.tag.endswith("p"):
            p = Paragraph(el, d)
            txt = p.text.strip()
            if txt.lower().startswith("proposal"):
                buf, cm = [txt], {cur} if cur else set()
                idx2 = d.element.body.index(el) + 1
                while idx2 < len(d.element.body):
                    sib = d.element.body[idx2]
                    if not sib.tag.endswith("p"): break
                    sp = Paragraph(sib, d)
                    st = sp.text.rstrip()
                    if not st.strip() or st.lower().startswith("proposal"): break
                    buf.append(st)
                    if cur: cm.add(cur)
                    idx2 += 1
                bl = "\n".join(buf)
                props.append(bl)
                pcs[bl] = cm.copy()

    r = Document()
    r.add_heading("Proposal Summary", 0)

    if not props:
        r.add_paragraph("No proposals found.")
        bio = io.BytesIO()
        r.save(bio)
        bio.seek(0)
        return bio

    status_elem.text("Generating embeddings & Clustering...")
    em = TFIDFEmbedder()
    emb = em.encode(props)

    N = len(props)
    mn, mx = max(2, N // 5), max(3, N // 2)
    best_diff = float("inf")
    best_lbl = None
    for thr in np.linspace(0.2, 0.8, 13):
        try:
            hac = AgglomerativeClustering(
                n_clusters=None, metric="cosine", linkage="average",
                distance_threshold=thr, compute_full_tree=True,
            )
            lbls = hac.fit_predict(emb)
            cnt = len(set(lbls))
            diff = abs(cnt - (mn + mx) / 2)
            if diff < best_diff:
                best_diff = diff
                best_lbl = lbls
        except: pass
    lbls = best_lbl if best_lbl is not None else np.zeros(N, dtype=int)

    clusters = {}
    for i, l in enumerate(lbls):
        clusters.setdefault(l, {"idxs": [], "cm": set()})
        clusters[l]["idxs"].append(i)
        clusters[l]["cm"].update(pcs[props[i]])

    items = []
    for info in clusters.values():
        idxs = info["idxs"]
        subset = emb[idxs]
        cent = np.mean(subset, axis=0, keepdims=True)
        sims = cosine_similarity(cent, subset)[0]
        rep = props[idxs[int(np.argmax(sims))]]
        cm = sorted(info["cm"])
        items.append({"proposal": rep, "companies": cm, "count": len(cm)})

    items.sort(key=lambda x: x["count"], reverse=True)

    status_elem.text("Creating summary...")
    for it in items:
        r.add_paragraph(it["proposal"])
        r.add_paragraph(f"Supporting companies ({it['count']}): " + (", ".join(it["companies"]) if it["companies"] else "(none)"))
        r.add_paragraph("")
        
    bio = io.BytesIO()
    r.save(bio)
    bio.seek(0)
    log_func("Summary 생성 완료")
    return bio

# ==========================================
# 3. 사이드바 및 메인 화면 구성
# ==========================================
st.sidebar.title("📡 3GPP AI Analyzer")
st.sidebar.markdown("---")
page = st.sidebar.radio("메뉴 이동", ["🚀 메인 분석기", "📄 기고문 상세 분석", "📁 3GPP FTP 탐색기", "ℹ️ 소개 및 가이드"])
st.sidebar.markdown("---")
st.sidebar.info("구글 애드센스 수익 창출을 위해 최적화된 웹 애플리케이션입니다.")

# 상단 애드센스 슬롯 (빈 영역)
show_adsense()

# --- 페이지 1: 메인 분석기 ---
if page == "🚀 메인 분석기":
    st.title("🚀 3GPP 기고문 자동 분석기")
    st.write("엑셀 파일이나 링크 목록을 입력하여 각 회사의 기고문을 다운로드하고, 결론(Conclusions)을 추출 및 유사도 기반으로 요약합니다.")
    
    if not st.session_state.authenticated:
        st.info("시스템 사용을 위해 4자리 PIN 번호를 입력해주세요.")
        pin_input = st.text_input("PIN 번호", type="password", max_chars=4)
        if st.button("인증"):
            with st.spinner("네트워크 확인 및 PIN 검증 중..."):
                detect_network()
                remote_pin = fetch_remote_pin()
                if pin_input == remote_pin:
                    st.session_state.authenticated = True
                    st.rerun()
                else:
                    st.error("PIN 번호가 일치하지 않습니다.")
        st.stop()

    st.success("인증 완료 (네트워크 환경: " + ("사내망" if USE_PROXY else "외부망") + ")")
    
    input_method = st.radio("입력 방식 선택:", ("Excel 파일 업로드", "링크 텍스트 직접 입력"))
    entries = []

    if input_method == "Excel 파일 업로드":
        uploaded_file = st.file_uploader("엑셀(.xlsx) 파일 선택 (1열 docid, 3열 company 양식 준수)", type=["xlsx", "xls"])
        if uploaded_file is not None:
            entries = read_excel_from_bytes(uploaded_file)
            st.info(f"총 {len(entries)}개의 문서 링크를 인식했습니다.")
    else:
        raw_text = st.text_area("3GPP 기고문 링크들을 한 줄에 하나씩 붙여넣으세요.", height=150)
        if raw_text:
            lines = [url.strip() for url in raw_text.split('\n') if url.strip()]
            for line in lines:
                docid = line.split('/')[-1].replace('.zip', '')
                entries.append({"doc": docid, "company": "Unknown", "link": line})
            st.info(f"총 {len(entries)}개의 문서 링크를 인식했습니다.")

    if st.button("🚀 실행 (Run)", type="primary"):
        if not entries:
            st.warning("먼저 엑셀 파일을 업로드하거나 링크를 입력해주세요.")
        else:
            st.session_state.log_text = ""
            st.session_state.process_done = False
            
            status_elem = st.empty()
            progress_elem = st.progress(0)
            
            status_elem.text("기고문 다운로드 및 결론 추출 시작...")
            out1_bio = extract_all_conclusions(entries, status_elem, progress_elem, append_log)
            
            status_elem.text("자연어 처리 및 Proposal 요약 분석 시작...")
            out2_bio = parse_and_summarize(out1_bio, status_elem, append_log)
            
            status_elem.text("✅ 모든 작업이 완료되었습니다!")
            progress_elem.progress(1.0)
            
            st.session_state.out1_bio = out1_bio
            st.session_state.out2_bio = out2_bio
            st.session_state.process_done = True

    if st.session_state.process_done:
        st.markdown("---")
        st.subheader("📥 결과물 다운로드")
        
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w") as zf:
            zf.writestr("output1_conclusions.docx", st.session_state.out1_bio.getvalue())
            zf.writestr("output2_summary.docx", st.session_state.out2_bio.getvalue())
        zip_buffer.seek(0)
        
        st.download_button(
            label="📦 두 파일 한 번에 다운로드 (ZIP 압축파일)",
            data=zip_buffer,
            file_name="3gpp_results.zip",
            mime="application/zip",
            type="primary",
            use_container_width=True
        )
        
        st.write("또는 개별 파일로 다운로드 하실 수 있습니다:")
        col1, col2 = st.columns(2)
        with col1:
            st.download_button("📄 Output 1 다운로드 (Conclusions 취합)", data=st.session_state.out1_bio, file_name="output1_conclusions.docx", mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        with col2:
            st.download_button("📄 Output 2 다운로드 (Proposal 요약)", data=st.session_state.out2_bio, file_name="output2_summary.docx", mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
            
        with st.expander("실행 로그 보기"):
            st.text(st.session_state.log_text)

# --- 페이지 2: 기고문 상세 분석 ---
elif page == "📄 기고문 상세 분석":
    st.title("📄 개별 기고문 상세 분석")
    st.write("메인 분석기에서 추출된 기고문들의 원문을 브라우저에서 바로 확인하고 분석합니다.")
    
    if not st.session_state.process_done or not st.session_state.extracted_data:
        st.warning("아직 분석된 데이터가 없습니다. '메인 분석기'에서 먼저 기고문을 분석해주세요.")
    else:
        doc_list = [item["doc"] for item in st.session_state.extracted_data]
        selected_doc = st.selectbox("확인할 기고문을 선택하세요:", doc_list)
        
        for item in st.session_state.extracted_data:
            if item["doc"] == selected_doc:
                st.subheader(f"📌 {item['title']}")
                col1, col2 = st.columns(2)
                col1.write(f"**제출 회사:** {item['company']}")
                col2.markdown(f"**[원문 다운로드 링크]({item['link']})**")
                
                st.markdown("---")
                st.markdown("### 📝 추출된 결론(Conclusions) 내용")
                st.info(item["content"])
                
                st.markdown("#### AI 기반 인사이트 (Beta)")
                st.write("본문 내용을 바탕으로 자연어 처리 알고리즘이 해당 기고문의 중요 Proposal을 클러스터링하여 메인 페이지의 Output2에 반영했습니다. 자세한 지지 회사 통계는 요약 결과물을 다운로드하여 확인하시기 바랍니다.")
                break

# --- 페이지 3: 3GPP FTP 탐색기 ---
elif page == "📁 3GPP FTP 탐색기":
    st.title("📁 3GPP 공식 FTP 탐색기 및 가이드")
    st.write("이 페이지에서는 3GPP 회의의 원본 기고문들이 업로드되는 공식 FTP 서버의 다이렉트 링크와 활용 방법을 안내합니다.")
    
    st.subheader("🔗 주요 Working Group 회의록 FTP 접속")
    st.markdown("""
    * [RAN1 (물리계층) FTP 서버 바로가기](https://www.3gpp.org/ftp/tsg_ran/WG1_RL1/)
    * [RAN2 (무선 인터페이스 구조) FTP 서버 바로가기](https://www.3gpp.org/ftp/tsg_ran/WG2_RL2/)
    * [RAN3 (네트워크 아키텍처) FTP 서버 바로가기](https://www.3gpp.org/ftp/tsg_ran/WG3_IU/)
    """)
    
    st.subheader("📖 기고문 번호(TDoc) 읽는 법")
    st.write("""
    3GPP 기고문은 일반적으로 `R1-2505131` 과 같은 형태를 가집니다.
    - **R1:** RAN WG1 회의를 의미합니다.
    - **25:** 2025년에 제출되었음을 의미합니다.
    - **05131:** 해당 연도의 기고문 일련번호입니다.
    이를 통해 기고문이 언제 제출되었고 어떤 그룹에서 논의되는지 한눈에 파악할 수 있습니다.
    """)

# --- 페이지 4: 소개 및 가이드 ---
elif page == "ℹ️ 소개 및 가이드":
    st.title("ℹ️ 사이트 소개 및 이용 약관")
    
    st.header("사이트 소개 (About Us)")
    st.write("본 사이트는 통신 전문가 및 연구원들이 3GPP 회의에 제출된 방대한 양의 기고문(Contributions)을 쉽고 빠르게 분석할 수 있도록 돕기 위해 제작되었습니다. AI 기반 자연어 처리 기술을 활용하여 핵심 결론(Conclusions)만 추출하고, 유사한 제안(Proposals)을 병합해 줍니다.")
    
    st.header("이용 약관 (Terms of Service)")
    st.write("1. 본 서비스에서 제공하는 요약 결과는 원문의 텍스트 기반 추출 알고리즘에 의존하며, 100%의 정확도를 보장하지 않습니다.\n2. 공식적인 통계나 회의록은 반드시 3GPP 공식 홈페이지를 교차 검증하시기 바랍니다.\n3. 본 서비스를 이용하여 발생하는 어떠한 손실에 대해서도 운영자는 책임지지 않습니다.")
    
    st.header("개인정보처리방침 (Privacy Policy)")
    st.write("본 웹사이트는 사용자가 업로드한 엑셀 파일이나 입력한 링크 정보를 서버 하드디스크에 영구적으로 저장하지 않습니다. 모든 데이터 처리는 메모리(RAM) 상에서 이루어지며, 세션이 종료되거나 결과물을 다운로드한 후 즉시 소멸됩니다.")

# 하단 애드센스 슬롯 (빈 영역)
show_adsense()