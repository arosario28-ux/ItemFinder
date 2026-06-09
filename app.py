"""
Lost & Found Hub — Streamlit + Supabase community board.
"""

import os
import streamlit as st
import uuid
from datetime import datetime, date, timedelta
from supabase import create_client, Client

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

SUPABASE_URL = os.environ.get("SUPABASE_URL") or st.secrets.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY") or st.secrets.get("SUPABASE_KEY", "")

CATEGORIES = [
    "Electronics", "Wallet / Purse", "Keys", "Clothing",
    "Jewelry", "Bag / Backpack", "Documents / ID",
    "Pet", "Glasses / Sunglasses", "Umbrella", "Other",
]


@st.cache_resource
def get_supabase() -> Client:
    if not SUPABASE_URL or not SUPABASE_KEY:
        st.error("Missing Supabase credentials. Set SUPABASE_URL and SUPABASE_KEY in Streamlit Secrets.")
        st.stop()
    return create_client(SUPABASE_URL, SUPABASE_KEY)


def get_dev_emails(sb):
    return [r["email"] for r in sb.table("dev_users").select("email").order("created_at").execute().data]

def is_dev_email(sb, email):
    return len(sb.table("dev_users").select("id").ilike("email", email.strip()).execute().data) > 0

def add_dev_user(sb, email, name=""):
    uid = uuid.uuid4().hex[:12]
    sb.table("dev_users").insert({"id": uid, "email": email.strip().lower(), "name": name.strip(), "created_at": datetime.now().isoformat(timespec="seconds")}).execute()
    return uid

def dev_count(sb):
    return (sb.table("dev_users").select("id", count="exact").execute().count) or 0

def is_logged_in():
    return st.session_state.get("auth_role") in ("guest", "dev")

def is_dev():
    return st.session_state.get("auth_role") == "dev"

def logout():
    for k in ["auth_role", "auth_email", "auth_name", "page", "detail_id"]:
        st.session_state.pop(k, None)


def insert_item(sb, d):
    iid = uuid.uuid4().hex[:12]
    sb.table("items").insert({
        "id": iid, "item_type": d["item_type"], "title": d["title"],
        "description": d["description"], "category": d["category"],
        "location": d["location"], "date_occurred": d["date_occurred"],
        "date_posted": datetime.now().isoformat(timespec="seconds"),
        "contact_name": d["contact_name"], "contact_email": d["contact_email"],
        "contact_phone": d["contact_phone"], "photo_id": d.get("photo_id"),
        "status": "open",
    }).execute()
    return iid

def save_photo(sb, f):
    if f is None: return None
    pid = uuid.uuid4().hex[:16]
    ext = f.name.rsplit(".", 1)[-1].lower()
    path = f"{pid}.{ext}"
    mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}
    sb.storage.from_("photos").upload(path=path, file=f.getvalue(), file_options={"content-type": mime.get(ext, "application/octet-stream")})
    return path

def get_photo_url(sb, pid):
    return sb.storage.from_("photos").get_public_url(pid) if pid else None

def delete_photo(sb, pid):
    if pid:
        try: sb.storage.from_("photos").remove([pid])
        except: pass

def search_items(sb, item_type, query="", category="All", status="open", days=0):
    q = sb.table("items").select("*").eq("item_type", item_type).eq("status", status)
    if category and category != "All": q = q.eq("category", category)
    if query:
        w = f"%{query}%"
        q = q.or_(f"title.ilike.{w},description.ilike.{w},location.ilike.{w}")
    if days > 0: q = q.gte("date_posted", (datetime.now() - timedelta(days=days)).isoformat())
    return q.order("date_posted", desc=True).execute().data

def get_item(sb, iid):
    r = sb.table("items").select("*").eq("id", iid).execute()
    return r.data[0] if r.data else None

def resolve_item(sb, iid):
    sb.table("items").update({"status": "resolved"}).eq("id", iid).execute()

def reopen_item(sb, iid):
    sb.table("items").update({"status": "open"}).eq("id", iid).execute()

def delete_item(sb, iid):
    row = get_item(sb, iid)
    if row and row.get("photo_id"): delete_photo(sb, row["photo_id"])
    sb.table("items").delete().eq("id", iid).execute()

def get_potential_matches(sb, item):
    opp = "found" if item["item_type"] == "lost" else "lost"
    matches = []
    if item.get("category"):
        matches.extend(sb.table("items").select("*").eq("item_type", opp).eq("status", "open").eq("category", item["category"]).order("date_posted", desc=True).limit(10).execute().data)
    if item.get("title"):
        for w in [x for x in item["title"].lower().split() if len(x) > 3][:4]:
            wild = f"%{w}%"
            matches.extend(sb.table("items").select("*").eq("item_type", opp).eq("status", "open").or_(f"title.ilike.{wild},description.ilike.{wild}").order("date_posted", desc=True).limit(5).execute().data)
    seen = set(); out = []
    for m in matches:
        if m["id"] not in seen: seen.add(m["id"]); out.append(m)
    return out[:8]

def count_stats(sb):
    s = {}
    for t in ("lost", "found"):
        for st_ in ("open", "resolved"):
            s[f"{t}_{st_}"] = (sb.table("items").select("id", count="exact").eq("item_type", t).eq("status", st_).execute().count) or 0
    return s


def nav(page, **kw):
    st.session_state["page"] = page
    for k, v in kw.items(): st.session_state[k] = v

def go_detail(iid): nav("Detail", detail_id=iid)


def apply_styles():
    st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Playfair+Display:wght@400;600;700;800&family=DM+Sans:opsz,wght@9..40,300;9..40,400;9..40,500;9..40,600;9..40,700&display=swap');

    :root {
        --cream:       #faf8f4;
        --warm:        #f3f0ea;
        --surface:     #ffffff;
        --ink:         #1a1a2e;
        --ink2:        #6b6b80;
        --ink3:        #a0a0b0;
        --accent:      #e8532b;
        --accent-g:    rgba(232,83,43,.12);
        --red:         #c0392b;
        --red-bg:      #fef2f2;
        --green:       #1a7a4c;
        --green-bg:    #ecfdf5;
        --bdr:         #e8e4dc;
        --sh-s:        0 1px 3px rgba(26,26,46,.06);
        --sh-m:        0 4px 20px rgba(26,26,46,.08);
        --sh-l:        0 12px 40px rgba(26,26,46,.12);
        --r:           14px;
        --rs:          8px;
        --ff-d:        'Playfair Display', Georgia, serif;
        --ff-b:        'DM Sans', system-ui, sans-serif;
    }

    .stApp, .stApp > header {
        background: var(--cream) !important;
        font-family: var(--ff-b) !important;
    }
    .block-container {
        max-width: 960px !important;
        padding-top: 2rem !important;
    }

    .stApp,
    .stMainBlockContainer,
    .stMainBlockContainer p,
    .stMainBlockContainer span,
    .stMainBlockContainer div,
    .stMainBlockContainer label,
    .stMainBlockContainer li,
    .stMainBlockContainer td,
    .stMainBlockContainer th,
    .stMarkdown, .stMarkdown p, .stMarkdown span, .stMarkdown li,
    .stCaption, .stCaption p,
    .stAlert p,
    .stTextInput label, .stTextArea label,
    .stSelectbox label, .stDateInput label,
    .stFileUploader label, .stFileUploader span,
    .stRadio label, .stCheckbox label,
    [data-testid="stFormSubmitButton"] button {
        color: var(--ink) !important;
    }
    .stTextInput input, .stTextArea textarea {
        color: var(--ink) !important;
        background: var(--surface) !important;
    }
    .stSelectbox > div > div,
    .stSelectbox > div > div > div {
        color: var(--ink) !important;
        background: var(--surface) !important;
    }
    .stDateInput input {
        color: var(--ink) !important;
        background: var(--surface) !important;
    }
    .stFileUploader > div {
        background: var(--surface) !important;
    }
    .stForm {
        background: var(--surface) !important;
        border-color: var(--bdr) !important;
    }

    h1, h2, h3, .stMarkdown h1, .stMarkdown h2, .stMarkdown h3 {
        font-family: var(--ff-d) !important;
        color: var(--ink) !important;
        font-weight: 700 !important;
        letter-spacing: -0.02em !important;
    }
    p, li, label, .stMarkdown p {
        font-family: var(--ff-b) !important;
        color: var(--ink) !important;
    }

    section[data-testid="stSidebar"] {
        background: var(--ink) !important;
    }
    section[data-testid="stSidebar"] h1,
    section[data-testid="stSidebar"] h2,
    section[data-testid="stSidebar"] h3,
    section[data-testid="stSidebar"] h5 {
        color: #fff !important;
        font-family: var(--ff-d) !important;
    }
    section[data-testid="stSidebar"] p,
    section[data-testid="stSidebar"] span,
    section[data-testid="stSidebar"] label,
    section[data-testid="stSidebar"] .stMarkdown p,
    section[data-testid="stSidebar"] .stCaption p {
        color: #b0b0c0 !important;
    }
    section[data-testid="stSidebar"] .stButton > button {
        background: transparent !important;
        color: #c4c4d4 !important;
        border: 1px solid rgba(255,255,255,.08) !important;
        border-radius: var(--rs) !important;
        text-align: left !important;
        font-family: var(--ff-b) !important;
        font-weight: 500 !important;
        padding: .55rem .9rem !important;
        transition: all .2s ease !important;
    }
    section[data-testid="stSidebar"] .stButton > button:hover {
        background: rgba(255,255,255,.07) !important;
        color: #fff !important;
        transform: translateX(3px);
    }
    section[data-testid="stSidebar"] .stButton > button[kind="primary"] {
        background: var(--accent) !important;
        color: #fff !important;
        border-color: var(--accent) !important;
    }

    @keyframes fadeUp {
        from { opacity: 0; transform: translateY(16px); }
        to   { opacity: 1; transform: translateY(0); }
    }
    .anim { animation: fadeUp .45s cubic-bezier(.22,1,.36,1) both; }
    .d1 { animation-delay: 0s; }
    .d2 { animation-delay: .07s; }
    .d3 { animation-delay: .14s; }
    .d4 { animation-delay: .21s; }

    .stat {
        background: var(--surface);
        border: 1px solid var(--bdr);
        border-radius: var(--r);
        padding: 1.2rem .8rem;
        text-align: center;
        box-shadow: var(--sh-s);
        transition: all .25s cubic-bezier(.22,1,.36,1);
        overflow: hidden;
        position: relative;
    }
    .stat::after {
        content: ''; position: absolute; bottom: 0; left: 0; right: 0;
        height: 3px; background: var(--bdr); transition: background .25s;
    }
    .stat:hover { transform: translateY(-3px); box-shadow: var(--sh-m); }
    .stat:hover::after { background: var(--accent); }
    .stat .n {
        font-family: var(--ff-d); font-size: 2.2rem; font-weight: 800;
        line-height: 1; letter-spacing: -0.03em;
    }
    .stat .l {
        font-size: .72rem; font-weight: 600; color: var(--ink2);
        margin-top: .3rem; text-transform: uppercase; letter-spacing: .08em;
    }
    .s-red .n  { color: var(--red); }
    .s-grn .n  { color: var(--green); }
    .s-acc .n  { color: var(--accent); }
    .s-mut .n  { color: var(--ink2); }

    .card {
        background: var(--surface);
        border: 1px solid var(--bdr);
        border-radius: var(--r);
        padding: 1rem 1.2rem;
        margin-bottom: .6rem;
        box-shadow: var(--sh-s);
        transition: all .2s cubic-bezier(.22,1,.36,1);
        border-left: 4px solid var(--bdr);
    }
    .card.c-lost { border-left-color: var(--red); }
    .card.c-found { border-left-color: var(--green); }
    .card:hover {
        transform: translateY(-2px);
        box-shadow: var(--sh-m);
    }
    .card h4 {
        margin: .3rem 0 .2rem;
        font-family: var(--ff-d) !important;
        font-size: 1rem; font-weight: 600;
        color: var(--ink);
    }
    .card .meta { font-size: .78rem; color: var(--ink2); }

    .badge {
        display: inline-block; padding: .15rem .6rem; border-radius: 20px;
        font-size: .65rem; font-weight: 600; font-family: var(--ff-b);
        text-transform: uppercase; letter-spacing: .06em;
    }
    .b-lost { background: var(--red-bg); color: var(--red); }
    .b-found { background: var(--green-bg); color: var(--green); }
    .b-resolved { background: #f1f1f1; color: var(--ink2); }

    .sec {
        font-family: var(--ff-d); font-size: 1.05rem; font-weight: 700;
        color: var(--ink); border-bottom: 2px solid var(--bdr);
        padding-bottom: .4rem; margin-bottom: .8rem;
    }

    .bc { font-size: .8rem; color: var(--ink2) !important; margin-bottom: .5rem; }
    .bc b { color: var(--ink) !important; }
    .bc .sep { margin: 0 .35rem; color: var(--ink3); }

    .hero { text-align: center; padding: 3.5rem 1rem 2rem; }
    .hero h1 {
        font-family: var(--ff-d) !important; font-size: 3rem !important;
        font-weight: 800 !important; letter-spacing: -0.03em !important;
        color: var(--ink) !important; line-height: 1.1;
    }
    .hero .hi { color: var(--accent); }
    .hero .sub {
        font-size: 1.1rem; color: var(--ink2);
        margin-top: .5rem; margin-bottom: 2rem;
    }
    .lcard {
        background: var(--surface); border: 2px solid var(--bdr);
        border-radius: 18px; padding: 2rem 1.5rem; text-align: center;
        transition: all .3s cubic-bezier(.22,1,.36,1);
        overflow: hidden; position: relative;
    }
    .lcard::before {
        content: ''; position: absolute; top: 0; left: 0; right: 0;
        height: 3px; background: linear-gradient(90deg, var(--accent), #f5a623);
        opacity: 0; transition: opacity .3s;
    }
    .lcard:hover {
        border-color: var(--accent); box-shadow: var(--sh-l);
        transform: translateY(-4px);
    }
    .lcard:hover::before { opacity: 1; }
    .lcard .ico { font-size: 2.5rem; display: block; margin-bottom: .5rem; }
    .lcard h3 {
        font-family: var(--ff-d) !important; font-size: 1.25rem !important;
        color: var(--ink) !important; margin: .2rem 0 !important;
    }
    .lcard p { font-size: .85rem; color: var(--ink2); line-height: 1.5; margin-bottom: 1rem; }

    .rbadge {
        display: inline-block; padding: .18rem .6rem; border-radius: 20px;
        font-size: .68rem; font-weight: 600; text-transform: uppercase;
        letter-spacing: .05em;
    }
    .rb-dev { background: rgba(232,83,43,.18); color: #ff6b4a; }
    .rb-guest { background: rgba(255,255,255,.08); color: #999; }

    .stMainBlockContainer .stButton > button {
        font-family: var(--ff-b) !important; font-weight: 600 !important;
        border-radius: var(--rs) !important;
        transition: all .2s cubic-bezier(.22,1,.36,1) !important;
    }
    .stMainBlockContainer .stButton > button:hover {
        transform: translateY(-1px) !important;
    }
    .stMainBlockContainer .stButton > button[kind="primary"] {
        background: var(--accent) !important; border-color: var(--accent) !important;
        color: #fff !important;
    }

    .stTextInput input, .stTextArea textarea, .stSelectbox > div > div {
        font-family: var(--ff-b) !important;
        border-radius: var(--rs) !important;
    }

    .ph-empty {
        background: var(--warm); border: 2px dashed var(--bdr);
        border-radius: var(--r); height: 180px;
        display: flex; align-items: center; justify-content: center;
        color: var(--ink3); font-size: 2.2rem;
    }

    .dtitle {
        font-family: var(--ff-d); font-size: 1.8rem; font-weight: 700;
        color: var(--ink); letter-spacing: -0.02em; line-height: 1.2;
        margin: .4rem 0 .8rem;
    }

    hr { border-color: var(--bdr) !important; opacity: .4 !important; }
    ::-webkit-scrollbar { width: 5px; }
    ::-webkit-scrollbar-thumb { background: var(--bdr); border-radius: 3px; }

    .stMainBlockContainer [data-testid="stCaptionContainer"],
    .stMainBlockContainer [data-testid="stCaptionContainer"] p {
        color: var(--ink2) !important;
    }
    .stMainBlockContainer .stAlert [data-testid="stMarkdownContainer"] p {
        color: inherit !important;
    }
    .stMainBlockContainer strong, .stMainBlockContainer b {
        color: var(--ink) !important;
    }
    .stMainBlockContainer code {
        color: var(--ink2) !important;
        background: var(--warm) !important;
    }
    .stMainBlockContainer [data-testid="stFormSubmitButton"] button {
        color: var(--ink) !important;
    }
    .stMainBlockContainer [data-testid="stFormSubmitButton"] button p {
        color: inherit !important;
    }
    header[data-testid="stHeader"] {
        background: var(--cream) !important;
    }
    .stApp [data-testid="stToolbar"] {
        background: var(--cream) !important;
    }
    </style>
    """, unsafe_allow_html=True)


def badge_html(t, s="open"):
    if s == "resolved": return '<span class="badge b-resolved">Resolved</span>'
    c = "b-lost" if t == "lost" else "b-found"
    return f'<span class="badge {c}">{"Lost" if t == "lost" else "Found"}</span>'

def card_html(row, i=0):
    d = f" · {row['date_occurred']}" if row.get("date_occurred") else ""
    loc = f" · 📍 {row['location']}" if row.get("location") else ""
    cat = f" · {row['category']}" if row.get("category") else ""
    cc = "c-lost" if row["item_type"] == "lost" else "c-found"
    dc = f"d{min(i+1, 4)}"
    return f'<div class="card {cc} anim {dc}">{badge_html(row["item_type"], row["status"])}<h4>{row["title"]}</h4><div class="meta">{cat}{loc}{d}</div></div>'

def breadcrumb(*parts):
    items = [p[0] for p in parts[:-1]] + [f"<b>{parts[-1][0]}</b>"]
    st.markdown(f'<div class="bc">{"<span class=sep>›</span>".join(items)}</div>', unsafe_allow_html=True)

def show_photo(sb, pid, **kw):
    if pid:
        url = get_photo_url(sb, pid)
        if url: st.image(url, **kw); return
    st.markdown('<div class="ph-empty">📷</div>', unsafe_allow_html=True)


def page_landing(sb):
    st.markdown('<div class="hero"><h1>Lost & <span class="hi">Found</span> Hub</h1><div class="sub">Reuniting people with what matters</div></div>', unsafe_allow_html=True)

    c1, c2 = st.columns(2, gap="large")
    with c1:
        st.markdown('<div class="lcard anim d1"><span class="ico">👤</span><h3>Guest Access</h3><p>Browse and post items freely. No sign-up needed.</p></div>', unsafe_allow_html=True)
        if st.button("Continue as Guest", use_container_width=True, type="secondary"):
            st.session_state["auth_role"] = "guest"; st.session_state["page"] = "Home"; st.rerun()
    with c2:
        st.markdown('<div class="lcard anim d2"><span class="ico">🔐</span><h3>Dev Login</h3><p>Manage the board: resolve, reopen, and delete items.</p></div>', unsafe_allow_html=True)
        if st.button("Log in with Email", use_container_width=True, type="primary"):
            st.session_state["show_login"] = True; st.rerun()

    if st.session_state.get("show_login"):
        st.markdown("---")
        st.markdown("### 🔐 Dev Login")
        has = dev_count(sb) > 0
        if has:
            with st.form("login"):
                email = st.text_input("Email", placeholder="you@example.com")
                go = st.form_submit_button("Log In", use_container_width=True)
            if go:
                if not email.strip(): st.error("Enter an email.")
                elif is_dev_email(sb, email):
                    st.session_state.update(auth_role="dev", auth_email=email.strip().lower(), page="Home")
                    st.session_state.pop("show_login", None); st.rerun()
                else: st.error("Email not registered.")
        else:
            st.info("No devs yet. Create the first account below.")
            with st.form("setup"):
                name = st.text_input("Name")
                email = st.text_input("Email")
                confirm = st.text_input("Confirm email")
                go = st.form_submit_button("Create Account", use_container_width=True)
            if go:
                if not email.strip(): st.error("Enter an email.")
                elif email.strip().lower() != confirm.strip().lower(): st.error("Emails don't match.")
                else:
                    add_dev_user(sb, email, name)
                    st.session_state.update(auth_role="dev", auth_email=email.strip().lower(), page="Home")
                    st.session_state.pop("show_login", None); st.rerun()
        if st.button("← Back"): st.session_state.pop("show_login", None); st.rerun()


def render_sidebar(sb):
    st.sidebar.markdown("# Lost & Found")
    if is_dev():
        em = st.session_state.get("auth_email", "")
        st.sidebar.markdown(f'<span class="rbadge rb-dev">Dev</span> &nbsp; <span style="color:#b0b0c0;font-size:.82rem">{em}</span>', unsafe_allow_html=True)
    else:
        st.sidebar.markdown('<span class="rbadge rb-guest">Guest</span>', unsafe_allow_html=True)

    st.sidebar.markdown("---")
    cur = st.session_state.get("page", "Home")
    pages = [("🏠", "Home"), ("🔴", "Report Lost"), ("🟢", "Report Found"), ("📋", "Browse Lost"), ("📋", "Browse Found")]
    for icon, name in pages:
        st.sidebar.button(f"{icon}  {name}", key=f"n_{name}", on_click=nav, args=(name,), use_container_width=True, type="primary" if cur == name else "secondary")

    st.sidebar.markdown("---")
    if st.sidebar.button("🚪  Log Out", use_container_width=True): logout(); st.rerun()

    if is_dev():
        st.sidebar.markdown("---")
        st.sidebar.markdown("##### 🛠 Dev Tools")
        new_email = st.sidebar.text_input("Add a dev email", key="add_dev_email", placeholder="email@example.com", label_visibility="collapsed")
        if st.sidebar.button("Add Dev", key="btn_add"):
            if new_email.strip() and "@" in new_email:
                if is_dev_email(sb, new_email): st.sidebar.warning("Already exists.")
                else: add_dev_user(sb, new_email); st.rerun()


def page_home(sb):
    st.markdown("## Dashboard")

    stats = count_stats(sb)
    c1, c2, c3, c4 = st.columns(4)
    with c1: st.markdown(f'<div class="stat s-red anim d1"><div class="n">{stats["lost_open"]}</div><div class="l">Lost</div></div>', unsafe_allow_html=True)
    with c2: st.markdown(f'<div class="stat s-grn anim d2"><div class="n">{stats["found_open"]}</div><div class="l">Found</div></div>', unsafe_allow_html=True)
    with c3: st.markdown(f'<div class="stat s-acc anim d3"><div class="n">{stats["lost_open"]+stats["found_open"]}</div><div class="l">Active</div></div>', unsafe_allow_html=True)
    with c4: st.markdown(f'<div class="stat s-mut anim d4"><div class="n">{stats["lost_resolved"]+stats["found_resolved"]}</div><div class="l">Reunited</div></div>', unsafe_allow_html=True)

    st.markdown("")
    st.markdown("")

    col_l, col_r = st.columns(2, gap="medium")
    with col_l:
        st.markdown('<div class="sec">🔴 Recently Lost</div>', unsafe_allow_html=True)
        lost = search_items(sb, "lost")[:5]
        if lost:
            for i, row in enumerate(lost):
                st.markdown(card_html(row, i), unsafe_allow_html=True)
                st.button("View →", key=f"hl_{row['id']}", on_click=go_detail, args=(row["id"],))
        else: st.info("No lost items yet.")

    with col_r:
        st.markdown('<div class="sec">🟢 Recently Found</div>', unsafe_allow_html=True)
        found = search_items(sb, "found")[:5]
        if found:
            for i, row in enumerate(found):
                st.markdown(card_html(row, i), unsafe_allow_html=True)
                st.button("View →", key=f"hf_{row['id']}", on_click=go_detail, args=(row["id"],))
        else: st.info("No found items yet.")


def page_post(sb, item_type):
    verb = "Lost" if item_type == "lost" else "Found"
    emoji = "🔴" if item_type == "lost" else "🟢"
    breadcrumb(("Home", "Home"), (f"Report {verb}", None))
    st.markdown(f"## {emoji} Report a {verb} Item")

    with st.form(f"post_{item_type}", clear_on_submit=True):
        title = st.text_input("Item title *", placeholder="e.g. Black leather wallet")
        description = st.text_area("Description", placeholder="Brand, color, distinguishing features...", height=100)

        c1, c2, c3 = st.columns(3)
        with c1: category = st.selectbox("Category", CATEGORIES)
        with c2: date_occurred = st.date_input(f"Date {verb.lower()}", value=date.today(), max_value=date.today())
        with c3: location = st.text_input("Location", placeholder="e.g. Main Street")

        photo = st.file_uploader("Photo (optional)", type=["png", "jpg", "jpeg", "webp"])

        st.markdown("**Contact Info**")
        cc1, cc2, cc3 = st.columns(3)
        with cc1: contact_name = st.text_input("Name")
        with cc2: contact_email = st.text_input("Email")
        with cc3: contact_phone = st.text_input("Phone")

        submitted = st.form_submit_button(f"📌  Post {verb} Item", use_container_width=True)

    if submitted:
        if not title.strip(): st.error("Please enter a title."); return
        pid = save_photo(sb, photo)
        iid = insert_item(sb, dict(item_type=item_type, title=title.strip(), description=description.strip(), category=category, location=location.strip(), date_occurred=str(date_occurred), contact_name=contact_name.strip(), contact_email=contact_email.strip(), contact_phone=contact_phone.strip(), photo_id=pid))
        st.success(f"Posted! ID: {iid}")
        st.balloons()


def page_browse(sb, item_type):
    verb = "Lost" if item_type == "lost" else "Found"
    emoji = "🔴" if item_type == "lost" else "🟢"
    breadcrumb(("Home", "Home"), (f"Browse {verb}", None))
    st.markdown(f"## {emoji} Browse {verb} Items")

    c1, c2, c3, c4 = st.columns([3, 2, 2, 2])
    with c1: q = st.text_input("Search", placeholder="Keyword or location...", label_visibility="collapsed")
    with c2: cat = st.selectbox("Category", ["All"] + CATEGORIES, label_visibility="collapsed")
    with c3: tf = st.selectbox("Time", ["All time", "7 days", "30 days", "90 days"], label_visibility="collapsed")
    with c4: sf = st.selectbox("Status", ["open", "resolved"], label_visibility="collapsed")

    dm = {"All time": 0, "7 days": 7, "30 days": 30, "90 days": 90}
    results = search_items(sb, item_type, q, cat, sf, dm[tf])
    st.caption(f"{len(results)} result{'s' if len(results) != 1 else ''}")

    if not results: st.info(f"No {verb.lower()} items match."); return

    cols = st.columns(2)
    for i, row in enumerate(results):
        with cols[i % 2]:
            st.markdown(card_html(row, i), unsafe_allow_html=True)
            if row.get("photo_id"):
                url = get_photo_url(sb, row["photo_id"])
                if url: st.image(url, width=180)
            st.button("View →", key=f"b_{item_type}_{row['id']}", on_click=go_detail, args=(row["id"],))


def page_detail(sb):
    iid = st.session_state.get("detail_id")
    if not iid:
        st.warning("No item selected.")
        if st.button("← Home"): nav("Home"); st.rerun()
        return

    row = get_item(sb, iid)
    if not row:
        st.error("Item not found.")
        if st.button("← Home"): nav("Home"); st.rerun()
        return

    verb = "Lost" if row["item_type"] == "lost" else "Found"
    breadcrumb(("Home", "Home"), (f"Browse {verb}", f"Browse {verb}"), (row["title"], None))

    if st.button(f"← Browse {verb}"): nav(f"Browse {verb}"); st.rerun()

    st.markdown(f'{badge_html(row["item_type"], row["status"])}', unsafe_allow_html=True)
    st.markdown(f'<div class="dtitle">{row["title"]}</div>', unsafe_allow_html=True)

    ci, cx = st.columns([1, 2])
    with ci: show_photo(sb, row.get("photo_id"), use_container_width=True)
    with cx:
        info = []
        if row.get("category"): info.append(f"**Category:** {row['category']}")
        if row.get("location"): info.append(f"**Location:** {row['location']}")
        if row.get("date_occurred"): info.append(f"**Date:** {row['date_occurred']}")
        info.append(f"**Posted:** {row['date_posted'][:16].replace('T', ' ')}")
        st.markdown("  \n".join(info))

    if row.get("description"):
        st.markdown("")
        st.markdown(f"**Description**  \n{row['description']}")

    contact = []
    if row.get("contact_name"): contact.append(f"**{row['contact_name']}**")
    if row.get("contact_email"): contact.append(row['contact_email'])
    if row.get("contact_phone"): contact.append(row['contact_phone'])
    if contact:
        st.markdown("")
        st.markdown(f"**Contact:** {' · '.join(contact)}")

    st.markdown("---")
    if is_dev():
        c1, c2, _ = st.columns([1, 1, 3])
        with c1:
            if row["status"] == "open":
                if st.button("✅ Resolve"): resolve_item(sb, iid); st.rerun()
            else:
                if st.button("🔄 Reopen"): reopen_item(sb, iid); st.rerun()
        with c2:
            if st.button("🗑️ Delete"): delete_item(sb, iid); nav("Home"); st.rerun()
    else:
        st.caption("🔒 Only devs can manage item status.")

    matches = get_potential_matches(sb, row)
    if matches:
        opp = "Found" if row["item_type"] == "lost" else "Lost"
        st.markdown(f"### Potential Matches ({opp})")
        for i, m in enumerate(matches):
            st.markdown(card_html(m, i), unsafe_allow_html=True)
            st.button("View", key=f"m_{m['id']}", on_click=go_detail, args=(m["id"],))


def main():
    st.set_page_config(page_title="Lost & Found Hub", page_icon="🔎", layout="centered")
    apply_styles()
    sb = get_supabase()

    if not is_logged_in(): page_landing(sb); return
    if "page" not in st.session_state: st.session_state["page"] = "Home"

    render_sidebar(sb)
    p = st.session_state["page"]
    {"Home": lambda: page_home(sb),
     "Report Lost": lambda: page_post(sb, "lost"),
     "Report Found": lambda: page_post(sb, "found"),
     "Browse Lost": lambda: page_browse(sb, "lost"),
     "Browse Found": lambda: page_browse(sb, "found"),
     "Detail": lambda: page_detail(sb),
    }.get(p, lambda: page_home(sb))()

if __name__ == "__main__":
    main()
