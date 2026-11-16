import re
import streamlit as st
from google.oauth2 import service_account
from google.auth.transport.requests import Request as GoogleAuthRequest
from googleapiclient.discovery import build

st.set_page_config(page_title="GDrive Audio Browser", layout="wide")

AUDIO_EXT_RE = re.compile(r"\.(wav|wave)$", re.IGNORECASE)


# -----------------------------
# Drive helpers
# -----------------------------

def get_drive_service():
    creds_dict = dict(st.secrets["gcp_service_account"])
    creds = service_account.Credentials.from_service_account_info(
        creds_dict,
        scopes=["https://www.googleapis.com/auth/drive.readonly"],
    )
    return build("drive", "v3", credentials=creds, cache_discovery=False)


@st.cache_data(ttl=3000)   # OAuth tokens live ~60 min; refresh at 50 min
def _get_access_token() -> str:
    """Return a short-lived OAuth access token for Drive streaming URLs."""
    creds_dict = dict(st.secrets["gcp_service_account"])
    creds = service_account.Credentials.from_service_account_info(
        creds_dict,
        scopes=["https://www.googleapis.com/auth/drive.readonly"],
    )
    creds.refresh(GoogleAuthRequest())
    return creds.token


def stream_url(file_id: str) -> str:
    """Direct Drive streaming URL — browser fetches only while playing."""
    token = _get_access_token()
    return (
        f"https://www.googleapis.com/drive/v3/files/{file_id}"
        f"?alt=media&access_token={token}"
    )


@st.cache_data(ttl=60)
def list_children(folder_id: str):
    """Return (subfolders, wav_files) for folder_id. Cached 60 s."""
    service = get_drive_service()
    q = f"'{folder_id}' in parents and trashed = false"
    all_items, page_token = [], None
    while True:
        res = service.files().list(
            q=q,
            fields="nextPageToken, files(id,name,mimeType,size)",
            pageSize=1000,
            orderBy="name",
            pageToken=page_token,
        ).execute()
        all_items.extend(res.get("files", []))
        page_token = res.get("nextPageToken")
        if not page_token:
            break

    subfolders = [
        f for f in all_items
        if f.get("mimeType") == "application/vnd.google-apps.folder"
    ]
    wavs = [
        f for f in all_items
        if f.get("name") and AUDIO_EXT_RE.search(f["name"])
    ]
    return subfolders, wavs


def audio_player(file_id: str, mime: str = "audio/wav"):
    """
    Render an audio player that streams from Drive.
    preload='none' means zero bytes are fetched until the user hits play.
    No Python-side download at all.
    """
    url = stream_url(file_id)
    html = f"""
    <audio controls controlsList="nodownload noplaybackrate"
           oncontextmenu="return false" style="width:100%;" preload="none">
      <source src="{url}" type="{mime}">
    </audio>
    """
    st.components.v1.html(html, height=60)


# -----------------------------
# Recursive sidebar tree
# -----------------------------

def render_tree(folder_id: str, folder_name: str, depth: int = 0):
    expanded_set = st.session_state.setdefault("expanded", set())
    is_expanded = folder_id in expanded_set
    is_selected = st.session_state.get("sel_id") == folder_id

    indent = " " * (depth * 4)
    arrow  = "▾" if is_expanded else "▸"
    icon   = "📂" if is_expanded else "📁"

    c1, c2 = st.sidebar.columns([1, 7])
    with c1:
        if st.button(arrow, key=f"tog_{folder_id}"):
            if is_expanded:
                expanded_set.discard(folder_id)
            else:
                expanded_set.add(folder_id)
                list_children(folder_id)
            st.rerun()
    with c2:
        label = f"{indent}{icon} {folder_name}"
        btn_type = "primary" if is_selected else "secondary"
        if st.button(label, key=f"sel_{folder_id}", type=btn_type):
            st.session_state["sel_id"]   = folder_id
            st.session_state["sel_name"] = folder_name
            expanded_set.add(folder_id)
            list_children(folder_id)
            st.rerun()

    if is_expanded:
        subfolders, _ = list_children(folder_id)
        for sf in subfolders:
            render_tree(sf["id"], sf["name"], depth + 1)


# -----------------------------
# Session state init
# -----------------------------
root_id = st.secrets.get("GDRIVE_ROOT_FOLDER_ID", None)
if not root_id:
    st.error("Missing GDRIVE_ROOT_FOLDER_ID in Streamlit secrets.")
    st.stop()

st.session_state.setdefault("sel_id",   None)
st.session_state.setdefault("sel_name", "")
st.session_state.setdefault("expanded", set())

# -----------------------------
# Sidebar
# -----------------------------
with st.sidebar:
    st.header("📂 Folder Tree")
    if st.button("🔄 Refresh"):
        st.cache_data.clear()
        st.session_state["expanded"] = set()
        st.rerun()
    st.divider()

    root_selected = st.session_state.get("sel_id") == root_id
    if st.button(
        "🗂️  Root",
        key="sel_root",
        type="primary" if root_selected else "secondary",
    ):
        st.session_state["sel_id"]   = root_id
        st.session_state["sel_name"] = "Root"
        st.rerun()

    root_subfolders, _ = list_children(root_id)
    for folder in root_subfolders:
        render_tree(folder["id"], folder["name"], depth=0)


# -----------------------------
# Main panel
# -----------------------------
st.title("📁 Google Drive Audio Browser")

sel_id   = st.session_state.get("sel_id")
sel_name = st.session_state.get("sel_name", "")

if not sel_id:
    st.info("👈 Select a folder from the sidebar to browse its WAV files.")
    st.stop()

st.subheader(f"📂 {sel_name}")

_, files = list_children(sel_id)

colA, colB = st.columns([2, 1])
with colA:
    query = st.text_input("🔍 Search file name", "")
with colB:
    page_size = st.number_input("Files per page", min_value=10, max_value=1500, value=50, step=10)

if query.strip():
    files = [f for f in files if query.strip().lower() in f["name"].lower()]

total = len(files)
st.caption(f"{total} WAV file(s)")

if not files:
    st.info("No WAV files directly in this folder. Expand a sub-folder in the sidebar to navigate deeper.")
    st.stop()

page_count = max(1, (total + page_size - 1) // page_size)
page = st.number_input("Page", min_value=1, max_value=page_count, value=1, step=1)
start = (page - 1) * page_size
end   = min(start + page_size, total)

st.divider()

for idx, f in enumerate(files[start:end], start=start + 1):
    size_str = ""
    if f.get("size"):
        size_mb = int(f["size"]) / 1_048_576
        size_str = f"  —  {size_mb:.2f} MB"

    with st.expander(f"#{idx}. 🎧 {f['name']}{size_str}", expanded=False):
        # Just renders an <audio> tag pointing at a Drive URL.
        # Zero bytes transferred until the user presses play.
        audio_player(f["id"], mime="audio/wav")
