"""YouTube Data API v3 publishing: OAuth, resumable upload, engagement comment.

Three constraints from the real API, confirmed against Google's own documentation
before this module was written, that change what it can actually deliver versus what a
generic "build a YouTube uploader" brief tends to assume:

1. **There is no comment-pin endpoint.** `comments.setModerationStatus` accepts only
   `heldForReview`, `published`, `rejected` -- pinning a comment is a YouTube Studio /
   mobile-app-only action, not part of Data API v3 at all. `post_engagement_comment`
   below posts a comment (which still seeds the early-reply signal that's the point of
   pinning) but does not and cannot pin it -- named accordingly, so the code doesn't
   claim a capability it doesn't have. Pin it manually in Studio if you want that too.

2. **An unaudited API project locks every upload to private.** Any Cloud Console
   project created after 2020-07-28 that hasn't passed Google's compliance audit has
   all `videos.insert` results restricted to private viewing, regardless of the
   `privacyStatus` requested -- see
   https://developers.google.com/youtube/v3/guides/quota_and_compliance_audits. A brand
   new project (the default state here) is unaudited. `config.YOUTUBE_PRIVACY_STATUS`
   defaults to `"private"` accordingly, not `"public"` -- requesting public on an
   unaudited project doesn't fail, it just silently doesn't do what was asked.

3. **OAuth's first run needs a real, visible browser.** `InstalledAppFlow.run_local_server`
   opens one and blocks until you approve access -- it must never be called
   automatically (e.g. from `main.py`'s `pipeline` verb, which chains every other
   stage): no browser to open there, nothing to block on cleanly. It's only ever
   invoked directly via `python main.py authorize-youtube`, the same way this
   project's Gemini sign-in step (`video/browser.py`) is a manual, one-time action
   outside the automated pipeline. Once `config.YOUTUBE_TOKEN_PATH` exists, later runs
   refresh silently with no browser at all.

4. **A private video cannot receive comments through the API.** Confirmed by direct
   test, not inferred from docs: `commentThreads.insert` against a video whose
   `privacyStatus` was still `"private"` returned a 403 `insufficientPermissions`
   ("might not be properly authorized") on a token with the correct scope, on the
   uploader's own video -- and the identical call against the identical video/comment
   text succeeded the instant YouTube's scheduled `publishAt` flipped it to `"public"`.
   This is a real consequence of `config.YOUTUBE_PRIVACY_STATUS` defaulting to
   `"private"` (see point 2): posting a comment immediately after a private upload
   will 403 on every run under the default flow. It fails harmlessly (logged, never
   raises), but it will never succeed there either. `main.py`'s `upload` verb skips
   the attempt entirely when the upload is private; the `comment` verb is the
   workaround -- run it by hand once a video has actually gone public.
"""

import logging
import re
import time
from pathlib import Path

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

from upload.config import YOUTUBE_CATEGORY_ID, YOUTUBE_CLIENT_SECRETS_PATH, YOUTUBE_TOKEN_PATH

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.force-ssl",
]

# HTTP statuses worth retrying with backoff -- transient server-side/network trouble,
# as opposed to a 4xx (bad request, auth, quota) that retrying identically won't fix.
RETRIABLE_STATUS_CODES = frozenset({500, 502, 503, 504})
MAX_UPLOAD_RETRIES = 5


class UploadAuthError(RuntimeError):
    """No valid credentials, and this call has no browser to open one with."""


def get_authenticated_service(
    token_path: Path = None, client_secrets_path: Path = None
):
    """Load or refresh credentials -> an authorized `youtube` API resource.

    Never opens a browser itself. If `token_path` doesn't exist yet, the caller is
    expected to run `python main.py authorize-youtube` first -- see run_oauth_flow()
    below. This raises rather than attempting the browser flow itself (see module
    docstring point 3).
    """
    token_path = Path(token_path or YOUTUBE_TOKEN_PATH)
    client_secrets_path = Path(client_secrets_path or YOUTUBE_CLIENT_SECRETS_PATH)

    if not token_path.exists():
        raise UploadAuthError(
            f"No credentials at {token_path}. Run the one-time authorization first:\n"
            f"  python main.py authorize-youtube\n"
            f"(needs {client_secrets_path}, downloaded from Cloud Console -- see "
            "CLAUDE.md's Upload section)"
        )

    creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if not creds.valid:
        if creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except RefreshError as e:
                # A refresh token can exist but still be dead -- revoked,
                # expired from inactivity, or issued for a different OAuth
                # client than the one in client_secrets_path now. Caught
                # live: Google returns invalid_grant with no further detail.
                raise UploadAuthError(
                    f"Refresh failed for {token_path} ({e}). The stored refresh "
                    "token is no longer valid. Delete the file and re-run: "
                    "python main.py authorize-youtube"
                ) from e
            token_path.write_text(creds.to_json(), encoding="utf-8")
            logger.info("Refreshed YouTube credentials at %s", token_path)
        else:
            raise UploadAuthError(
                f"Credentials at {token_path} are invalid and have no refresh token. "
                "Delete the file and re-run: python main.py authorize-youtube"
            )

    return build("youtube", "v3", credentials=creds)


def run_oauth_flow(token_path: Path = None, client_secrets_path: Path = None) -> None:
    """The one-time interactive step: opens a browser, blocks on approval, writes
    `token_path`. Call only from `python main.py authorize-youtube` -- see module
    docstring point 3.
    """
    token_path = Path(token_path or YOUTUBE_TOKEN_PATH)
    client_secrets_path = Path(client_secrets_path or YOUTUBE_CLIENT_SECRETS_PATH)

    if not client_secrets_path.exists():
        raise FileNotFoundError(
            f"Missing {client_secrets_path}. Download an OAuth client (Desktop app "
            "type) from Cloud Console -> APIs & Services -> Credentials, save it there."
        )

    flow = InstalledAppFlow.from_client_secrets_file(str(client_secrets_path), SCOPES)
    creds = flow.run_local_server(port=0)
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(creds.to_json(), encoding="utf-8")
    logger.info("Wrote YouTube credentials to %s", token_path)


def title_from_filename(video_path) -> tuple:
    """Finished shorts are named <slug>_<12-hex-id>.mp4 (mux/config.py) -- the one
    piece of the short's identity that's still around whenever Upload actually runs,
    since the ScriptPackage that produced it may long since have been overwritten by
    a later topic (generic-scene-id intermediates are overwritten per run; the
    content-addressed short filename is not). Cheap, dependency-free stand-in for a
    real title/description until this needs to be smarter than that. #Shorts is
    required by YouTube for a vertical upload to be classified as a Short.
    """
    slug = re.sub(r"_[0-9a-f]{12}$", "", Path(video_path).stem)
    name = slug.replace("-", " ").replace("_", " ").title()
    title = f"{name} #Shorts"[:100]
    description = f"{name}\n\n#Shorts"
    tags = [w for w in slug.split("-") if len(w) > 3][:10] + ["shorts"]
    return title, description, tags


def upload_short(
    youtube,
    video_path: str,
    title: str,
    description: str,
    tags: list,
    privacy_status: str,
    publish_at: str = None,
    category_id: str = None,
) -> str:
    """Resumable upload -> YouTube video id.

    `publish_at` (an ISO 8601 timestamp) only has an effect when `privacy_status` is
    `"private"` -- that's the API's own rule, not one enforced here, so passing it
    alongside `"public"` is silently ignored by YouTube rather than validated here.
    """
    path = Path(video_path)
    if not path.exists():
        raise FileNotFoundError(f"Video not found: {video_path}")

    status_body = {"privacyStatus": privacy_status, "selfDeclaredMadeForKids": False}
    if publish_at and privacy_status == "private":
        status_body["publishAt"] = publish_at

    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags,
            "categoryId": category_id or YOUTUBE_CATEGORY_ID,
        },
        "status": status_body,
    }

    media = MediaFileUpload(str(path), chunksize=-1, resumable=True, mimetype="video/mp4")
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

    logger.info("Starting upload: %r (%s)", title, video_path)
    response = None
    retry = 0
    while response is None:
        try:
            status, response = request.next_chunk()
            if status:
                logger.info("Uploading %s: %d%%", path.name, int(status.progress() * 100))
        except HttpError as e:
            if e.resp.status in RETRIABLE_STATUS_CODES and retry < MAX_UPLOAD_RETRIES:
                retry += 1
                sleep_seconds = 2**retry
                logger.warning(
                    "Transient upload error %s for %r; retry %d/%d in %ds",
                    e.resp.status, video_path, retry, MAX_UPLOAD_RETRIES, sleep_seconds,
                )
                time.sleep(sleep_seconds)
            else:
                raise

    video_id = response["id"]
    logger.info("Uploaded %r -> https://youtube.com/shorts/%s", video_path, video_id)
    return video_id


def post_engagement_comment(youtube, video_id: str, comment_text: str) -> str:
    """Post a top-level comment to seed early engagement. Cannot pin it -- see module
    docstring point 1. Returns the comment thread id, or None on failure: a video that
    uploaded successfully but didn't get a comment is still a successful publish, so
    this is never allowed to raise into the caller.
    """
    try:
        response = (
            youtube.commentThreads()
            .insert(
                part="snippet",
                body={
                    "snippet": {
                        "videoId": video_id,
                        "topLevelComment": {"snippet": {"textOriginal": comment_text}},
                    }
                },
            )
            .execute()
        )
        thread_id = response.get("id")
        logger.info("Posted engagement comment on %s (thread %s)", video_id, thread_id)
        return thread_id
    except HttpError as e:
        logger.warning("Could not post engagement comment on %s: %s", video_id, e)
        return None
