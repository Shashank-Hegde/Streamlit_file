import io
import re
import streamlit as st
from google.oauth2 import service_account
from googleapiclient.discovery import build

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

@st.cache_data(ttl=60)  # re-sync every 60 seconds
def list_subfolders(root_folder_id: str):
    service = get_drive_service()
    q = (
        f"'{root_folder_id}' in parents "
        "and mimeType = 'application/vnd.google-apps.folder' "
        "and trashed = false"
    )
    res = service.files().list(
        q=q,
        fields="files(id,name)",
        pageSize=1000,
        orderBy="name",
    ).execute()
    return res.get("files", [])

@st.cache_data(ttl=60)
def list_wav_files(folder_id: str):
    service = get_drive_service()
    q = (
        f"'{folder_id}' in parents "
        "and trashed = false"
    )
    # We fetch all files; filter client-side by extension for reliability
    res = service.files().list(
        q=q,
        fields="files(id,name,mimeType,size)",
        pageSize=1000,
        orderBy="name",
    ).execute()
    files = res.get("files", [])
    wavs = [f for f in files if f.get("name") and AUDIO_EXT_RE.search(f["name"])]
    return wavs

@st.cache_data(ttl=3600)  # cache audio bytes longer; Drive file ids are stable
def download_file_bytes(file_id: str) -> bytes:
    service = get_drive_service()
    req = service.files().get_media(fileId=file_id)
    fh = io.BytesIO()
    downloader = None

    # googleapiclient has MediaIoBaseDownload, but importing is optional
    from googleapiclient.http import MediaIoBaseDownload
    downloader = MediaIoBaseDownload(fh, req)

    done = False
    while not done:
        _, done = downloader.next_chunk()

    return fh.getvalue()

def audio_player_nodownload(audio_bytes: bytes, mime: str = "audio/wav"):
    """
    Tries to discourage download.
    NOTE: On the web you cannot fully prevent users from saving audio.
    """
    import base64
    b64 = base64.b64encode(audio_bytes).decode("utf-8")
    html = f"""
    <audio controls controlsList="nodownload noplaybackrate" oncontextmenu="return false" style="width: 100%;">
      <source src="data:{mime};base64,{b64}" type="{mime}">
      Your browser does not support the audio element.
    </audio>
    """
    st.components.v1.html(html, height=60)

# -----------------------------
# UI
# -----------------------------
st.title("📁 Google Drive Audio Browser (WAV)")

root_id = st.secrets.get("GDRIVE_ROOT_FOLDER_ID", None)
if not root_id:
    st.error("Missing GDRIVE_ROOT_FOLDER_ID in Streamlit secrets.")
    st.stop()

with st.sidebar:
    st.header("Browse")
    refresh = st.button("🔄 Refresh now")

if refresh:
    list_subfolders.clear()
    list_wav_files.clear()
    st.cache_data.clear()
    st.rerun()

folders = list_subfolders(root_id)
if not folders:
    st.warning("No subfolders found under the root folder (or not shared with the service account).")
    st.stop()

folder_names = [f["name"] for f in folders]
selected_name = st.sidebar.selectbox("Select audio folder", folder_names)
selected_folder = next(f for f in folders if f["name"] == selected_name)

st.subheader(f"Folder: {selected_folder['name']}")

colA, colB = st.columns([2, 1])
with colA:
    query = st.text_input("Search file name", "")
with colB:
    page_size = st.number_input("Files per page", min_value=10, max_value=200, value=50, step=10)

files = list_wav_files(selected_folder["id"])
if query.strip():
    files = [f for f in files if query.lower() in f["name"].lower()]

total = len(files)
st.caption(f"{total} WAV file(s)")

# Pagination
page_count = max(1, (total + page_size - 1) // page_size)
page = st.number_input("Page", min_value=1, max_value=page_count, value=1, step=1)
start = (page - 1) * page_size
end = min(start + page_size, total)
files_page = files[start:end]

st.divider()

for idx, f in enumerate(files_page, start=start + 1):
    with st.expander(f"{idx}. 🎧 {f['name']}", expanded=False):
        audio_bytes = download_file_bytes(f["id"])
        audio_player_nodownload(audio_bytes, mime="audio/wav")

        # Option 2: discourage download (not perfect)
        audio_bytes = download_file_bytes(f["id"])
        audio_player_nodownload(audio_bytes, mime="audio/wav")
