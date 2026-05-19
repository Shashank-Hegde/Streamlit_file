import io
import re
import base64
import streamlit as st
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

st.set_page_config(page_title="GDrive Audio Browser", layout="wide")

# -----------------------------
# Helpers
# -----------------------------
AUDIO_EXT_RE = re.compile(r"\.(wav|wave)$", re.IGNORECASE)


def get_drive_service():
    creds_dict = dict(st.secrets["gcp_service_account"])
    creds = service_account.Credentials.from_service_account_info(
        creds_dict,
        scopes=["https://www.googleapis.com/auth/drive.readonly"],
    )
    return build("drive", "v3", credentials=creds, cache_discovery=False)


@st.cache_data(ttl=60)
def list_children(folder_id: str):
    """
    Returns (subfolders, wav_files) for the given folder_id.
    subfolders: list of {id, name}
    wav_files:  list of {id, name, mimeType, size}
    """
    service = get_drive_service()
    q = f"'{folder_id}' in parents and trashed = false"

    all_items = []
    page_token = None
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
    wav_files = [
        f for f in all_items
        if f.get("name") and AUDIO_EXT_RE.search(f["name"])
    ]
    return subfolders, wav_files


@st.cache_data(ttl=3600)
def download_file_bytes(file_id: str) -> bytes:
    service = get_drive_service()
    req = service.files().get_media(fileId=file_id)
    fh = io.BytesIO()
    downloader = MediaIoBaseDownload(fh, req)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return fh.getvalue()


def audio_player_nodownload(audio_bytes: bytes, mime: str = "audio/wav"):
    b64 = base64.b64encode(audio_bytes).decode("utf-8")
    html = f"""
    <audio controls controlsList="nodownload noplaybackrate"
           oncontextmenu="return false" style="width:100%;">
      <source src="data:{mime};base64,{b64}" type="{mime}">
      Your browser does not support the audio element.
    </audio>
    """
    st.components.v1.html(html, height=60)


# -----------------------------
# Recursive sidebar tree
# -----------------------------

def render_folder_tree(folder_id: str, folder_name: str, depth: int = 0):
    """
    Renders the folder tree in the sidebar using st.expander for nesting.
    Returns the (folder_id, folder_name) of the folder the user clicked,
    stored in st.session_state["selected_folder"].
    """
    subfolders, wav_files = list_children(folder_id)
    has_children = bool(subfolders) or bool(wav_files)

    indent = "&nbsp;" * (depth * 4)

    if has_children:
        # Use an expander for folders that have children
        label = f"{'📂' if depth > 0 else '🗂️'} {folder_name}"
        with st.sidebar.expander(label, expanded=(depth == 0)):
            # Clickable button to select this folder's audio
            if wav_files:
                btn_label = f"📋 Show {len(wav_files)} WAV file(s) here"
                if st.button(btn_label, key=f"btn_{folder_id}"):
                    st.session_state["selected_folder_id"] = folder_id
                    st.session_state["selected_folder_name"] = folder_name

            for subfolder in subfolders:
                render_folder_tree(subfolder["id"], subfolder["name"], depth + 1)
    else:
        # Leaf folder — just a button
        btn_label = f"📁 {folder_name}"
        if st.sidebar.button(btn_label, key=f"btn_{folder_id}"):
            st.session_state["selected_folder_id"] = folder_id
            st.session_state["selected_folder_name"] = folder_name


def collect_all_wavs(folder_id: str) -> list:
    """Recursively collect all WAV files under folder_id."""
    subfolders, wav_files = list_children(folder_id)
    result = list(wav_files)
    for sf in subfolders:
        result.extend(collect_all_wavs(sf["id"]))
    return result


# -----------------------------
# UI
# -----------------------------
st.title("📁 Google Drive Audio Browser")

root_id = st.secrets.get("GDRIVE_ROOT_FOLDER_ID", None)
if not root_id:
    st.error("Missing GDRIVE_ROOT_FOLDER_ID in Streamlit secrets.")
    st.stop()

# Session state defaults
if "selected_folder_id" not in st.session_state:
    st.session_state["selected_folder_id"] = root_id
    st.session_state["selected_folder_name"] = "Root"

# --- Sidebar ---
with st.sidebar:
    st.header("📂 Folder Tree")
    if st.button("🔄 Refresh"):
        st.cache_data.clear()
        st.rerun()

    st.divider()

    # Render root-level subfolders as the tree
    root_subfolders, root_wavs = list_children(root_id)

    # Option to view root-level WAVs directly
    if root_wavs:
        if st.button(f"🗂️ Root ({len(root_wavs)} WAV files)"):
            st.session_state["selected_folder_id"] = root_id
            st.session_state["selected_folder_name"] = "Root"

    for folder in root_subfolders:
        render_folder_tree(folder["id"], folder["name"], depth=1)

# --- Main panel ---
selected_folder_id = st.session_state["selected_folder_id"]
selected_folder_name = st.session_state["selected_folder_name"]

st.subheader(f"📂 {selected_folder_name}")

# Toggle: show only direct files or recurse into subfolders
include_subfolders = st.checkbox("Include files from sub-folders", value=False)

if include_subfolders:
    _, direct_wavs = list_children(selected_folder_id)
    files = collect_all_wavs(selected_folder_id)
else:
    _, files = list_children(selected_folder_id)

# Search
colA, colB = st.columns([2, 1])
with colA:
    query = st.text_input("🔍 Search file name", "")
with colB:
    page_size = st.number_input("Files per page", min_value=10, max_value=200, value=50, step=10)

if query.strip():
    q = query.strip().lower()
    files = [f for f in files if q in f["name"].lower()]

total = len(files)
st.caption(f"{total} WAV file(s) found")

if not files:
    st.info("No WAV files in this folder. Select a folder from the sidebar or enable 'Include files from sub-folders'.")
    st.stop()

# Pagination
page_count = max(1, (total + page_size - 1) // page_size)
page = st.number_input("Page", min_value=1, max_value=page_count, value=1, step=1)
start = (page - 1) * page_size
end = min(start + page_size, total)
files_page = files[start:end]

st.divider()

for idx, f in enumerate(files_page, start=start + 1):
    file_id = f["id"]
    file_name = f["name"]
    with st.expander(f"#{idx}. 🎧 {file_name}", expanded=False):
        audio_bytes = download_file_bytes(file_id)
        audio_player_nodownload(audio_bytes, mime="audio/wav")
