"""
Lost & Found Hub — a Streamlit community board for posting and finding lost items.
Run:  streamlit run app.py
"""

import streamlit as st
import sqlite3
import base64
import uuid
import io
import os
from datetime import datetime, date, timedelta
from pathlib import Path

# ── Database ────────────────────────────────────────────────────────────────────

DB_PATH = Path(__file__).parent / "lost_and_found.db"
PHOTO_DIR = Path(__file__).parent / "photos"
PHOTO_DIR.mkdir(exist_ok=True)

CATEGORIES = [
    "Electronics", "Wallet / Purse", "Keys", "Clothing",
    "Jewelry", "Bag / Backpack", "Documents / ID",
    "Pet", "Glasses / Sunglasses", "Umbrella", "Other",
]


def get_db():
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS items (
            id           TEXT PRIMARY KEY,
            item_type    TEXT NOT NULL CHECK(item_type IN ('lost','found')),
            title        TEXT NOT NULL,
            description  TEXT,
            category     TEXT,
            location     TEXT,
            date_occurred TEXT,
            date_posted  TEXT NOT NULL,
            contact_name TEXT,
            contact_email TEXT,
            contact_phone TEXT,
            photo_id     TEXT,
            status       TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','resolved'))
        )
    """)
    conn.commit()
    return conn


def insert_item(conn, data: dict) -> str:
    item_id = uuid.uuid4().hex[:12]
    conn.execute(
        """INSERT INTO items
           (id, item_type, title, description, category, location,
            date_occurred, date_posted, contact_name, contact_email,
            contact_phone, photo_id, status)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            item_id,
            data["item_type"],
            data["title"],
            data["description"],
            data["category"],
            data["location"],
            data["date_occurred"],
            datetime.now().isoformat(timespec="seconds"),
            data["contact_name"],
            data["contact_email"],
            data["contact_phone"],
            data.get("photo_id"),
            "open",
        ),
    )
    conn.commit()
    return item_id


def save_photo(uploaded_file) -> str | None:
    if uploaded_file is None:
        return None
    photo_id = uuid.uuid4().hex[:16]
    ext = uploaded_file.name.rsplit(".", 1)[-1].lower()
    path = PHOTO_DIR / f"{photo_id}.{ext}"
    path.write_bytes(uploaded_file.getvalue())
    return f"{photo_id}.{ext}"


def load_photo_bytes(photo_id: str) -> bytes | None:
    if not photo_id:
        return None
    path = PHOTO_DIR / photo_id
    if path.exists():
        return path.read_bytes()
    return None


def search_items(conn, item_type: str, query: str = "", category: str = "All",
                 status: str = "open", days: int = 0):
    sql = "SELECT * FROM items WHERE item_type = ? AND status = ?"
    params: list = [item_type, status]

    if category and category != "All":
        sql += " AND category = ?"
        params.append(category)
    if query:
        sql += " AND (title LIKE ? OR description LIKE ? OR location LIKE ?)"
        wild = f"%{query}%"
        params.extend([wild, wild, wild])
    if days > 0:
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        sql += " AND date_posted >= ?"
        params.append(cutoff)

    sql += " ORDER BY date_posted DESC"
    return conn.execute(sql, params).fetchall()


def get_item(conn, item_id: str):
    return conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()


def resolve_item(conn, item_id: str):
    conn.execute("UPDATE items SET status = 'resolved' WHERE id = ?", (item_id,))
    conn.commit()


def reopen_item(conn, item_id: str):
    conn.execute("UPDATE items SET status = 'open' WHERE id = ?", (item_id,))
    conn.commit()


def delete_item(conn, item_id: str):
    row = get_item(conn, item_id)
    if row and row["photo_id"]:
        p = PHOTO_DIR / row["photo_id"]
        if p.exists():
            p.unlink()
    conn.execute("DELETE FROM items WHERE id = ?", (item_id,))
    conn.commit()


def get_potential_matches(conn, item):
    """Find items of the opposite type sharing category or keywords."""
    opposite = "found" if item["item_type"] == "lost" else "lost"
    matches = []

    if item["category"]:
        rows = conn.execute(
            """SELECT * FROM items
               WHERE item_type = ? AND status = 'open' AND category = ?
               ORDER BY date_posted DESC LIMIT 10""",
            (opposite, item["category"]),
        ).fetchall()
        matches.extend(rows)

    if item["title"]:
        words = [w for w in item["title"].lower().split() if len(w) > 3]
        for word in words[:4]:
            rows = conn.execute(
                """SELECT * FROM items
                   WHERE item_type = ? AND status = 'open'
                     AND (title LIKE ? OR description LIKE ?)
                   ORDER BY date_posted DESC LIMIT 5""",
                (opposite, f"%{word}%", f"%{word}%"),
            ).fetchall()
            matches.extend(rows)

    seen = set()
    unique = []
    for m in matches:
        if m["id"] not in seen:
            seen.add(m["id"])
            unique.append(m)
    return unique[:8]


def count_stats(conn):
    rows = conn.execute(
        """SELECT item_type, status, COUNT(*) as cnt
           FROM items GROUP BY item_type, status"""
    ).fetchall()
    stats = {"lost_open": 0, "lost_resolved": 0, "found_open": 0, "found_resolved": 0}
    for r in rows:
        stats[f"{r['item_type']}_{r['status']}"] = r["cnt"]
    return stats


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
    </style>
    """, unsafe_allow_html=True)


def badge(item_type: str, status: str = "open") -> str:
    if status == "resolved":
        return '<span class="badge badge-resolved">Resolved</span>'
    cls = "badge-lost" if item_type == "lost" else "badge-found"
    label = "Lost" if item_type == "lost" else "Found"
    return f'<span class="badge {cls}">{label}</span>'


def item_card_html(row) -> str:
    date_str = ""
    if row["date_occurred"]:
        date_str = f" &middot; {row['date_occurred']}"
    loc = f" &middot; 📍 {row['location']}" if row["location"] else ""
    cat = f" &middot; {row['category']}" if row["category"] else ""
    return f"""
    <div class="item-card">
        {badge(row['item_type'], row['status'])}
        <h4 style="margin-top:.4rem">{row['title']}</h4>
        <div class="meta">{cat}{loc}{date_str}</div>
    </div>
    """


# ── Pages ───────────────────────────────────────────────────────────────────────

def page_home(conn):
    st.markdown("## 🏠 Dashboard")
    stats = count_stats(conn)

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
        lost = search_items(conn, "lost")[:5]
        if lost:
            for row in lost:
                st.markdown(item_card_html(row), unsafe_allow_html=True)
                if st.button("View details", key=f"home_l_{row['id']}"):
                    st.session_state["detail_id"] = row["id"]
                    st.session_state["page"] = "Detail"
                    st.rerun()
        else:
            st.info("No lost items posted yet.")

    with col_r:
        st.markdown('<div class="section-head">🟢 Recently Found</div>', unsafe_allow_html=True)
        found = search_items(conn, "found")[:5]
        if found:
            for row in found:
                st.markdown(item_card_html(row), unsafe_allow_html=True)
                if st.button("View details", key=f"home_f_{row['id']}"):
                    st.session_state["detail_id"] = row["id"]
                    st.session_state["page"] = "Detail"
                    st.rerun()
        else:
            st.info("No found items posted yet.")


def page_post(conn, item_type: str):
    emoji = "🔴" if item_type == "lost" else "🟢"
    verb = "Lost" if item_type == "lost" else "Found"
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
        location = st.text_input("Location", placeholder="e.g. Central Park near the fountain")
        photo = st.file_uploader("Photo (optional)", type=["png", "jpg", "jpeg", "webp"])

        st.markdown("#### Your Contact Info")
        cc1, cc2, cc3 = st.columns(3)
        with cc1:
            contact_name = st.text_input("Name")
        with cc2:
            contact_email = st.text_input("Email")
        with cc3:
            contact_phone = st.text_input("Phone")

        submitted = st.form_submit_button(f"📌  Post {verb} Item", use_container_width=True)

    if submitted:
        if not title.strip():
            st.error("Please enter an item title.")
            return

        photo_id = save_photo(photo)
        item_id = insert_item(conn, {
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


def page_browse(conn, item_type: str):
    emoji = "🔴" if item_type == "lost" else "🟢"
    verb = "Lost" if item_type == "lost" else "Found"
    st.markdown(f"## {emoji} Browse {verb} Items")

    fc1, fc2, fc3, fc4 = st.columns([3, 2, 2, 2])
    with fc1:
        query = st.text_input("🔍 Search", placeholder="Keyword, location...", label_visibility="collapsed")
    with fc2:
        cat_filter = st.selectbox("Category", ["All"] + CATEGORIES, label_visibility="collapsed")
    with fc3:
        time_filter = st.selectbox("Time range", ["All time", "Last 7 days", "Last 30 days", "Last 90 days"], label_visibility="collapsed")
    with fc4:
        status_filter = st.selectbox("Status", ["open", "resolved"], label_visibility="collapsed")

    days_map = {"All time": 0, "Last 7 days": 7, "Last 30 days": 30, "Last 90 days": 90}
    results = search_items(conn, item_type, query, cat_filter, status_filter, days_map[time_filter])

    st.caption(f"{len(results)} result{'s' if len(results) != 1 else ''}")

    if not results:
        st.info(f"No {verb.lower()} items match your filters.")
        return

    cols = st.columns(2)
    for i, row in enumerate(results):
        with cols[i % 2]:
            st.markdown(item_card_html(row), unsafe_allow_html=True)

            photo_bytes = load_photo_bytes(row["photo_id"])
            if photo_bytes:
                st.image(photo_bytes, width=200)

            if st.button("View details →", key=f"browse_{item_type}_{row['id']}"):
                st.session_state["detail_id"] = row["id"]
                st.session_state["page"] = "Detail"
                st.rerun()
            st.markdown("")


def page_detail(conn):
    item_id = st.session_state.get("detail_id")
    if not item_id:
        st.warning("No item selected. Go browse items first.")
        return

    row = get_item(conn, item_id)
    if not row:
        st.error("Item not found (it may have been deleted).")
        return

    verb = "Lost" if row["item_type"] == "lost" else "Found"

    if st.button("← Back to browsing"):
        st.session_state["page"] = f"Browse {verb}"
        st.session_state.pop("detail_id", None)
        st.rerun()

    st.markdown(f"{badge(row['item_type'], row['status'])} &nbsp; `{row['id']}`", unsafe_allow_html=True)
    st.markdown(f"# {row['title']}")

    col_img, col_info = st.columns([1, 2])

    with col_img:
        photo_bytes = load_photo_bytes(row["photo_id"])
        if photo_bytes:
            st.image(photo_bytes, use_container_width=True)
        else:
            st.markdown(
                '<div style="background:#f0f1f4;border-radius:12px;height:200px;'
                'display:flex;align-items:center;justify-content:center;color:#aaa;'
                'font-size:2.5rem;">📷</div>',
                unsafe_allow_html=True,
            )

    with col_info:
        if row["category"]:
            st.markdown(f"**Category:** {row['category']}")
        if row["location"]:
            st.markdown(f"**Location:** {row['location']}")
        if row["date_occurred"]:
            st.markdown(f"**Date {verb.lower()}:** {row['date_occurred']}")
        st.markdown(f"**Posted:** {row['date_posted'][:16].replace('T', ' ')}")

    if row["description"]:
        st.markdown("### Description")
        st.markdown(row["description"])

    has_contact = row["contact_name"] or row["contact_email"] or row["contact_phone"]
    if has_contact:
        st.markdown("### Contact")
        parts = []
        if row["contact_name"]:
            parts.append(f"**Name:** {row['contact_name']}")
        if row["contact_email"]:
            parts.append(f"**Email:** {row['contact_email']}")
        if row["contact_phone"]:
            parts.append(f"**Phone:** {row['contact_phone']}")
        st.markdown(" &nbsp;|&nbsp; ".join(parts), unsafe_allow_html=True)

    st.markdown("---")
    ac1, ac2, _ = st.columns([1, 1, 3])
    with ac1:
        if row["status"] == "open":
            if st.button("✅ Mark as Resolved"):
                resolve_item(conn, item_id)
                st.rerun()
        else:
            if st.button("🔄 Reopen"):
                reopen_item(conn, item_id)
                st.rerun()
    with ac2:
        if st.button("🗑️ Delete Post"):
            delete_item(conn, item_id)
            st.success("Post deleted.")
            st.session_state.pop("detail_id", None)
            st.session_state["page"] = "Home"
            st.rerun()

    matches = get_potential_matches(conn, row)
    if matches:
        opposite = "Found" if row["item_type"] == "lost" else "Lost"
        st.markdown(f"### 🔗 Potential Matches ({opposite} Items)")
        st.caption("These items share a similar category or keywords.")
        for m in matches:
            st.markdown(item_card_html(m), unsafe_allow_html=True)
            if st.button("View", key=f"match_{m['id']}"):
                st.session_state["detail_id"] = m["id"]
                st.rerun()


# ── Main App ────────────────────────────────────────────────────────────────────

def main():
    st.set_page_config(
        page_title="Lost & Found Hub",
        page_icon="🔎",
        layout="centered",
    )
    apply_styles()

    conn = get_db()

    # Initialize page state
    if "page" not in st.session_state:
        st.session_state["page"] = "Home"

    pages = ["Home", "Report Lost", "Report Found", "Browse Lost", "Browse Found"]
    icons = ["🏠", "🔴", "🟢", "📋", "📋"]

    # Render detail page first — sidebar should not interfere
    if st.session_state.get("page") == "Detail":
        # Show sidebar radio locked to no visible selection to avoid confusion
        st.sidebar.markdown("# 🔎 Lost & Found Hub")
        st.sidebar.caption("Community board for lost and found items")
        st.sidebar.markdown("---")
        # Sidebar nav still renders so user can escape detail view by clicking a page
        selected = st.sidebar.radio(
            "Navigate",
            pages,
            index=0,
            format_func=lambda p: f"{icons[pages.index(p)]}  {p}",
            label_visibility="collapsed",
            key="nav_radio",
        )
        # If user explicitly clicks a sidebar item, navigate there
        if st.session_state.get("_last_nav") != selected:
            st.session_state["_last_nav"] = selected
            st.session_state["page"] = selected
            st.session_state.pop("detail_id", None)
            st.rerun()

        page_detail(conn)
        conn.close()
        return

    # Normal navigation
    st.sidebar.markdown("# 🔎 Lost & Found Hub")
    st.sidebar.caption("Community board for lost and found items")
    st.sidebar.markdown("---")

    current_page = st.session_state["page"]
    default_idx = pages.index(current_page) if current_page in pages else 0

    selected = st.sidebar.radio(
        "Navigate",
        pages,
        index=default_idx,
        format_func=lambda p: f"{icons[pages.index(p)]}  {p}",
        label_visibility="collapsed",
        key="nav_radio",
    )

    # Only update page state when user actually changes the sidebar selection
    if selected != st.session_state["page"]:
        st.session_state["page"] = selected
        st.session_state.pop("detail_id", None)
        st.rerun()

    st.session_state["_last_nav"] = selected

    if selected == "Home":
        page_home(conn)
    elif selected == "Report Lost":
        page_post(conn, "lost")
    elif selected == "Report Found":
        page_post(conn, "found")
    elif selected == "Browse Lost":
        page_browse(conn, "lost")
    elif selected == "Browse Found":
        page_browse(conn, "found")

    conn.close()


if __name__ == "__main__":
    main()
