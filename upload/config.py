"""Guardrail + location constants for the Upload stage."""
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent

# Same convention as .auth/gemini/ (the Video stage's Chrome profile dir):
# a per-service subdirectory under the already-gitignored .auth/, rather than
# a new top-level secrets location.
YOUTUBE_CLIENT_SECRETS_PATH = _ROOT / ".auth" / "youtube" / "client_secret.json"
YOUTUBE_TOKEN_PATH = _ROOT / ".auth" / "youtube" / "token.json"

YOUTUBE_CATEGORY_ID = "22"          # People & Blogs; override per-call if needed

# Defaults to "private": an unaudited Cloud Console project forces every
# upload to private regardless of what's requested (see upload/youtube.py's
# module docstring, point 2) -- requesting "public" here wouldn't do what it
# says, so the default doesn't pretend otherwise. Flip once the project
# passes Google's compliance audit.
YOUTUBE_PRIVACY_STATUS = "private"
