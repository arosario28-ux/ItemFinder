"""
Lost & Found Hub — Streamlit + Supabase community board with charity auction.
"""
import os, smtplib, uuid
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import streamlit as st
from datetime import datetime, date, timedelta
from supabase import create_client, Client

try:
    from dotenv import load_dotenv; load_dotenv()
except ImportError: pass

SUPABASE_URL = os.environ.get("SUPABASE_URL") or st.secrets.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY") or st.secrets.get("SUPABASE_KEY", "")
SMTP_EMAIL = os.environ.get("SMTP_EMAIL") or st.secrets.get("SMTP_EMAIL", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD") or st.secrets.get("SMTP_PASSWORD", "")
AUCTION_WEEKS = 4
CATEGORIES = ["Electronics","Wallet / Purse","Keys","Clothing","Jewelry","Bag / Backpack","Documents / ID","Pet","Glasses / Sunglasses","Umbrella","Other"]

@st.cache_resource
def get_supabase() -> Client:
    if not SUPABASE_URL or not SUPABASE_KEY: st.error("Missing Supabase credentials."); st.stop()
    return create_client(SUPABASE_URL, SUPABASE_KEY)

def get_dev_emails(sb): return [r["email"] for r in sb.table("dev_users").select("email").order("created_at").execute().data]
def is_dev_email(sb, e): return len(sb.table("dev_users").select("id").ilike("email", e.strip()).execute().data) > 0
def add_dev_user(sb, e, n=""):
    sb.table("dev_users").insert({"id": uuid.uuid4().hex[:12], "email": e.strip().lower(), "name": n.strip(), "created_at": datetime.now().isoformat(timespec="seconds")}).execute()
def dev_count(sb): return (sb.table("dev_users").select("id", count="exact").execute().count) or 0
def is_logged_in(): return st.session_state.get("auth_role") in ("guest","dev")
def is_dev(): return st.session_state.get("auth_role") == "dev"
def logout():
    for k in ["auth_role","auth_email","auth_name","page","detail_id"]: st.session_state.pop(k, None)

def insert_item(sb, d):
    iid = uuid.uuid4().hex[:12]
    sb.table("items").insert({"id":iid,"item_type":d["item_type"],"title":d["title"],"description":d["description"],"category":d["category"],"location":d["location"],"date_occurred":d["date_occurred"],"date_posted":datetime.now().isoformat(timespec="seconds"),"contact_name":d["contact_name"],"contact_email":d["contact_email"],"contact_phone":d["contact_phone"],"photo_id":d.get("photo_id"),"status":"open"}).execute()
    return iid
def save_photo(sb, f):
    if not f: return None
    p = f"{uuid.uuid4().hex[:16]}.{f.name.rsplit('.',1)[-1].lower()}"
    sb.storage.from_("photos").upload(path=p, file=f.getvalue(), file_options={"content-type":{"jpg":"image/jpeg","jpeg":"image/jpeg","png":"image/png","webp":"image/webp"}.get(p.rsplit(".",1)[-1],"application/octet-stream")})
    return p
def get_photo_url(sb, pid): return sb.storage.from_("photos").get_public_url(pid) if pid else None
def delete_photo(sb, pid):
    if pid:
        try: sb.storage.from_("photos").remove([pid])
        except: pass
def search_items(sb, item_type, query="", category="All", status="open", days=0):
    q = sb.table("items").select("*").eq("item_type", item_type).eq("status", status)
    if category and category != "All": q = q.eq("category", category)
    if query: w=f"%{query}%"; q=q.or_(f"title.ilike.{w},description.ilike.{w},location.ilike.{w}")
    if days > 0: q = q.gte("date_posted", (datetime.now()-timedelta(days=days)).isoformat())
    return q.order("date_posted", desc=True).execute().data
def get_item(sb, iid):
    r = sb.table("items").select("*").eq("id", iid).execute()
    return r.data[0] if r.data else None
def resolve_item(sb, iid): sb.table("items").update({"status":"resolved"}).eq("id",iid).execute()
def reopen_item(sb, iid): sb.table("items").update({"status":"open"}).eq("id",iid).execute()
def delete_item(sb, iid):
    row = get_item(sb, iid)
    if row and row.get("photo_id"): delete_photo(sb, row["photo_id"])
    sb.table("items").delete().eq("id", iid).execute()
def send_to_auction(sb, iid):
    row = get_item(sb, iid)
    if not row or row.get("item_type") != "found" or row.get("status") != "open" or is_in_auction(row) or has_open_match(sb, row):
        return False
    old = (datetime.now() - timedelta(weeks=AUCTION_WEEKS + 1)).isoformat(timespec="seconds")
    sb.table("items").update({"date_posted": old}).eq("id", iid).execute()
    return True
def is_in_auction(row):
    try:
        posted = datetime.fromisoformat(row["date_posted"])
        return (datetime.now() - posted) > timedelta(weeks=AUCTION_WEEKS)
    except: return False
def get_potential_matches(sb, item):
    opp = "found" if item["item_type"]=="lost" else "lost"; matches=[]
    if item.get("category"): matches.extend(sb.table("items").select("*").eq("item_type",opp).eq("status","open").eq("category",item["category"]).order("date_posted",desc=True).limit(10).execute().data)
    if item.get("title"):
        for w in [x for x in item["title"].lower().split() if len(x)>3][:4]:
            matches.extend(sb.table("items").select("*").eq("item_type",opp).eq("status","open").or_(f"title.ilike.%{w}%,description.ilike.%{w}%").order("date_posted",desc=True).limit(5).execute().data)
    seen=set(); return [m for m in matches if m["id"] not in seen and not seen.add(m["id"])][:8]
def has_open_match(sb, item):
    return len(get_potential_matches(sb, item)) > 0
def count_stats(sb):
    s={}
    for t in ("lost","found"):
        for st_ in ("open","resolved"): s[f"{t}_{st_}"]=(sb.table("items").select("id",count="exact").eq("item_type",t).eq("status",st_).execute().count) or 0
    return s
def get_auction_items(sb):
    cutoff = (datetime.now() - timedelta(weeks=AUCTION_WEEKS)).isoformat()
    rows = sb.table("items").select("*").eq("item_type","found").eq("status","open").lte("date_posted", cutoff).order("date_posted").execute().data
    return [r for r in rows if not has_open_match(sb, r)]
def place_bid(sb, item_id, email):
    sb.table("auction_bids").insert({"id":uuid.uuid4().hex[:12],"item_id":item_id,"email":email.strip().lower(),"created_at":datetime.now().isoformat(timespec="seconds")}).execute()
def get_bids(sb, item_id): return sb.table("auction_bids").select("*").eq("item_id",item_id).order("created_at").execute().data
def has_bid(sb, item_id, email): return len(sb.table("auction_bids").select("id").eq("item_id",item_id).ilike("email",email.strip()).execute().data)>0

def notify_devs(sb, item_data):
    if not SMTP_EMAIL or not SMTP_PASSWORD: return
    devs = get_dev_emails(sb)
    if not devs: return
    verb = "Lost" if item_data["item_type"]=="lost" else "Found"
    try:
        server=smtplib.SMTP("smtp.gmail.com",587); server.starttls(); server.login(SMTP_EMAIL,SMTP_PASSWORD)
        for addr in devs:
            msg=MIMEMultipart(); msg["From"]=SMTP_EMAIL; msg["To"]=addr
            msg["Subject"]=f"[Lost & Found] New {verb}: {item_data['title']}"
            msg.attach(MIMEText(f"New {verb.lower()} item: {item_data['title']}\nCategory: {item_data['category']}\nLast seen at: {item_data['location']}\nDate: {item_data['date_occurred']}\nDescription: {item_data['description'] or '-'}","plain"))
            server.sendmail(SMTP_EMAIL,addr,msg.as_string())
        server.quit()
    except: pass

def nav(page, **kw):
    st.session_state["page"]=page
    for k,v in kw.items(): st.session_state[k]=v
def go_detail(iid): nav("Detail", detail_id=iid)

def auction_timer_html(row):
    try:
        posted = datetime.fromisoformat(row["date_posted"])
        deadline = posted + timedelta(weeks=AUCTION_WEEKS)
        remain = deadline - datetime.now()
        if remain.total_seconds() <= 0:
            return '<div style="background:#fff3e0;border:1px solid #ffcc80;border-radius:8px;padding:.4rem .7rem;font-size:.78rem;color:#e65100;font-weight:600;margin-bottom:.5rem">IN CHARITY AUCTION</div>'
        d = remain.days; h = remain.seconds // 3600
        return f'<div style="background:#f3f0ea;border:1px solid #e8e4dc;border-radius:8px;padding:.4rem .7rem;font-size:.78rem;color:#6b6b80;font-weight:500;margin-bottom:.5rem">Moves to auction in: <b style="color:#1a1a2e">{d}d {h}h</b></div>'
    except: return ""

def apply_styles():
    st.markdown("""<style>
    @import url('https://fonts.googleapis.com/css2?family=Playfair+Display:wght@400;600;700;800&family=DM+Sans:opsz,wght@9..40,300;9..40,400;9..40,500;9..40,600;9..40,700&display=swap');
    :root{--cream:#faf8f4;--warm:#f3f0ea;--surface:#fff;--ink:#1a1a2e;--ink2:#6b6b80;--ink3:#a0a0b0;--accent:#e8532b;--red:#c0392b;--red-bg:#fef2f2;--green:#1a7a4c;--green-bg:#ecfdf5;--bdr:#e8e4dc;--sh-s:0 1px 3px rgba(26,26,46,.06);--sh-m:0 4px 20px rgba(26,26,46,.08);--sh-l:0 12px 40px rgba(26,26,46,.12);--r:14px;--rs:8px;--ff-d:'Playfair Display',Georgia,serif;--ff-b:'DM Sans',system-ui,sans-serif}
    .stApp{background:var(--cream)!important;font-family:var(--ff-b)!important}
    .block-container{max-width:960px!important;padding-top:2rem!important}
    h1,h2,h3,.stMarkdown h1,.stMarkdown h2,.stMarkdown h3{font-family:var(--ff-d)!important;color:var(--ink)!important;font-weight:700!important;letter-spacing:-0.02em!important}
    p,li,label,.stMarkdown p{font-family:var(--ff-b)!important}
    section[data-testid="stSidebar"]{background:var(--ink)!important}
    section[data-testid="stSidebar"] h1,section[data-testid="stSidebar"] h5{color:#fff!important;font-family:var(--ff-d)!important}
    section[data-testid="stSidebar"] p,section[data-testid="stSidebar"] span,section[data-testid="stSidebar"] label,section[data-testid="stSidebar"] .stMarkdown p,section[data-testid="stSidebar"] .stCaption p{color:#b0b0c0!important}
    section[data-testid="stSidebar"] .stButton>button{background:transparent!important;color:#c4c4d4!important;border:1px solid rgba(255,255,255,.08)!important;border-radius:var(--rs)!important;text-align:left!important;font-family:var(--ff-b)!important;font-weight:500!important;padding:.55rem .9rem!important;transition:all .2s ease!important}
    section[data-testid="stSidebar"] .stButton>button:hover{background:rgba(255,255,255,.07)!important;color:#fff!important;transform:translateX(3px)}
    section[data-testid="stSidebar"] .stButton>button[kind="primary"]{background:var(--accent)!important;color:#fff!important;border-color:var(--accent)!important}
    @keyframes fadeUp{from{opacity:0;transform:translateY(16px)}to{opacity:1;transform:translateY(0)}}
    .anim{animation:fadeUp .45s cubic-bezier(.22,1,.36,1) both}.d1{animation-delay:0s}.d2{animation-delay:.07s}.d3{animation-delay:.14s}.d4{animation-delay:.21s}
    .stat{background:var(--surface);border:1px solid var(--bdr);border-radius:var(--r);padding:1.2rem .8rem;text-align:center;box-shadow:var(--sh-s);transition:all .25s cubic-bezier(.22,1,.36,1);overflow:hidden;position:relative;cursor:pointer}
    .stat::after{content:'';position:absolute;bottom:0;left:0;right:0;height:3px;background:var(--bdr);transition:background .25s}
    .stat:hover{transform:translateY(-3px);box-shadow:var(--sh-m)}.stat:hover::after{background:var(--accent)}
    .stat .n{font-family:var(--ff-d);font-size:2.2rem;font-weight:800;line-height:1;letter-spacing:-0.03em}
    .stat .l{font-size:.72rem;font-weight:600;color:var(--ink2);margin-top:.3rem;text-transform:uppercase;letter-spacing:.08em}
    .s-red .n{color:var(--red)}.s-grn .n{color:var(--green)}.s-acc .n{color:var(--accent)}.s-mut .n{color:var(--ink2)}
    .card{background:var(--surface);border:1px solid var(--bdr);border-radius:var(--r);padding:1rem 1.2rem;margin-bottom:.6rem;box-shadow:var(--sh-s);transition:all .2s cubic-bezier(.22,1,.36,1);border-left:4px solid var(--bdr);overflow:hidden}
    .card.c-lost{border-left-color:var(--red)}.card.c-found{border-left-color:var(--green)}
    .card:hover{transform:translateY(-2px);box-shadow:var(--sh-m)}
    .card h4{margin:.3rem 0 .2rem;font-family:var(--ff-d)!important;font-size:1rem;font-weight:600;color:var(--ink)}
    .card .meta{font-size:.78rem;color:var(--ink2)}
    .card img{width:100%;max-height:200px;object-fit:cover;border-radius:8px;margin-top:.6rem}
    .badge{display:inline-block;padding:.15rem .6rem;border-radius:20px;font-size:.65rem;font-weight:600;font-family:var(--ff-b);text-transform:uppercase;letter-spacing:.06em}
    .b-lost{background:var(--red-bg);color:var(--red)}.b-found{background:var(--green-bg);color:var(--green)}.b-resolved{background:#f1f1f1;color:var(--ink2)}
    .sec{font-family:var(--ff-d);font-size:1.05rem;font-weight:700;color:var(--ink);border-bottom:2px solid var(--bdr);padding-bottom:.4rem;margin-bottom:.8rem}
    .bc{font-size:.8rem;color:var(--ink2);margin-bottom:.5rem}.bc b{color:var(--ink)}.bc .sep{margin:0 .35rem;color:var(--ink3)}
    .hero{text-align:center;padding:3.5rem 1rem 2rem}
    .hero h1{font-family:var(--ff-d)!important;font-size:3rem!important;font-weight:800!important;letter-spacing:-0.03em!important;color:var(--ink)!important;line-height:1.1}
    .hero .hi{color:var(--accent)}.hero .sub{font-size:1.1rem;color:var(--ink2);margin-top:.5rem;margin-bottom:2rem}
    .lcard{background:var(--surface);border:2px solid var(--bdr);border-radius:18px;padding:2rem 1.5rem;text-align:center;transition:all .3s cubic-bezier(.22,1,.36,1);overflow:hidden;position:relative}
    .lcard::before{content:'';position:absolute;top:0;left:0;right:0;height:3px;background:linear-gradient(90deg,var(--accent),#f5a623);opacity:0;transition:opacity .3s}
    .lcard:hover{border-color:var(--accent);box-shadow:var(--sh-l);transform:translateY(-4px)}.lcard:hover::before{opacity:1}
    .lcard .ico{font-size:2.5rem;display:block;margin-bottom:.5rem}
    .lcard h3{font-family:var(--ff-d)!important;font-size:1.25rem!important;color:var(--ink)!important;margin:.2rem 0!important}
    .lcard p{font-size:.85rem;color:var(--ink2);line-height:1.5;margin-bottom:1rem}
    .rbadge{display:inline-block;padding:.18rem .6rem;border-radius:20px;font-size:.68rem;font-weight:600;text-transform:uppercase;letter-spacing:.05em}
    .rb-dev{background:rgba(232,83,43,.18);color:#ff6b4a}.rb-guest{background:rgba(255,255,255,.08);color:#999}
    .stMainBlockContainer .stButton>button{font-family:var(--ff-b)!important;font-weight:600!important;border-radius:var(--rs)!important;transition:all .2s cubic-bezier(.22,1,.36,1)!important}
    .stMainBlockContainer .stButton>button:hover{transform:translateY(-1px)!important}
    .stMainBlockContainer .stButton>button[kind="primary"]{background:var(--accent)!important;border-color:var(--accent)!important;color:#fff!important}
    .stTextInput input,.stTextArea textarea,.stSelectbox>div>div{font-family:var(--ff-b)!important;border-radius:var(--rs)!important}
    .ph-empty{background:var(--warm);border:2px dashed var(--bdr);border-radius:var(--r);height:180px;display:flex;align-items:center;justify-content:center;color:var(--ink3);font-size:2.2rem}
    .dtitle{font-family:var(--ff-d);font-size:1.8rem;font-weight:700;color:var(--ink);letter-spacing:-0.02em;line-height:1.2;margin:.4rem 0 .8rem}
    hr{border-color:var(--bdr)!important;opacity:.4!important}
    ::-webkit-scrollbar{width:5px}::-webkit-scrollbar-thumb{background:var(--bdr);border-radius:3px}

    /* ── Stat button cards ───────────────────── */
    .statlbl{text-align:center;font-size:.72rem;font-weight:600;color:var(--ink2);text-transform:uppercase;letter-spacing:.08em;margin-top:-8px;padding-bottom:4px}
    .statlbl-red{color:var(--red)}.statlbl-grn{color:var(--green)}.statlbl-acc{color:var(--accent)}.statlbl-mut{color:var(--ink2)}

    /* Style the first 4 buttons after Dashboard heading as stat cards */
    .stMainBlockContainer [data-testid="stHorizontalBlock"]:first-of-type .stButton>button {
        background:var(--surface)!important;
        border:1px solid var(--bdr)!important;
        border-radius:var(--r)!important;
        padding:1.4rem .5rem .8rem!important;
        box-shadow:var(--sh-s)!important;
        font-family:var(--ff-d)!important;
        font-size:2.2rem!important;
        font-weight:800!important;
        line-height:1!important;
        letter-spacing:-0.03em!important;
        color:var(--ink)!important;
        min-height:70px!important;
        transition:all .25s cubic-bezier(.22,1,.36,1)!important;
        position:relative;
        overflow:hidden;
    }
    .stMainBlockContainer [data-testid="stHorizontalBlock"]:first-of-type .stButton>button::after {
        content:'';position:absolute;bottom:0;left:0;right:0;height:3px;background:var(--bdr);transition:background .25s;
    }
    .stMainBlockContainer [data-testid="stHorizontalBlock"]:first-of-type .stButton>button:hover {
        transform:translateY(-3px)!important;box-shadow:var(--sh-m)!important;
    }
    .stMainBlockContainer [data-testid="stHorizontalBlock"]:first-of-type .stButton>button:hover::after {
        background:var(--accent);
    }
    /* Color the numbers - target by column position */
    .stMainBlockContainer [data-testid="stHorizontalBlock"]:first-of-type > div:nth-child(1) .stButton>button { color:var(--red)!important; }
    .stMainBlockContainer [data-testid="stHorizontalBlock"]:first-of-type > div:nth-child(2) .stButton>button { color:var(--green)!important; }
    .stMainBlockContainer [data-testid="stHorizontalBlock"]:first-of-type > div:nth-child(3) .stButton>button { color:var(--accent)!important; }
    .stMainBlockContainer [data-testid="stHorizontalBlock"]:first-of-type > div:nth-child(4) .stButton>button { color:var(--ink2)!important; }
    </style>""", unsafe_allow_html=True)

def badge_html(t, s="open"):
    if s=="resolved": return '<span class="badge b-resolved">Resolved</span>'
    return f'<span class="badge {"b-lost" if t=="lost" else "b-found"}">{"Lost" if t=="lost" else "Found"}</span>'

def card_html(row, i=0, sb=None):
    d = f" · {row['date_occurred']}" if row.get("date_occurred") else ""
    loc = f" · {row['location']}" if row.get("location") else ""
    cat = f" · {row['category']}" if row.get("category") else ""
    cc = "c-lost" if row["item_type"]=="lost" else "c-found"
    dc = f"d{min(i+1,4)}"
    img = ""
    if row.get("photo_id") and sb:
        url = get_photo_url(sb, row["photo_id"])
        if url: img = f'<img src="{url}" alt="">'
    timer = auction_timer_html(row) if row.get("status")=="open" else ""
    return f'<div class="card {cc} anim {dc}">{timer}{badge_html(row["item_type"],row["status"])}<h4>{row["title"]}</h4><div class="meta">{cat}{loc}{d}</div>{img}</div>'

def breadcrumb(*parts):
    items = [p[0] for p in parts[:-1]] + [f"<b>{parts[-1][0]}</b>"]
    st.markdown(f'<div class="bc">{"<span class=sep>›</span>".join(items)}</div>', unsafe_allow_html=True)

def show_photo(sb, pid, **kw):
    if pid:
        url = get_photo_url(sb, pid)
        if url: st.image(url, **kw); return
    st.markdown('<div class="ph-empty">No Photo</div>', unsafe_allow_html=True)

def page_landing(sb):
    st.markdown('<div class="hero"><h1>Lost & <span class="hi">Found</span> Hub</h1><div class="sub">Reuniting people with what matters</div></div>', unsafe_allow_html=True)
    c1,c2 = st.columns(2, gap="large")
    with c1:
        st.markdown('<div class="lcard anim d1"><h3>Guest Access</h3><p>Browse and post items freely.</p></div>', unsafe_allow_html=True)
        if st.button("Continue as Guest", use_container_width=True, type="secondary"): st.session_state["auth_role"]="guest"; st.session_state["page"]="Home"; st.rerun()
    with c2:
        st.markdown('<div class="lcard anim d2"><h3>Dev Login</h3><p>Manage the board and auctions.</p></div>', unsafe_allow_html=True)
        if st.button("Log in with Email", use_container_width=True, type="primary"): st.session_state["show_login"]=True; st.rerun()
    if st.session_state.get("show_login"):
        st.markdown("---"); st.markdown("### Dev Login")
        has = dev_count(sb)>0
        if has:
            with st.form("login"):
                email=st.text_input("Email",placeholder="you@example.com"); go=st.form_submit_button("Log In",use_container_width=True)
            if go:
                if not email.strip(): st.error("Enter an email.")
                elif is_dev_email(sb,email): st.session_state.update(auth_role="dev",auth_email=email.strip().lower(),page="Home"); st.session_state.pop("show_login",None); st.rerun()
                else: st.error("Email not registered.")
        else:
            st.info("No devs yet. Create the first account.")
            with st.form("setup"):
                name=st.text_input("Name"); email=st.text_input("Email"); confirm=st.text_input("Confirm email"); go=st.form_submit_button("Create Account",use_container_width=True)
            if go:
                if not email.strip(): st.error("Enter an email.")
                elif email.strip().lower()!=confirm.strip().lower(): st.error("Emails don't match.")
                else: add_dev_user(sb,email,name); st.session_state.update(auth_role="dev",auth_email=email.strip().lower(),page="Home"); st.session_state.pop("show_login",None); st.rerun()
        if st.button("← Back"): st.session_state.pop("show_login",None); st.rerun()

def render_sidebar(sb):
    st.sidebar.markdown("# Lost & Found")
    if is_dev():
        st.sidebar.markdown(f'<span class="rbadge rb-dev">Dev</span> &nbsp; <span style="color:#b0b0c0;font-size:.82rem">{st.session_state.get("auth_email","")}</span>', unsafe_allow_html=True)
    else: st.sidebar.markdown('<span class="rbadge rb-guest">Guest</span>', unsafe_allow_html=True)
    st.sidebar.markdown("---")
    cur = st.session_state.get("page","Home")
    for icon,name in [("🏠","Home"),("","Report Lost"),("","Report Found"),("","Browse Lost"),("","Browse Found"),("","Charity Auction")]:
        st.sidebar.button(f"{icon}  {name}" if icon else name,key=f"n_{name}",on_click=nav,args=(name,),use_container_width=True,type="primary" if cur==name else "secondary")
    st.sidebar.markdown("---")
    if st.sidebar.button("Log Out",use_container_width=True): logout(); st.rerun()
    if is_dev():
        st.sidebar.markdown("---"); st.sidebar.markdown("##### Dev Tools")
        ne=st.sidebar.text_input("Add dev email",key="add_dev_email",placeholder="email@example.com",label_visibility="collapsed")
        if st.sidebar.button("Add Dev",key="btn_add"):
            if ne.strip() and "@" in ne:
                if is_dev_email(sb,ne): st.sidebar.warning("Already exists.")
                else: add_dev_user(sb,ne); st.rerun()

def page_home(sb):
    st.markdown("## Dashboard")
    stats = count_stats(sb)
    st.markdown('<div class="stat-row">', unsafe_allow_html=True)
    c1,c2,c3,c4 = st.columns(4)
    with c1:
        if st.button(str(stats["lost_open"]),key="go_lost",use_container_width=True): nav("Browse Lost"); st.rerun()
        st.markdown('<div class="statlbl statlbl-red">LOST</div>', unsafe_allow_html=True)
    with c2:
        if st.button(str(stats["found_open"]),key="go_found",use_container_width=True): nav("Browse Found"); st.rerun()
        st.markdown('<div class="statlbl statlbl-grn">FOUND</div>', unsafe_allow_html=True)
    with c3:
        st.button(str(stats["lost_open"]+stats["found_open"]),key="stat_active",use_container_width=True)
        st.markdown('<div class="statlbl statlbl-acc">ACTIVE</div>', unsafe_allow_html=True)
    with c4:
        st.button(str(stats["lost_resolved"]+stats["found_resolved"]),key="stat_reunited",use_container_width=True)
        st.markdown('<div class="statlbl statlbl-mut">REUNITED</div>', unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)
    st.markdown(""); st.markdown("")
    cl,cr = st.columns(2, gap="medium")
    with cl:
        st.markdown('<div class="sec">Recently Lost</div>', unsafe_allow_html=True)
        lost = search_items(sb,"lost")[:5]
        if lost:
            for i,row in enumerate(lost):
                st.markdown(card_html(row,i,sb), unsafe_allow_html=True)
                st.button("View →",key=f"hl_{row['id']}",on_click=go_detail,args=(row["id"],))
        else: st.info("No lost items yet.")
    with cr:
        st.markdown('<div class="sec">Recently Found</div>', unsafe_allow_html=True)
        found = search_items(sb,"found")[:5]
        if found:
            for i,row in enumerate(found):
                st.markdown(card_html(row,i,sb), unsafe_allow_html=True)
                st.button("View →",key=f"hf_{row['id']}",on_click=go_detail,args=(row["id"],))
        else: st.info("No found items yet.")

def page_post(sb, item_type):
    verb="Lost" if item_type=="lost" else "Found"
    breadcrumb(("Home","Home"),(f"Report {verb}",None))
    st.markdown(f"## Report a {verb} Item")
    if item_type=="found":
        match_existing = st.toggle("I found an already posted lost item",value=False)
        if match_existing:
            open_lost = search_items(sb,"lost")
            if not open_lost: st.info("No open lost items to match."); return
            opts = {f"{r['title']} — {r['category']} — {r['location'] or '?'} ({r['date_occurred']})":r["id"] for r in open_lost}
            sel = st.selectbox("Select the lost item you found",list(opts.keys()))
            if sel:
                chosen = get_item(sb, opts[sel])
                if chosen:
                    st.markdown(card_html(chosen,sb=sb), unsafe_allow_html=True)
                    if is_dev():
                        if st.button("Mark this item as found / reunited",type="primary",use_container_width=True):
                            resolve_item(sb,chosen["id"]); notify_devs(sb,{**chosen,"item_type":"found","title":f"RESOLVED: {chosen['title']}"})
                            st.success(f"'{chosen['title']}' marked as reunited!"); st.balloons()
                    else:
                        st.info("Guests cannot mark items reunited. Submit your name to create a found post while keeping the lost post open.")
                        with st.form(f"guest_match_post_{chosen['id']}"):
                            guest_name = st.text_input("Your name *", key=f"guest_match_name_{chosen['id']}")
                            post_found = st.form_submit_button("Post as Found", type="primary", use_container_width=True)
                        if post_found:
                            if not guest_name.strip():
                                st.error("Please enter your name.")
                            else:
                                d=dict(item_type="found",title=chosen["title"].strip(),description=f"Potential match for lost item '{chosen['title']}' (ID: {chosen['id']}). Original lost post remains open for owner confirmation.",category=(chosen.get("category") or "Other"),location=(chosen.get("location") or "").strip(),date_occurred=str(date.today()),contact_name=guest_name.strip(),contact_email="",contact_phone="",photo_id=chosen.get("photo_id"))
                                iid=insert_item(sb,d); notify_devs(sb,d); st.success(f"Found post created! ID: {iid}"); st.balloons()
            return
    with st.form(f"post_{item_type}",clear_on_submit=True):
        title=st.text_input("Item title *",placeholder="e.g. Black leather wallet")
        description=st.text_area("Description",placeholder="Brand, color, distinguishing features...",height=100)
        c1,c2,c3=st.columns(3)
        with c1: category=st.selectbox("Category",CATEGORIES)
        with c2: date_occurred=st.date_input(f"Date {verb.lower()}",value=date.today(),max_value=date.today())
        with c3: location=st.text_input("Last seen at",placeholder="e.g. Main Street")
        photo=st.file_uploader("Photo (optional)",type=["png","jpg","jpeg","webp"])
        st.markdown("**Contact Info**")
        cc1,cc2,cc3=st.columns(3)
        with cc1: cn=st.text_input("Name")
        with cc2: ce=st.text_input("Email")
        with cc3: cp=st.text_input("Phone")
        submitted=st.form_submit_button(f"Post {verb} Item",use_container_width=True)
    if submitted:
        if not title.strip(): st.error("Please enter a title."); return
        pid=save_photo(sb,photo)
        d=dict(item_type=item_type,title=title.strip(),description=description.strip(),category=category,location=location.strip(),date_occurred=str(date_occurred),contact_name=cn.strip(),contact_email=ce.strip(),contact_phone=cp.strip(),photo_id=pid)
        iid=insert_item(sb,d); notify_devs(sb,d); st.success(f"Posted! ID: {iid}"); st.balloons()

def page_browse(sb, item_type):
    verb="Lost" if item_type=="lost" else "Found"
    breadcrumb(("Home","Home"),(f"Browse {verb}",None))
    st.markdown(f"## Browse {verb} Items")
    c1,c2,c3,c4=st.columns([3,2,2,2])
    with c1: q=st.text_input("Search",placeholder="Keyword or location...",label_visibility="collapsed")
    with c2: cat=st.selectbox("Category",["All"]+CATEGORIES,label_visibility="collapsed")
    with c3: tf=st.selectbox("Time",["All time","7 days","30 days","90 days"],label_visibility="collapsed")
    with c4: sf=st.selectbox("Status",["open","resolved"],label_visibility="collapsed")
    dm={"All time":0,"7 days":7,"30 days":30,"90 days":90}
    results=search_items(sb,item_type,q,cat,sf,dm[tf])
    st.caption(f"{len(results)} result{'s' if len(results)!=1 else ''}")
    if not results: st.info(f"No {verb.lower()} items match."); return
    cols=st.columns(2)
    for i,row in enumerate(results):
        with cols[i%2]:
            st.markdown(card_html(row,i,sb), unsafe_allow_html=True)
            st.button("View →",key=f"b_{item_type}_{row['id']}",on_click=go_detail,args=(row["id"],))

def page_detail(sb):
    iid=st.session_state.get("detail_id")
    if not iid: st.warning("No item selected."); return
    row=get_item(sb,iid)
    if not row: st.error("Item not found."); return
    breadcrumb(("Home","Home"),(row["title"],None))
    if st.button("← Home"): nav("Home"); st.rerun()
    st.markdown(auction_timer_html(row), unsafe_allow_html=True)
    st.markdown(f'{badge_html(row["item_type"],row["status"])}', unsafe_allow_html=True)
    st.markdown(f'<div class="dtitle">{row["title"]}</div>', unsafe_allow_html=True)
    ci,cx=st.columns([1,2])
    with ci: show_photo(sb,row.get("photo_id"),use_container_width=True)
    with cx:
        info=[]
        if row.get("category"): info.append(f"**Category:** {row['category']}")
        if row.get("location"): info.append(f"**Last seen at:** {row['location']}")
        if row.get("date_occurred"): info.append(f"**Date:** {row['date_occurred']}")
        info.append(f"**Posted:** {row['date_posted'][:16].replace('T',' ')}")
        st.markdown("  \n".join(info))
    if row.get("description"): st.markdown(""); st.markdown(f"**Description**  \n{row['description']}")
    contact=[]
    if row.get("contact_name"): contact.append(f"**{row['contact_name']}**")
    if row.get("contact_email"): contact.append(row['contact_email'])
    if row.get("contact_phone"): contact.append(row['contact_phone'])
    if contact: st.markdown(""); st.markdown(f"**Contact:** {' · '.join(contact)}")
    st.markdown("---")
    if is_dev():
        c1,c2,c3,_=st.columns([1,1,1,2])
        with c1:
            if row["status"]=="open":
                if st.button("Resolve"): resolve_item(sb,iid); st.rerun()
            else:
                if st.button("Reopen"): reopen_item(sb,iid); st.rerun()
        with c2:
            if row["status"]=="open" and row["item_type"]=="found" and not is_in_auction(row) and not has_open_match(sb, row):
                if st.button("Send to Auction"):
                    if send_to_auction(sb,iid): st.success("Sent to charity auction!"); st.rerun()
                    else: st.warning("This item is not eligible for auction.")
        with c3:
            if st.button("Delete"): delete_item(sb,iid); nav("Home"); st.rerun()
    else: st.caption("Only devs can manage item status.")
    matches=get_potential_matches(sb,row)
    if matches:
        st.markdown("### Potential Matches")
        for i,m in enumerate(matches):
            st.markdown(card_html(m,i,sb), unsafe_allow_html=True)
            st.button("View",key=f"m_{m['id']}",on_click=go_detail,args=(m["id"],))

def page_auction(sb):
    breadcrumb(("Home","Home"),("Charity Auction",None))
    st.markdown("## Charity Auction House")
    st.info("**How it works:** Items unclaimed for 4 weeks enter a silent charity auction. All proceeds go to charity. Enter your email to place a bid. The winner of each auction will be decided at the **end of the school year**. One bid per person per item.")
    st.markdown("---")
    items = get_auction_items(sb)
    if not items:
        st.markdown("No items are currently up for auction. Items move here automatically after 4 weeks unclaimed, or when a dev sends them directly.")
        return
    st.caption(f"{len(items)} item{'s' if len(items)!=1 else ''} up for auction")
    for i,row in enumerate(items):
        st.markdown(card_html(row,i,sb), unsafe_allow_html=True)
        bids = get_bids(sb, row["id"])
        st.caption(f"{len(bids)} bid{'s' if len(bids)!=1 else ''} placed")
        with st.form(f"bid_{row['id']}"):
            email = st.text_input("Your email to place a bid", placeholder="you@example.com", key=f"bid_email_{row['id']}")
            go = st.form_submit_button("Place Bid", use_container_width=True)
        if go:
            if not email.strip() or "@" not in email: st.error("Enter a valid email.")
            elif has_bid(sb, row["id"], email): st.warning("You already placed a bid on this item.")
            else: place_bid(sb, row["id"], email); st.success("Bid placed! Winner announced at end of school year."); st.rerun()
        st.markdown("---")

def main():
    st.set_page_config(page_title="Lost & Found Hub",layout="centered")
    apply_styles(); sb=get_supabase()
    if not is_logged_in(): page_landing(sb); return
    if "page" not in st.session_state: st.session_state["page"]="Home"
    render_sidebar(sb)
    {"Home":lambda:page_home(sb),"Report Lost":lambda:page_post(sb,"lost"),"Report Found":lambda:page_post(sb,"found"),"Browse Lost":lambda:page_browse(sb,"lost"),"Browse Found":lambda:page_browse(sb,"found"),"Detail":lambda:page_detail(sb),"Charity Auction":lambda:page_auction(sb)}.get(st.session_state["page"],lambda:page_home(sb))()

if __name__=="__main__": main()
