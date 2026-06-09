"""
Lost & Found Hub — a Streamlit community board for posting and finding lost items.
Backed by Supabase (Postgres + Storage).
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

PAGES = ["Home", "Report Lost", "Report Found", "Browse Lost", "Browse Found"]
PAGE_ICONS = {"Home": "🏠", "Report Lost": "🔴", "Report Found": "🟢",
              "Browse Lost": "📋", "Browse Found": "📋", "Detail": "📄"}


@st.cache_resource
def get_supabase() -> Client:
    if not SUPABASE_URL or not SUPABASE_KEY:
        st.error("Missing Supabase credentials. Set SUPABASE_URL and SUPABASE_KEY in Streamlit Secrets.")
        st.stop()
    return create_client(SUPABASE_URL, SUPABASE_KEY)


# ── Auth helpers ────────────────────────────────────────────────────────────────

def get_dev_emails(sb): return [r["email"] for r in sb.table("dev_users").select("email").order("created_at").execute().data]

def is_dev_email(sb, email):
    return len(sb.table("dev_users").select("id").ilike("email", email.strip()).execute().data) > 0

def add_dev_user(sb, email, name=""):
    uid = uuid.uuid4().hex[:12]
    sb.table("dev_users").insert({"id": uid, "email": email.strip().lower(), "name": name.strip(), "created_at": datetime.now().isoformat(timespec="seconds")}).execute()
    return uid

def dev_count(sb):
    resp = sb.table("dev_users").select("id", count="exact").execute()
    return resp.count or 0

def is_logged_in(): return st.session_state.get("auth_role") in ("guest", "dev")
def is_dev(): return st.session_state.get("auth_role") == "dev"

def logout():
    for key in ["auth_role", "auth_email", "auth_name", "page", "detail_id"]:
        st.session_state.pop(key, None)


# ── Item CRUD ───────────────────────────────────────────────────────────────────

def insert_item(sb, data):
    item_id = uuid.uuid4().hex[:12]
    sb.table("items").insert({
        "id": item_id, "item_type": data["item_type"], "title": data["title"],
        "description": data["description"], "category": data["category"],
        "location": data["location"], "date_occurred": data["date_occurred"],
        "date_posted": datetime.now().isoformat(timespec="seconds"),
        "contact_name": data["contact_name"], "contact_email": data["contact_email"],
        "contact_phone": data["contact_phone"], "photo_id": data.get("photo_id"), "status": "open",
    }).execute()
    return item_id

def save_photo(sb, uploaded_file):
    if uploaded_file is None: return None
    photo_id = uuid.uuid4().hex[:16]
    ext = uploaded_file.name.rsplit(".", 1)[-1].lower()
    storage_path = f"{photo_id}.{ext}"
    mime_map = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}
    sb.storage.from_("photos").upload(path=storage_path, file=uploaded_file.getvalue(), file_options={"content-type": mime_map.get(ext, "application/octet-stream")})
    return storage_path

def get_photo_url(sb, photo_id):
    if not photo_id: return None
    return sb.storage.from_("photos").get_public_url(photo_id)

def delete_photo(sb, photo_id):
    if not photo_id: return
    try: sb.storage.from_("photos").remove([photo_id])
    except Exception: pass

def search_items(sb, item_type, query="", category="All", status="open", days=0):
    q = sb.table("items").select("*").eq("item_type", item_type).eq("status", status)
    if category and category != "All": q = q.eq("category", category)
    if query:
        wild = f"%{query}%"
        q = q.or_(f"title.ilike.{wild},description.ilike.{wild},location.ilike.{wild}")
    if days > 0: q = q.gte("date_posted", (datetime.now() - timedelta(days=days)).isoformat())
    return q.order("date_posted", desc=True).execute().data

def get_item(sb, item_id):
    resp = sb.table("items").select("*").eq("id", item_id).execute()
    return resp.data[0] if resp.data else None

def resolve_item(sb, item_id): sb.table("items").update({"status": "resolved"}).eq("id", item_id).execute()
def reopen_item(sb, item_id): sb.table("items").update({"status": "open"}).eq("id", item_id).execute()

def delete_item(sb, item_id):
    row = get_item(sb, item_id)
    if row and row.get("photo_id"): delete_photo(sb, row["photo_id"])
    sb.table("items").delete().eq("id", item_id).execute()

def get_potential_matches(sb, item):
    opposite = "found" if item["item_type"] == "lost" else "lost"
    matches = []
    if item.get("category"):
        matches.extend(sb.table("items").select("*").eq("item_type", opposite).eq("status", "open").eq("category", item["category"]).order("date_posted", desc=True).limit(10).execute().data)
    if item.get("title"):
        for word in [w for w in item["title"].lower().split() if len(w) > 3][:4]:
            wild = f"%{word}%"
            matches.extend(sb.table("items").select("*").eq("item_type", opposite).eq("status", "open").or_(f"title.ilike.{wild},description.ilike.{wild}").order("date_posted", desc=True).limit(5).execute().data)
    seen = set(); unique = []
    for m in matches:
        if m["id"] not in seen: seen.add(m["id"]); unique.append(m)
    return unique[:8]

def count_stats(sb):
    stats = {"lost_open": 0, "lost_resolved": 0, "found_open": 0, "found_resolved": 0}
    for t in ("lost", "found"):
        for s in ("open", "resolved"):
            resp = sb.table("items").select("id", count="exact").eq("item_type", t).eq("status", s).execute()
            stats[f"{t}_{s}"] = resp.count or 0
    return stats


# ── Navigation ──────────────────────────────────────────────────────────────────

def navigate_to(page, **kwargs):
    st.session_state["page"] = page
    for k, v in kwargs.items(): st.session_state[k] = v

def go_to_detail(item_id): navigate_to("Detail", detail_id=item_id)


# ── UI: Styles ──────────────────────────────────────────────────────────────────

def apply_styles():
    st.markdown("""
    <style>
    /* ── Fonts ───────────────────────────────────────────────── */
    @import url('https://fonts.googleapis.com/css2?family=Playfair+Display:ital,wght@0,400;0,600;0,700;0,800;1,400&family=DM+Sans:ital,opsz,wght@0,9..40,300;0,9..40,400;0,9..40,500;0,9..40,600;0,9..40,700;1,9..40,400&display=swap');

    :root {
        --bg-cream:    #faf8f4;
        --bg-warm:     #f3f0ea;
        --surface:     #ffffff;
        --ink:         #1a1a2e;
        --ink-muted:   #6b6b80;
        --ink-faint:   #a0a0b0;
        --accent:      #e8532b;
        --accent-glow: rgba(232, 83, 43, .12);
        --lost-red:    #c0392b;
        --lost-bg:     #fef2f2;
        --found-green: #1a7a4c;
        --found-bg:    #ecfdf5;
        --resolved-bg: #f1f1f1;
        --border:      #e8e4dc;
        --shadow-sm:   0 1px 3px rgba(26,26,46,.06);
        --shadow-md:   0 4px 20px rgba(26,26,46,.08);
        --shadow-lg:   0 12px 40px rgba(26,26,46,.12);
        --radius:      14px;
        --radius-sm:   8px;
        --font-display: 'Playfair Display', Georgia, serif;
        --font-body:   'DM Sans', system-ui, sans-serif;
    }

    /* ── Global overrides ────────────────────────────────────── */
    .stApp {
        background: var(--bg-cream) !important;
        font-family: var(--font-body) !important;
        color: var(--ink) !important;
    }
    .stApp::before {
        content: '';
        position: fixed; top: 0; left: 0; right: 0; bottom: 0;
        background-image: url("data:image/svg+xml,%3Csvg viewBox='0 0 256 256' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='.85' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)' opacity='.03'/%3E%3C/svg%3E");
        background-size: 200px;
        pointer-events: none;
        z-index: 0;
    }
    .block-container { max-width: 960px !important; position: relative; z-index: 1; }

    /* ── Typography overrides ────────────────────────────────── */
    h1, h2, h3, .stMarkdown h1, .stMarkdown h2, .stMarkdown h3 {
        font-family: var(--font-display) !important;
        color: var(--ink) !important;
        font-weight: 700 !important;
        letter-spacing: -0.02em !important;
    }
    h1, .stMarkdown h1 { font-size: 2.4rem !important; }
    h2, .stMarkdown h2 { font-size: 1.65rem !important; }
    p, li, span, div, label, .stMarkdown p, .stTextInput label, .stSelectbox label {
        font-family: var(--font-body) !important;
    }

    /* ── Sidebar ─────────────────────────────────────────────── */
    section[data-testid="stSidebar"] {
        background: var(--ink) !important;
        border-right: none !important;
    }
    section[data-testid="stSidebar"] * {
        color: #d4d4e0 !important;
    }
    section[data-testid="stSidebar"] h1, section[data-testid="stSidebar"] h2,
    section[data-testid="stSidebar"] h3, section[data-testid="stSidebar"] h5 {
        color: #fff !important;
        font-family: var(--font-display) !important;
    }
    section[data-testid="stSidebar"] .stButton > button {
        background: transparent !important;
        color: #c4c4d4 !important;
        border: 1px solid rgba(255,255,255,.08) !important;
        border-radius: var(--radius-sm) !important;
        text-align: left !important;
        font-family: var(--font-body) !important;
        font-weight: 500 !important;
        font-size: .9rem !important;
        padding: .6rem .9rem !important;
        transition: all .2s ease !important;
    }
    section[data-testid="stSidebar"] .stButton > button:hover {
        background: rgba(255,255,255,.08) !important;
        color: #fff !important;
        border-color: rgba(255,255,255,.15) !important;
        transform: translateX(3px);
    }
    section[data-testid="stSidebar"] .stButton > button[kind="primary"] {
        background: var(--accent) !important;
        color: #fff !important;
        border-color: var(--accent) !important;
        box-shadow: 0 0 20px rgba(232,83,43,.25) !important;
    }

    /* ── Animations ──────────────────────────────────────────── */
    @keyframes fadeInUp {
        from { opacity: 0; transform: translateY(18px); }
        to   { opacity: 1; transform: translateY(0); }
    }
    @keyframes fadeIn {
        from { opacity: 0; }
        to   { opacity: 1; }
    }
    @keyframes shimmer {
        0%   { background-position: -200% center; }
        100% { background-position: 200% center; }
    }
    @keyframes pulseGlow {
        0%, 100% { box-shadow: 0 0 0 0 var(--accent-glow); }
        50%      { box-shadow: 0 0 0 12px transparent; }
    }
    .animate-in {
        animation: fadeInUp .5s cubic-bezier(.22,1,.36,1) both;
    }
    .animate-in-1 { animation-delay: 0s; }
    .animate-in-2 { animation-delay: .08s; }
    .animate-in-3 { animation-delay: .16s; }
    .animate-in-4 { animation-delay: .24s; }

    /* ── Stat cards ──────────────────────────────────────────── */
    .stat-card {
        background: var(--surface);
        border: 1px solid var(--border);
        border-radius: var(--radius);
        padding: 1.4rem 1rem;
        text-align: center;
        box-shadow: var(--shadow-sm);
        transition: all .25s cubic-bezier(.22,1,.36,1);
        position: relative;
        overflow: hidden;
    }
    .stat-card::after {
        content: '';
        position: absolute; bottom: 0; left: 0; right: 0;
        height: 3px;
        background: var(--border);
        transition: background .25s;
    }
    .stat-card:hover {
        transform: translateY(-4px);
        box-shadow: var(--shadow-md);
    }
    .stat-card:hover::after { background: var(--accent); }
    .stat-card .num {
        font-family: var(--font-display);
        font-size: 2.6rem;
        font-weight: 800;
        line-height: 1;
        letter-spacing: -0.03em;
    }
    .stat-card .label {
        font-family: var(--font-body);
        font-size: .78rem;
        font-weight: 500;
        color: var(--ink-muted);
        margin-top: .35rem;
        text-transform: uppercase;
        letter-spacing: .08em;
    }
    .stat-lost .num   { color: var(--lost-red); }
    .stat-found .num  { color: var(--found-green); }
    .stat-active .num { color: var(--accent); }
    .stat-done .num   { color: var(--ink-muted); }

    /* ── Item cards ──────────────────────────────────────────── */
    .item-card {
        background: var(--surface);
        border: 1px solid var(--border);
        border-radius: var(--radius);
        padding: 1.1rem 1.3rem;
        margin-bottom: .7rem;
        box-shadow: var(--shadow-sm);
        transition: all .25s cubic-bezier(.22,1,.36,1);
        position: relative;
    }
    .item-card::before {
        content: '';
        position: absolute; top: 0; left: 0; bottom: 0;
        width: 4px;
        border-radius: var(--radius) 0 0 var(--radius);
        background: var(--border);
        transition: background .25s;
    }
    .item-card.card-lost::before { background: var(--lost-red); }
    .item-card.card-found::before { background: var(--found-green); }
    .item-card:hover {
        transform: translateY(-3px) translateX(2px);
        box-shadow: var(--shadow-md);
        border-color: transparent;
    }
    .item-card h4 {
        margin: .35rem 0 .25rem;
        font-family: var(--font-display) !important;
        font-size: 1.05rem;
        font-weight: 600;
        color: var(--ink);
        letter-spacing: -0.01em;
    }
    .item-card .meta {
        font-size: .8rem;
        color: var(--ink-muted);
        font-weight: 400;
    }

    /* ── Badges ──────────────────────────────────────────────── */
    .badge {
        display: inline-block;
        padding: .2rem .65rem;
        border-radius: 20px;
        font-size: .68rem;
        font-weight: 600;
        font-family: var(--font-body);
        text-transform: uppercase;
        letter-spacing: .07em;
    }
    .badge-lost     { background: var(--lost-bg); color: var(--lost-red); }
    .badge-found    { background: var(--found-bg); color: var(--found-green); }
    .badge-resolved { background: var(--resolved-bg); color: var(--ink-muted); }

    /* ── Section headers ─────────────────────────────────────── */
    .section-head {
        font-family: var(--font-display);
        font-size: 1.1rem;
        font-weight: 700;
        color: var(--ink);
        border-bottom: 2px solid var(--border);
        padding-bottom: .45rem;
        margin-bottom: .9rem;
        letter-spacing: -0.01em;
    }

    /* ── Breadcrumb ──────────────────────────────────────────── */
    .breadcrumb {
        font-size: .82rem;
        color: var(--ink-faint);
        margin-bottom: .6rem;
        font-weight: 400;
    }
    .breadcrumb span { color: var(--border); margin: 0 .4rem; }

    /* ── Landing page ────────────────────────────────────────── */
    .landing-hero {
        text-align: center;
        padding: 4rem 1rem 2.5rem;
        animation: fadeIn .6s ease both;
    }
    .landing-hero h1 {
        font-family: var(--font-display) !important;
        font-size: 3.4rem !important;
        font-weight: 800 !important;
        letter-spacing: -0.035em !important;
        color: var(--ink) !important;
        margin-bottom: .2rem;
        line-height: 1.1;
    }
    .landing-hero .accent { color: var(--accent); }
    .landing-hero .subtitle {
        font-family: var(--font-body);
        font-size: 1.15rem;
        color: var(--ink-muted);
        margin-top: .6rem;
        margin-bottom: 2.5rem;
        font-weight: 400;
    }
    .landing-card {
        background: var(--surface);
        border: 2px solid var(--border);
        border-radius: 20px;
        padding: 2.2rem 1.8rem;
        text-align: center;
        transition: all .3s cubic-bezier(.22,1,.36,1);
        position: relative;
        overflow: hidden;
    }
    .landing-card::before {
        content: '';
        position: absolute; top: 0; left: 0; right: 0;
        height: 4px;
        background: linear-gradient(90deg, var(--accent), #f5a623);
        opacity: 0;
        transition: opacity .3s;
    }
    .landing-card:hover {
        border-color: var(--accent);
        box-shadow: var(--shadow-lg);
        transform: translateY(-6px);
    }
    .landing-card:hover::before { opacity: 1; }
    .landing-card .card-icon {
        font-size: 2.8rem;
        margin-bottom: .7rem;
        display: block;
    }
    .landing-card h3 {
        font-family: var(--font-display) !important;
        font-size: 1.35rem !important;
        margin: .3rem 0 !important;
        color: var(--ink) !important;
    }
    .landing-card p {
        font-size: .88rem;
        color: var(--ink-muted);
        margin-bottom: 1.2rem;
        line-height: 1.55;
    }

    /* ── Role badges ─────────────────────────────────────────── */
    .role-badge {
        display: inline-block;
        padding: .22rem .7rem;
        border-radius: 20px;
        font-size: .7rem;
        font-weight: 600;
        font-family: var(--font-body);
        text-transform: uppercase;
        letter-spacing: .06em;
    }
    .role-dev  { background: rgba(232,83,43,.15) !important; color: var(--accent) !important; }
    .role-guest { background: rgba(255,255,255,.1) !important; color: #aaa !important; }

    /* ── Buttons (main content) ──────────────────────────────── */
    .stMainBlockContainer .stButton > button {
        font-family: var(--font-body) !important;
        font-weight: 600 !important;
        border-radius: var(--radius-sm) !important;
        transition: all .2s cubic-bezier(.22,1,.36,1) !important;
        font-size: .85rem !important;
        letter-spacing: .01em !important;
    }
    .stMainBlockContainer .stButton > button:hover {
        transform: translateY(-2px) !important;
        box-shadow: var(--shadow-sm) !important;
    }
    .stMainBlockContainer .stButton > button[kind="primary"] {
        background: var(--accent) !important;
        border-color: var(--accent) !important;
        color: #fff !important;
    }
    .stMainBlockContainer .stButton > button[kind="primary"]:hover {
        box-shadow: 0 4px 16px rgba(232,83,43,.3) !important;
    }

    /* ── Form inputs ─────────────────────────────────────────── */
    .stTextInput > div > div > input,
    .stTextArea > div > div > textarea,
    .stSelectbox > div > div {
        font-family: var(--font-body) !important;
        border-radius: var(--radius-sm) !important;
        border-color: var(--border) !important;
    }
    .stTextInput > div > div > input:focus,
    .stTextArea > div > div > textarea:focus {
        border-color: var(--accent) !important;
        box-shadow: 0 0 0 3px var(--accent-glow) !important;
    }

    /* ── Dividers ────────────────────────────────────────────── */
    hr { border-color: var(--border) !important; opacity: .5 !important; }

    /* ── Photo placeholder ───────────────────────────────────── */
    .photo-placeholder {
        background: linear-gradient(135deg, var(--bg-warm), var(--bg-cream));
        border: 2px dashed var(--border);
        border-radius: var(--radius);
        height: 200px;
        display: flex;
        align-items: center;
        justify-content: center;
        color: var(--ink-faint);
        font-size: 2.5rem;
    }

    /* ── Detail page header ──────────────────────────────────── */
    .detail-title {
        font-family: var(--font-display);
        font-size: 2rem;
        font-weight: 700;
        color: var(--ink);
        letter-spacing: -0.025em;
        line-height: 1.2;
        margin: .5rem 0 1rem;
    }

    /* ── Info alert override ─────────────────────────────────── */
    .stAlert {
        border-radius: var(--radius-sm) !important;
        font-family: var(--font-body) !important;
    }

    /* ── Scrollbar ───────────────────────────────────────────── */
    ::-webkit-scrollbar { width: 6px; }
    ::-webkit-scrollbar-track { background: transparent; }
    ::-webkit-scrollbar-thumb { background: var(--border); border-radius: 3px; }
    ::-webkit-scrollbar-thumb:hover { background: var(--ink-muted); }

    /* ── Toast override ──────────────────────────────────────── */
    .stToast { font-family: var(--font-body) !important; }
    </style>
    """, unsafe_allow_html=True)


def badge(item_type, status="open"):
    if status == "resolved": return '<span class="badge badge-resolved">Resolved</span>'
    cls = "badge-lost" if item_type == "lost" else "badge-found"
    label = "Lost" if item_type == "lost" else "Found"
    return f'<span class="badge {cls}">{label}</span>'


def item_card_html(row, delay_idx=0):
    date_str = f" &middot; {row['date_occurred']}" if row.get("date_occurred") else ""
    loc = f" &middot; 📍 {row['location']}" if row.get("location") else ""
    cat = f" &middot; {row['category']}" if row.get("category") else ""
    card_cls = "card-lost" if row["item_type"] == "lost" else "card-found"
    anim_cls = f"animate-in animate-in-{min(delay_idx + 1, 4)}"
    return f"""
    <div class="item-card {card_cls} {anim_cls}">
        {badge(row['item_type'], row['status'])}
        <h4>{row['title']}</h4>
        <div class="meta">{cat}{loc}{date_str}</div>
    </div>
    """


def render_breadcrumb(*parts):
    crumbs = [label for label, _ in parts[:-1]]
    crumbs.append(f"<strong>{parts[-1][0]}</strong>")
    sep = ' <span>›</span> '
    st.markdown(f'<div class="breadcrumb">{sep.join(crumbs)}</div>', unsafe_allow_html=True)


def show_photo(sb, photo_id, width=None, use_container_width=False):
    if photo_id:
        url = get_photo_url(sb, photo_id)
        if url:
            st.image(url, width=width, use_container_width=use_container_width)
            return
    st.markdown('<div class="photo-placeholder">📷</div>', unsafe_allow_html=True)


# ── Landing ─────────────────────────────────────────────────────────────────────

def page_landing(sb):
    st.markdown("""
    <div class="landing-hero">
        <h1>Lost & <span class="accent">Found</span> Hub</h1>
        <div class="subtitle">Reuniting people with what matters. Post, search, and recover lost items in your community.</div>
    </div>
    """, unsafe_allow_html=True)

    col_guest, col_dev = st.columns(2, gap="large")

    with col_guest:
        st.markdown("""
        <div class="landing-card animate-in animate-in-1">
            <span class="card-icon">👤</span>
            <h3>Guest Access</h3>
            <p>Browse lost &amp; found items and post your own listings. No account needed.</p>
        </div>
        """, unsafe_allow_html=True)
        if st.button("Continue as Guest", use_container_width=True, type="secondary"):
            st.session_state["auth_role"] = "guest"
            st.session_state["page"] = "Home"
            st.rerun()

    with col_dev:
        st.markdown("""
        <div class="landing-card animate-in animate-in-2">
            <span class="card-icon">🔐</span>
            <h3>Dev Login</h3>
            <p>Manage the board: mark items as found, reunited, reopen or delete posts.</p>
        </div>
        """, unsafe_allow_html=True)
        if st.button("Log in with Email", use_container_width=True, type="primary"):
            st.session_state["show_login"] = True
            st.rerun()

    if st.session_state.get("show_login"):
        st.markdown("---")
        st.markdown("### 🔐 Dev Login")
        has_devs = dev_count(sb) > 0

        if has_devs:
            st.caption("Enter your registered dev email to log in.")
            with st.form("login_form"):
                email = st.text_input("Email address", placeholder="you@example.com")
                submitted = st.form_submit_button("Log In", use_container_width=True)
            if submitted:
                if not email.strip(): st.error("Please enter an email address.")
                elif is_dev_email(sb, email):
                    st.session_state.update({"auth_role": "dev", "auth_email": email.strip().lower(), "page": "Home"})
                    st.session_state.pop("show_login", None)
                    st.rerun()
                else: st.error("This email is not registered as a dev account.")
        else:
            st.info("No dev accounts exist yet. Create the first one below to become the admin.")
            with st.form("setup_form"):
                name = st.text_input("Your name", placeholder="Jane Smith")
                email = st.text_input("Email address", placeholder="you@example.com")
                confirm = st.text_input("Confirm email", placeholder="you@example.com")
                submitted = st.form_submit_button("Create Dev Account", use_container_width=True)
            if submitted:
                if not email.strip(): st.error("Please enter an email address.")
                elif email.strip().lower() != confirm.strip().lower(): st.error("Emails do not match.")
                elif "@" not in email or "." not in email.split("@")[-1]: st.error("Please enter a valid email address.")
                else:
                    add_dev_user(sb, email, name)
                    st.session_state.update({"auth_role": "dev", "auth_email": email.strip().lower(), "auth_name": name.strip(), "page": "Home"})
                    st.session_state.pop("show_login", None)
                    st.rerun()

        if st.button("← Back"):
            st.session_state.pop("show_login", None)
            st.rerun()


# ── Sidebar ─────────────────────────────────────────────────────────────────────

def render_sidebar(sb):
    st.sidebar.markdown("# Lost & Found")

    if is_dev():
        email = st.session_state.get("auth_email", "")
        st.sidebar.markdown(f'<span class="role-badge role-dev">🔐 Dev</span> &nbsp; {email}', unsafe_allow_html=True)
    else:
        st.sidebar.markdown('<span class="role-badge role-guest">👤 Guest</span>', unsafe_allow_html=True)

    st.sidebar.markdown("---")
    current = st.session_state.get("page", "Home")

    for page_name in PAGES:
        icon = PAGE_ICONS[page_name]
        st.sidebar.button(f"{icon}  {page_name}", key=f"nav_{page_name}", on_click=navigate_to, args=(page_name,), use_container_width=True, type="primary" if current == page_name else "secondary")

    if is_dev():
        st.sidebar.markdown("---")
        st.sidebar.markdown("##### Dev Tools")
        with st.sidebar.expander("Manage Dev Accounts"):
            for em in get_dev_emails(sb): st.sidebar.text(f"• {em}")
            st.sidebar.markdown("")
            new_email = st.sidebar.text_input("Add dev email", key="add_dev_email", placeholder="new-dev@example.com")
            if st.sidebar.button("Add Dev", key="btn_add_dev"):
                if new_email.strip() and "@" in new_email:
                    if is_dev_email(sb, new_email): st.sidebar.warning("Already registered.")
                    else: add_dev_user(sb, new_email); st.sidebar.success(f"Added {new_email.strip().lower()}"); st.rerun()
                else: st.sidebar.error("Enter a valid email.")

    st.sidebar.markdown("---")
    if st.sidebar.button("🚪 Log Out", use_container_width=True): logout(); st.rerun()
    st.sidebar.caption("Dev accounts can manage item statuses." if not is_dev() else "You have full board management access.")


# ── Pages ───────────────────────────────────────────────────────────────────────

def page_home(sb):
    st.markdown("## Dashboard")

    qa1, qa2, qa3, qa4 = st.columns(4)
    with qa1:
        if st.button("🔴 Report Lost", use_container_width=True, key="qa_rl"): navigate_to("Report Lost"); st.rerun()
    with qa2:
        if st.button("🟢 Report Found", use_container_width=True, key="qa_rf"): navigate_to("Report Found"); st.rerun()
    with qa3:
        if st.button("📋 Browse Lost", use_container_width=True, key="qa_bl"): navigate_to("Browse Lost"); st.rerun()
    with qa4:
        if st.button("📋 Browse Found", use_container_width=True, key="qa_bf"): navigate_to("Browse Found"); st.rerun()

    st.markdown("")
    stats = count_stats(sb)
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.markdown(f'<div class="stat-card stat-lost animate-in animate-in-1"><div class="num">{stats["lost_open"]}</div><div class="label">Items Lost</div></div>', unsafe_allow_html=True)
    with c2:
        st.markdown(f'<div class="stat-card stat-found animate-in animate-in-2"><div class="num">{stats["found_open"]}</div><div class="label">Items Found</div></div>', unsafe_allow_html=True)
    with c3:
        st.markdown(f'<div class="stat-card stat-active animate-in animate-in-3"><div class="num">{stats["lost_open"]+stats["found_open"]}</div><div class="label">Active</div></div>', unsafe_allow_html=True)
    with c4:
        st.markdown(f'<div class="stat-card stat-done animate-in animate-in-4"><div class="num">{stats["lost_resolved"]+stats["found_resolved"]}</div><div class="label">Reunited ✦</div></div>', unsafe_allow_html=True)

    st.markdown("---")
    col_l, col_r = st.columns(2)

    with col_l:
        st.markdown('<div class="section-head">🔴 Recently Lost</div>', unsafe_allow_html=True)
        lost = search_items(sb, "lost")[:5]
        if lost:
            for i, row in enumerate(lost):
                st.markdown(item_card_html(row, i), unsafe_allow_html=True)
                if st.button("View details", key=f"home_l_{row['id']}", on_click=go_to_detail, args=(row["id"],)): st.rerun()
        else: st.info("No lost items posted yet.")

    with col_r:
        st.markdown('<div class="section-head">🟢 Recently Found</div>', unsafe_allow_html=True)
        found = search_items(sb, "found")[:5]
        if found:
            for i, row in enumerate(found):
                st.markdown(item_card_html(row, i), unsafe_allow_html=True)
                if st.button("View details", key=f"home_f_{row['id']}", on_click=go_to_detail, args=(row["id"],)): st.rerun()
        else: st.info("No found items posted yet.")


def page_post(sb, item_type):
    emoji = "🔴" if item_type == "lost" else "🟢"
    verb = "Lost" if item_type == "lost" else "Found"
    render_breadcrumb(("Home", "Home"), (f"Report {verb}", None))
    st.markdown(f"## {emoji} Report a {verb} Item")
    st.caption(f"Fill in the details below to post a {verb.lower()} item to the community board.")

    with st.form(f"post_{item_type}", clear_on_submit=True):
        title = st.text_input("Item title *", placeholder="e.g. Black leather wallet")
        description = st.text_area("Description", placeholder="Distinguishing features, brand, color, contents...", height=120)
        c1, c2 = st.columns(2)
        with c1: category = st.selectbox("Category", CATEGORIES)
        with c2: date_occurred = st.date_input(f"Date {verb.lower()}", value=date.today(), max_value=date.today())
        location = st.text_input("Location", placeholder="e.g. Central Park near the fountain")
        photo = st.file_uploader("Photo (optional)", type=["png", "jpg", "jpeg", "webp"])
        st.markdown("#### Your Contact Info")
        cc1, cc2, cc3 = st.columns(3)
        with cc1: contact_name = st.text_input("Name")
        with cc2: contact_email = st.text_input("Email")
        with cc3: contact_phone = st.text_input("Phone")
        submitted = st.form_submit_button(f"📌  Post {verb} Item", use_container_width=True)

    if submitted:
        if not title.strip(): st.error("Please enter an item title."); return
        photo_id = save_photo(sb, photo)
        item_id = insert_item(sb, {"item_type": item_type, "title": title.strip(), "description": description.strip(), "category": category, "location": location.strip(), "date_occurred": str(date_occurred), "contact_name": contact_name.strip(), "contact_email": contact_email.strip(), "contact_phone": contact_phone.strip(), "photo_id": photo_id})
        st.success(f"Item posted! (ID: {item_id})")
        st.balloons()
        vc1, vc2, _ = st.columns([1, 1, 2])
        with vc1:
            if st.button("📄 View your post"): go_to_detail(item_id); st.rerun()
        with vc2:
            if st.button(f"➕ Post another"): st.rerun()


def page_browse(sb, item_type):
    emoji = "🔴" if item_type == "lost" else "🟢"
    verb = "Lost" if item_type == "lost" else "Found"
    render_breadcrumb(("Home", "Home"), (f"Browse {verb}", None))
    st.markdown(f"## {emoji} Browse {verb} Items")

    fc1, fc2, fc3, fc4 = st.columns([3, 2, 2, 2])
    with fc1: query = st.text_input("🔍 Search", placeholder="Keyword, location...", label_visibility="collapsed")
    with fc2: cat_filter = st.selectbox("Category", ["All"] + CATEGORIES, label_visibility="collapsed")
    with fc3: time_filter = st.selectbox("Time range", ["All time", "Last 7 days", "Last 30 days", "Last 90 days"], label_visibility="collapsed")
    with fc4: status_filter = st.selectbox("Status", ["open", "resolved"], label_visibility="collapsed")

    days_map = {"All time": 0, "Last 7 days": 7, "Last 30 days": 30, "Last 90 days": 90}
    results = search_items(sb, item_type, query, cat_filter, status_filter, days_map[time_filter])
    st.caption(f"{len(results)} result{'s' if len(results) != 1 else ''}")

    if not results: st.info(f"No {verb.lower()} items match your filters."); return

    cols = st.columns(2)
    for i, row in enumerate(results):
        with cols[i % 2]:
            st.markdown(item_card_html(row, i), unsafe_allow_html=True)
            if row.get("photo_id"):
                url = get_photo_url(sb, row["photo_id"])
                if url: st.image(url, width=200)
            if st.button("View details →", key=f"browse_{item_type}_{row['id']}", on_click=go_to_detail, args=(row["id"],)): st.rerun()
            st.markdown("")


def page_detail(sb):
    item_id = st.session_state.get("detail_id")
    if not item_id:
        st.warning("No item selected.")
        if st.button("← Go to Home"): navigate_to("Home"); st.rerun()
        return

    row = get_item(sb, item_id)
    if not row:
        st.error("Item not found (it may have been deleted).")
        if st.button("← Go to Home"): navigate_to("Home"); st.rerun()
        return

    verb = "Lost" if row["item_type"] == "lost" else "Found"
    browse_page = f"Browse {verb}"
    render_breadcrumb(("Home", "Home"), (f"Browse {verb}", browse_page), (row["title"], None))

    if st.button(f"← Back to {browse_page}"): navigate_to(browse_page); st.rerun()

    st.markdown(f'{badge(row["item_type"], row["status"])} &nbsp; <code style="font-size:.75rem;color:var(--ink-faint)">{row["id"]}</code>', unsafe_allow_html=True)
    st.markdown(f'<div class="detail-title">{row["title"]}</div>', unsafe_allow_html=True)

    col_img, col_info = st.columns([1, 2])
    with col_img: show_photo(sb, row.get("photo_id"), use_container_width=True)
    with col_info:
        if row.get("category"): st.markdown(f"**Category:** {row['category']}")
        if row.get("location"): st.markdown(f"**Location:** {row['location']}")
        if row.get("date_occurred"): st.markdown(f"**Date {verb.lower()}:** {row['date_occurred']}")
        st.markdown(f"**Posted:** {row['date_posted'][:16].replace('T', ' ')}")

    if row.get("description"):
        st.markdown("### Description")
        st.markdown(row["description"])

    if row.get("contact_name") or row.get("contact_email") or row.get("contact_phone"):
        st.markdown("### Contact")
        parts = []
        if row.get("contact_name"): parts.append(f"**Name:** {row['contact_name']}")
        if row.get("contact_email"): parts.append(f"**Email:** {row['contact_email']}")
        if row.get("contact_phone"): parts.append(f"**Phone:** {row['contact_phone']}")
        st.markdown(" &nbsp;|&nbsp; ".join(parts), unsafe_allow_html=True)

    st.markdown("---")
    if is_dev():
        ac1, ac2, _ = st.columns([1, 1, 3])
        with ac1:
            if row["status"] == "open":
                if st.button("✅ Mark as Resolved"): resolve_item(sb, item_id); st.rerun()
            else:
                if st.button("🔄 Reopen"): reopen_item(sb, item_id); st.rerun()
        with ac2:
            if st.button("🗑️ Delete Post"): delete_item(sb, item_id); st.toast("Post deleted."); navigate_to("Home"); st.rerun()
    else:
        st.caption("🔒 Only dev accounts can mark items as resolved or delete posts." if row["status"] == "open" else "🔒 This item has been marked as resolved by a dev.")

    matches = get_potential_matches(sb, row)
    if matches:
        opposite = "Found" if row["item_type"] == "lost" else "Lost"
        st.markdown(f"### 🔗 Potential Matches ({opposite} Items)")
        st.caption("These items share a similar category or keywords.")
        for i, m in enumerate(matches):
            st.markdown(item_card_html(m, i), unsafe_allow_html=True)
            if st.button("View", key=f"match_{m['id']}", on_click=go_to_detail, args=(m["id"],)): st.rerun()


# ── Main ────────────────────────────────────────────────────────────────────────

def main():
    st.set_page_config(page_title="Lost & Found Hub", page_icon="🔎", layout="centered")
    apply_styles()
    sb = get_supabase()

    if not is_logged_in(): page_landing(sb); return
    if "page" not in st.session_state: st.session_state["page"] = "Home"

    render_sidebar(sb)
    p = st.session_state["page"]
    if   p == "Home":         page_home(sb)
    elif p == "Report Lost":  page_post(sb, "lost")
    elif p == "Report Found": page_post(sb, "found")
    elif p == "Browse Lost":  page_browse(sb, "lost")
    elif p == "Browse Found": page_browse(sb, "found")
    elif p == "Detail":       page_detail(sb)
    else:                     page_home(sb)

if __name__ == "__main__":
    main()
