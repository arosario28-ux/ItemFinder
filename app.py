"""
Lost & Found Hub — a Streamlit community board for posting and finding lost items.
Backed by Supabase (Postgres + Storage).
"""

import os
import streamlit as st
import uuid
from datetime import datetime, date, timedelta
from supabase import create_client, Client

# Load .env locally; skip gracefully on Streamlit Cloud
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ── Supabase client ─────────────────────────────────────────────────────────────

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
        st.error(
            "Missing Supabase credentials. "
            "Set SUPABASE_URL and SUPABASE_KEY in your .env file or Streamlit Secrets."
        )
        st.stop()
    return create_client(SUPABASE_URL, SUPABASE_KEY)


# ── Auth helpers ────────────────────────────────────────────────────────────────

def get_dev_emails(sb: Client) -> list[str]:
    resp = sb.table("dev_users").select("email").order("created_at").execute()
    return [r["email"] for r in resp.data]


def is_dev_email(sb: Client, email: str) -> bool:
    resp = (
        sb.table("dev_users")
        .select("id")
        .ilike("email", email.strip())
        .execute()
    )
    return len(resp.data) > 0


def add_dev_user(sb: Client, email: str, name: str = "") -> str:
    uid = uuid.uuid4().hex[:12]
    sb.table("dev_users").insert({
        "id": uid,
        "email": email.strip().lower(),
        "name": name.strip(),
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }).execute()
    return uid


def dev_count(sb: Client) -> int:
    resp = sb.table("dev_users").select("id", count="exact").execute()
    return resp.count or 0


def is_logged_in() -> bool:
    return st.session_state.get("auth_role") in ("guest", "dev")


def is_dev() -> bool:
    return st.session_state.get("auth_role") == "dev"


def logout():
    for key in ["auth_role", "auth_email", "auth_name", "page", "detail_id"]:
        st.session_state.pop(key, None)


# ── Item CRUD ───────────────────────────────────────────────────────────────────

def insert_item(sb: Client, data: dict) -> str:
    item_id = uuid.uuid4().hex[:12]
    sb.table("items").insert({
        "id": item_id,
        "item_type": data["item_type"],
        "title": data["title"],
        "description": data["description"],
        "category": data["category"],
        "location": data["location"],
        "date_occurred": data["date_occurred"],
        "date_posted": datetime.now().isoformat(timespec="seconds"),
        "contact_name": data["contact_name"],
        "contact_email": data["contact_email"],
        "contact_phone": data["contact_phone"],
        "photo_id": data.get("photo_id"),
        "status": "open",
    }).execute()
    return item_id


def save_photo(sb: Client, uploaded_file) -> str | None:
    if uploaded_file is None:
        return None
    photo_id = uuid.uuid4().hex[:16]
    ext = uploaded_file.name.rsplit(".", 1)[-1].lower()
    storage_path = f"{photo_id}.{ext}"
    file_bytes = uploaded_file.getvalue()

    mime_map = {"jpg": "image/jpeg", "jpeg": "image/jpeg",
                "png": "image/png", "webp": "image/webp"}
    content_type = mime_map.get(ext, "application/octet-stream")

    sb.storage.from_("photos").upload(
        path=storage_path,
        file=file_bytes,
        file_options={"content-type": content_type},
    )
    return storage_path


def get_photo_url(sb: Client, photo_id: str) -> str | None:
    if not photo_id:
        return None
    return sb.storage.from_("photos").get_public_url(photo_id)


def delete_photo(sb: Client, photo_id: str):
    if not photo_id:
        return
    try:
        sb.storage.from_("photos").remove([photo_id])
    except Exception:
        pass


def search_items(sb: Client, item_type: str, query: str = "",
                 category: str = "All", status: str = "open", days: int = 0):
    q = (
        sb.table("items")
        .select("*")
        .eq("item_type", item_type)
        .eq("status", status)
    )

    if category and category != "All":
        q = q.eq("category", category)

    if query:
        wild = f"%{query}%"
        q = q.or_(
            f"title.ilike.{wild},"
            f"description.ilike.{wild},"
            f"location.ilike.{wild}"
        )

    if days > 0:
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        q = q.gte("date_posted", cutoff)

    q = q.order("date_posted", desc=True)
    resp = q.execute()
    return resp.data


def get_item(sb: Client, item_id: str):
    resp = sb.table("items").select("*").eq("id", item_id).execute()
    return resp.data[0] if resp.data else None


def resolve_item(sb: Client, item_id: str):
    sb.table("items").update({"status": "resolved"}).eq("id", item_id).execute()


def reopen_item(sb: Client, item_id: str):
    sb.table("items").update({"status": "open"}).eq("id", item_id).execute()


def delete_item(sb: Client, item_id: str):
    row = get_item(sb, item_id)
    if row and row.get("photo_id"):
        delete_photo(sb, row["photo_id"])
    sb.table("items").delete().eq("id", item_id).execute()


def get_potential_matches(sb: Client, item: dict):
    opposite = "found" if item["item_type"] == "lost" else "lost"
    matches = []

    if item.get("category"):
        resp = (
            sb.table("items")
            .select("*")
            .eq("item_type", opposite)
            .eq("status", "open")
            .eq("category", item["category"])
            .order("date_posted", desc=True)
            .limit(10)
            .execute()
        )
        matches.extend(resp.data)

    if item.get("title"):
        words = [w for w in item["title"].lower().split() if len(w) > 3]
        for word in words[:4]:
            wild = f"%{word}%"
            resp = (
                sb.table("items")
                .select("*")
                .eq("item_type", opposite)
                .eq("status", "open")
                .or_(f"title.ilike.{wild},description.ilike.{wild}")
                .order("date_posted", desc=True)
                .limit(5)
                .execute()
            )
            matches.extend(resp.data)

    seen = set()
    unique = []
    for m in matches:
        if m["id"] not in seen:
            seen.add(m["id"])
            unique.append(m)
    return unique[:8]


def count_stats(sb: Client) -> dict:
    stats = {"lost_open": 0, "lost_resolved": 0, "found_open": 0, "found_resolved": 0}
    for item_type in ("lost", "found"):
        for status in ("open", "resolved"):
            resp = (
                sb.table("items")
                .select("id", count="exact")
                .eq("item_type", item_type)
                .eq("status", status)
                .execute()
            )
            stats[f"{item_type}_{status}"] = resp.count or 0
    return stats


# ── Navigation helpers ──────────────────────────────────────────────────────────

def navigate_to(page: str, **kwargs):
    st.session_state["page"] = page
    for k, v in kwargs.items():
        st.session_state[k] = v


def go_to_detail(item_id: str):
    navigate_to("Detail", detail_id=item_id)


# ── UI Helpers ──────────────────────────────────────────────────────────────────

def apply_styles():
    st.markdown("""
    <style>
    .block-container { max-width: 960px; }

    .stat-card {
        background: linear-gradient(135deg, #f8f9fc, #eef1f8);
        border-radius: 14px;
        padding: 1.3rem 1.5rem;
        text-align: center;
        border: 1px solid #dfe3ec;
    }
    .stat-card .num {
        font-size: 2.4rem;
        font-weight: 700;
        line-height: 1.1;
    }
    .stat-card .label {
        font-size: .85rem;
        color: #5a6270;
        margin-top: .25rem;
    }
    .stat-lost .num   { color: #d94f4f; }
    .stat-found .num  { color: #2e8b57; }
    .stat-closed .num { color: #6c7a89; }

    .item-card {
        background: #fff;
        border: 1px solid #e2e5eb;
        border-radius: 12px;
        padding: 1rem 1.2rem;
        margin-bottom: .8rem;
        transition: box-shadow .15s;
    }
    .item-card:hover { box-shadow: 0 4px 16px rgba(0,0,0,.07); }
    .item-card h4 { margin: 0 0 .3rem; }
    .item-card .meta { font-size: .82rem; color: #777; }

    .badge {
        display: inline-block;
        padding: .15rem .55rem;
        border-radius: 6px;
        font-size: .75rem;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: .03em;
    }
    .badge-lost   { background: #fde8e8; color: #c0392b; }
    .badge-found  { background: #e8f5e9; color: #27763d; }
    .badge-resolved { background: #eee; color: #888; }

    .section-head {
        font-size: 1.05rem;
        font-weight: 600;
        color: #3a3f47;
        border-bottom: 2px solid #e2e5eb;
        padding-bottom: .35rem;
        margin-bottom: .8rem;
    }

    .breadcrumb {
        font-size: .85rem;
        color: #888;
        margin-bottom: .5rem;
    }
    .breadcrumb span { color: #bbb; margin: 0 .35rem; }

    .landing-hero {
        text-align: center;
        padding: 3rem 1rem 2rem;
    }
    .landing-hero h1 {
        font-size: 2.8rem;
        margin-bottom: .3rem;
    }
    .landing-hero .subtitle {
        font-size: 1.15rem;
        color: #666;
        margin-bottom: 2rem;
    }
    .landing-card {
        background: #fff;
        border: 2px solid #e2e5eb;
        border-radius: 16px;
        padding: 2rem 1.5rem;
        text-align: center;
        transition: border-color .15s, box-shadow .15s;
    }
    .landing-card:hover {
        border-color: #a0b4d0;
        box-shadow: 0 6px 24px rgba(0,0,0,.07);
    }
    .landing-card .card-icon {
        font-size: 2.5rem;
        margin-bottom: .6rem;
    }
    .landing-card h3 { margin: .3rem 0; }
    .landing-card p {
        font-size: .9rem;
        color: #777;
        margin-bottom: 1rem;
    }

    .role-badge {
        display: inline-block;
        padding: .2rem .6rem;
        border-radius: 6px;
        font-size: .75rem;
        font-weight: 600;
        text-transform: uppercase;
    }
    .role-dev  { background: #e8eaf6; color: #3949ab; }
    .role-guest { background: #f5f5f5; color: #888; }
    </style>
    """, unsafe_allow_html=True)


def badge(item_type: str, status: str = "open") -> str:
    if status == "resolved":
        return '<span class="badge badge-resolved">Resolved</span>'
    cls = "badge-lost" if item_type == "lost" else "badge-found"
    label = "Lost" if item_type == "lost" else "Found"
    return f'<span class="badge {cls}">{label}</span>'


def item_card_html(row: dict) -> str:
    date_str = ""
    if row.get("date_occurred"):
        date_str = f" &middot; {row['date_occurred']}"
    loc = f" &middot; 📍 {row['location']}" if row.get("location") else ""
    cat = f" &middot; {row['category']}" if row.get("category") else ""
    return f"""
    <div class="item-card">
        {badge(row['item_type'], row['status'])}
        <h4 style="margin-top:.4rem">{row['title']}</h4>
        <div class="meta">{cat}{loc}{date_str}</div>
    </div>
    """


def render_breadcrumb(*parts):
    crumbs = []
    for label, _page in parts[:-1]:
        crumbs.append(f"{label}")
    crumbs.append(f"**{parts[-1][0]}**")
    sep = ' <span>›</span> '
    st.markdown(f'<div class="breadcrumb">{sep.join(crumbs)}</div>', unsafe_allow_html=True)


def show_photo(sb: Client, photo_id: str | None, width: int | None = None,
               use_container_width: bool = False):
    if photo_id:
        url = get_photo_url(sb, photo_id)
        if url:
            st.image(url, width=width, use_container_width=use_container_width)
            return
    st.markdown(
        '<div style="background:#f0f1f4;border-radius:12px;height:200px;'
        'display:flex;align-items:center;justify-content:center;color:#aaa;'
        'font-size:2.5rem;">📷</div>',
        unsafe_allow_html=True,
    )


# ── Landing / Auth Page ─────────────────────────────────────────────────────────

def page_landing(sb: Client):
    st.markdown("""
    <div class="landing-hero">
        <h1>🔎 Lost & Found Hub</h1>
        <div class="subtitle">A community board to post and find lost items</div>
    </div>
    """, unsafe_allow_html=True)

    col_guest, col_dev = st.columns(2, gap="large")

    with col_guest:
        st.markdown("""
        <div class="landing-card">
            <div class="card-icon">👤</div>
            <h3>Guest Access</h3>
            <p>Browse lost & found items and post your own listings. No account needed.</p>
        </div>
        """, unsafe_allow_html=True)
        if st.button("Continue as Guest", use_container_width=True, type="secondary"):
            st.session_state["auth_role"] = "guest"
            st.session_state["page"] = "Home"
            st.rerun()

    with col_dev:
        st.markdown("""
        <div class="landing-card">
            <div class="card-icon">🔐</div>
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
                if not email.strip():
                    st.error("Please enter an email address.")
                elif is_dev_email(sb, email):
                    st.session_state["auth_role"] = "dev"
                    st.session_state["auth_email"] = email.strip().lower()
                    st.session_state["page"] = "Home"
                    st.session_state.pop("show_login", None)
                    st.rerun()
                else:
                    st.error(
                        "This email is not registered as a dev account. "
                        "Contact an existing dev to be added."
                    )
        else:
            st.info("No dev accounts exist yet. Create the first one below to become the admin.")
            with st.form("setup_form"):
                name = st.text_input("Your name", placeholder="Jane Smith")
                email = st.text_input("Email address", placeholder="you@example.com")
                confirm = st.text_input("Confirm email", placeholder="you@example.com")
                submitted = st.form_submit_button("Create Dev Account", use_container_width=True)

            if submitted:
                if not email.strip():
                    st.error("Please enter an email address.")
                elif email.strip().lower() != confirm.strip().lower():
                    st.error("Emails do not match.")
                elif "@" not in email or "." not in email.split("@")[-1]:
                    st.error("Please enter a valid email address.")
                else:
                    add_dev_user(sb, email, name)
                    st.session_state["auth_role"] = "dev"
                    st.session_state["auth_email"] = email.strip().lower()
                    st.session_state["auth_name"] = name.strip()
                    st.session_state["page"] = "Home"
                    st.session_state.pop("show_login", None)
                    st.success("Dev account created! You are now logged in.")
                    st.rerun()

        if st.button("← Back"):
            st.session_state.pop("show_login", None)
            st.rerun()


# ── Sidebar ─────────────────────────────────────────────────────────────────────

def render_sidebar(sb: Client):
    st.sidebar.markdown("# 🔎 Lost & Found Hub")

    if is_dev():
        email = st.session_state.get("auth_email", "")
        st.sidebar.markdown(
            f'<span class="role-badge role-dev">🔐 Dev</span> &nbsp; {email}',
            unsafe_allow_html=True,
        )
    else:
        st.sidebar.markdown(
            '<span class="role-badge role-guest">👤 Guest</span>',
            unsafe_allow_html=True,
        )

    st.sidebar.markdown("---")

    current = st.session_state.get("page", "Home")

    for page_name in PAGES:
        icon = PAGE_ICONS[page_name]
        is_active = (current == page_name)
        label = f"{icon}  {page_name}"
        st.sidebar.button(
            label,
            key=f"nav_{page_name}",
            on_click=navigate_to,
            args=(page_name,),
            use_container_width=True,
            type="primary" if is_active else "secondary",
        )

    if is_dev():
        st.sidebar.markdown("---")
        st.sidebar.markdown("##### Dev Tools")
        with st.sidebar.expander("Manage Dev Accounts"):
            existing = get_dev_emails(sb)
            for em in existing:
                st.sidebar.text(f"• {em}")
            st.sidebar.markdown("")
            new_email = st.sidebar.text_input("Add dev email", key="add_dev_email",
                                               placeholder="new-dev@example.com")
            if st.sidebar.button("Add Dev", key="btn_add_dev"):
                if new_email.strip() and "@" in new_email:
                    if is_dev_email(sb, new_email):
                        st.sidebar.warning("Already registered.")
                    else:
                        add_dev_user(sb, new_email)
                        st.sidebar.success(f"Added {new_email.strip().lower()}")
                        st.rerun()
                else:
                    st.sidebar.error("Enter a valid email.")

    st.sidebar.markdown("---")

    if st.sidebar.button("🚪 Log Out", use_container_width=True):
        logout()
        st.rerun()

    if not is_dev():
        st.sidebar.caption("Tip: Log in as a dev to manage item statuses.")
    else:
        st.sidebar.caption("You can mark items as resolved, reopen, or delete posts.")


# ── Pages ───────────────────────────────────────────────────────────────────────

def page_home(sb: Client):
    st.markdown("## 🏠 Dashboard")

    qa1, qa2, qa3, qa4 = st.columns(4)
    with qa1:
        if st.button("🔴 Report Lost", use_container_width=True, key="qa_rl"):
            navigate_to("Report Lost"); st.rerun()
    with qa2:
        if st.button("🟢 Report Found", use_container_width=True, key="qa_rf"):
            navigate_to("Report Found"); st.rerun()
    with qa3:
        if st.button("📋 Browse Lost", use_container_width=True, key="qa_bl"):
            navigate_to("Browse Lost"); st.rerun()
    with qa4:
        if st.button("📋 Browse Found", use_container_width=True, key="qa_bf"):
            navigate_to("Browse Found"); st.rerun()

    st.markdown("")

    stats = count_stats(sb)
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.markdown(f"""<div class="stat-card stat-lost">
            <div class="num">{stats['lost_open']}</div>
            <div class="label">Items Lost</div></div>""", unsafe_allow_html=True)
    with c2:
        st.markdown(f"""<div class="stat-card stat-found">
            <div class="num">{stats['found_open']}</div>
            <div class="label">Items Found</div></div>""", unsafe_allow_html=True)
    with c3:
        total_open = stats['lost_open'] + stats['found_open']
        st.markdown(f"""<div class="stat-card">
            <div class="num" style="color:#4a7fc1">{total_open}</div>
            <div class="label">Active Posts</div></div>""", unsafe_allow_html=True)
    with c4:
        resolved = stats['lost_resolved'] + stats['found_resolved']
        st.markdown(f"""<div class="stat-card stat-closed">
            <div class="num">{resolved}</div>
            <div class="label">Reunited 🎉</div></div>""", unsafe_allow_html=True)

    st.markdown("---")

    col_l, col_r = st.columns(2)

    with col_l:
        st.markdown('<div class="section-head">🔴 Recently Lost</div>', unsafe_allow_html=True)
        lost = search_items(sb, "lost")[:5]
        if lost:
            for row in lost:
                st.markdown(item_card_html(row), unsafe_allow_html=True)
                if st.button("View details", key=f"home_l_{row['id']}",
                             on_click=go_to_detail, args=(row["id"],)):
                    st.rerun()
        else:
            st.info("No lost items posted yet.")

    with col_r:
        st.markdown('<div class="section-head">🟢 Recently Found</div>', unsafe_allow_html=True)
        found = search_items(sb, "found")[:5]
        if found:
            for row in found:
                st.markdown(item_card_html(row), unsafe_allow_html=True)
                if st.button("View details", key=f"home_f_{row['id']}",
                             on_click=go_to_detail, args=(row["id"],)):
                    st.rerun()
        else:
            st.info("No found items posted yet.")


def page_post(sb: Client, item_type: str):
    emoji = "🔴" if item_type == "lost" else "🟢"
    verb = "Lost" if item_type == "lost" else "Found"

    render_breadcrumb(("Home", "Home"), (f"Report {verb}", None))
    st.markdown(f"## {emoji} Report a {verb} Item")
    st.caption(f"Fill in the details below to post a {verb.lower()} item to the community board.")

    with st.form(f"post_{item_type}", clear_on_submit=True):
        title = st.text_input("Item title *", placeholder="e.g. Black leather wallet")
        description = st.text_area(
            "Description",
            placeholder="Distinguishing features, brand, color, contents...",
            height=120,
        )
        c1, c2 = st.columns(2)
        with c1:
            category = st.selectbox("Category", CATEGORIES)
        with c2:
            date_occurred = st.date_input(
                f"Date {verb.lower()}",
                value=date.today(),
                max_value=date.today(),
            )
        location = st.text_input("Location",
                                  placeholder="e.g. Central Park near the fountain")
        photo = st.file_uploader("Photo (optional)",
                                  type=["png", "jpg", "jpeg", "webp"])

        st.markdown("#### Your Contact Info")
        cc1, cc2, cc3 = st.columns(3)
        with cc1:
            contact_name = st.text_input("Name")
        with cc2:
            contact_email = st.text_input("Email")
        with cc3:
            contact_phone = st.text_input("Phone")

        submitted = st.form_submit_button(f"📌  Post {verb} Item",
                                           use_container_width=True)

    if submitted:
        if not title.strip():
            st.error("Please enter an item title.")
            return

        photo_id = save_photo(sb, photo)
        item_id = insert_item(sb, {
            "item_type": item_type,
            "title": title.strip(),
            "description": description.strip(),
            "category": category,
            "location": location.strip(),
            "date_occurred": str(date_occurred),
            "contact_name": contact_name.strip(),
            "contact_email": contact_email.strip(),
            "contact_phone": contact_phone.strip(),
            "photo_id": photo_id,
        })
        st.success(f"Item posted! (ID: {item_id})")
        st.balloons()

        vc1, vc2, _ = st.columns([1, 1, 2])
        with vc1:
            if st.button("📄 View your post"):
                go_to_detail(item_id); st.rerun()
        with vc2:
            if st.button(f"➕ Post another {verb.lower()} item"):
                st.rerun()


def page_browse(sb: Client, item_type: str):
    emoji = "🔴" if item_type == "lost" else "🟢"
    verb = "Lost" if item_type == "lost" else "Found"

    render_breadcrumb(("Home", "Home"), (f"Browse {verb}", None))
    st.markdown(f"## {emoji} Browse {verb} Items")

    fc1, fc2, fc3, fc4 = st.columns([3, 2, 2, 2])
    with fc1:
        query = st.text_input("🔍 Search", placeholder="Keyword, location...",
                               label_visibility="collapsed")
    with fc2:
        cat_filter = st.selectbox("Category", ["All"] + CATEGORIES,
                                   label_visibility="collapsed")
    with fc3:
        time_filter = st.selectbox(
            "Time range",
            ["All time", "Last 7 days", "Last 30 days", "Last 90 days"],
            label_visibility="collapsed",
        )
    with fc4:
        status_filter = st.selectbox("Status", ["open", "resolved"],
                                      label_visibility="collapsed")

    days_map = {"All time": 0, "Last 7 days": 7, "Last 30 days": 30, "Last 90 days": 90}
    results = search_items(sb, item_type, query, cat_filter,
                           status_filter, days_map[time_filter])

    st.caption(f"{len(results)} result{'s' if len(results) != 1 else ''}")

    if not results:
        st.info(f"No {verb.lower()} items match your filters.")
        return

    cols = st.columns(2)
    for i, row in enumerate(results):
        with cols[i % 2]:
            st.markdown(item_card_html(row), unsafe_allow_html=True)

            if row.get("photo_id"):
                url = get_photo_url(sb, row["photo_id"])
                if url:
                    st.image(url, width=200)

            if st.button("View details →", key=f"browse_{item_type}_{row['id']}",
                         on_click=go_to_detail, args=(row["id"],)):
                st.rerun()
            st.markdown("")


def page_detail(sb: Client):
    item_id = st.session_state.get("detail_id")
    if not item_id:
        st.warning("No item selected.")
        if st.button("← Go to Home"):
            navigate_to("Home"); st.rerun()
        return

    row = get_item(sb, item_id)
    if not row:
        st.error("Item not found (it may have been deleted).")
        if st.button("← Go to Home"):
            navigate_to("Home"); st.rerun()
        return

    verb = "Lost" if row["item_type"] == "lost" else "Found"
    browse_page = f"Browse {verb}"

    render_breadcrumb(("Home", "Home"), (f"Browse {verb}", browse_page),
                      (row["title"], None))

    if st.button(f"← Back to {browse_page}"):
        navigate_to(browse_page); st.rerun()

    st.markdown(
        f"{badge(row['item_type'], row['status'])} &nbsp; `{row['id']}`",
        unsafe_allow_html=True,
    )
    st.markdown(f"# {row['title']}")

    col_img, col_info = st.columns([1, 2])

    with col_img:
        show_photo(sb, row.get("photo_id"), use_container_width=True)

    with col_info:
        if row.get("category"):
            st.markdown(f"**Category:** {row['category']}")
        if row.get("location"):
            st.markdown(f"**Location:** {row['location']}")
        if row.get("date_occurred"):
            st.markdown(f"**Date {verb.lower()}:** {row['date_occurred']}")
        st.markdown(f"**Posted:** {row['date_posted'][:16].replace('T', ' ')}")

    if row.get("description"):
        st.markdown("### Description")
        st.markdown(row["description"])

    has_contact = (row.get("contact_name") or row.get("contact_email")
                   or row.get("contact_phone"))
    if has_contact:
        st.markdown("### Contact")
        parts = []
        if row.get("contact_name"):
            parts.append(f"**Name:** {row['contact_name']}")
        if row.get("contact_email"):
            parts.append(f"**Email:** {row['contact_email']}")
        if row.get("contact_phone"):
            parts.append(f"**Phone:** {row['contact_phone']}")
        st.markdown(" &nbsp;|&nbsp; ".join(parts), unsafe_allow_html=True)

    st.markdown("---")

    if is_dev():
        ac1, ac2, _ = st.columns([1, 1, 3])
        with ac1:
            if row["status"] == "open":
                if st.button("✅ Mark as Resolved"):
                    resolve_item(sb, item_id); st.rerun()
            else:
                if st.button("🔄 Reopen"):
                    reopen_item(sb, item_id); st.rerun()
        with ac2:
            if st.button("🗑️ Delete Post"):
                delete_item(sb, item_id)
                st.toast("Post deleted.")
                navigate_to("Home"); st.rerun()
    else:
        if row["status"] == "open":
            st.caption("🔒 Only dev accounts can mark items as resolved or delete posts.")
        else:
            st.caption("🔒 This item has been marked as resolved by a dev.")

    matches = get_potential_matches(sb, row)
    if matches:
        opposite = "Found" if row["item_type"] == "lost" else "Lost"
        st.markdown(f"### 🔗 Potential Matches ({opposite} Items)")
        st.caption("These items share a similar category or keywords.")
        for m in matches:
            st.markdown(item_card_html(m), unsafe_allow_html=True)
            if st.button("View", key=f"match_{m['id']}",
                         on_click=go_to_detail, args=(m["id"],)):
                st.rerun()


# ── Main App ────────────────────────────────────────────────────────────────────

def main():
    st.set_page_config(
        page_title="Lost & Found Hub",
        page_icon="🔎",
        layout="centered",
    )
    apply_styles()

    sb = get_supabase()

    if not is_logged_in():
        page_landing(sb)
        return

    if "page" not in st.session_state:
        st.session_state["page"] = "Home"

    render_sidebar(sb)

    current_page = st.session_state["page"]

    if current_page == "Home":
        page_home(sb)
    elif current_page == "Report Lost":
        page_post(sb, "lost")
    elif current_page == "Report Found":
        page_post(sb, "found")
    elif current_page == "Browse Lost":
        page_browse(sb, "lost")
    elif current_page == "Browse Found":
        page_browse(sb, "found")
    elif current_page == "Detail":
        page_detail(sb)
    else:
        page_home(sb)


if __name__ == "__main__":
    main()
