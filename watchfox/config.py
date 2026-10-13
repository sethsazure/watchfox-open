import os
import secrets as _secrets
from pathlib import Path

from dotenv import load_dotenv

HOME = Path(os.getenv("WATCHFOX_HOME", Path.home() / ".watchfox"))
HOME.mkdir(parents=True, exist_ok=True)
load_dotenv(Path.cwd() / ".env")
load_dotenv(HOME / ".env")

# Local OAuth redirects use http://localhost; the engine hangs on load when TensorFlow is installed
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")
os.environ.setdefault("USE_TF", "0")

HOST = os.getenv("WATCHFOX_HOST", "127.0.0.1")
PORT = int(os.getenv("WATCHFOX_PORT", "8787"))
BASE_URL = os.getenv("WATCHFOX_URL", f"http://localhost:{PORT}").rstrip("/")
if BASE_URL.startswith("http://"):
    os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")   # only ever true for http (local) deployments
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()        # postgresql://... for PostgreSQL; empty uses a local SQLite file
DB_PATH = Path(os.getenv("WATCHFOX_DB", HOME / "watchfox.db"))
WEB_DIST = Path(os.getenv("WATCHFOX_WEB") or Path(__file__).resolve().parent.parent / "web" / "dist")

GRAPH_URL = os.getenv("WATCHFOX_GRAPH_URL", "https://graph.microsoft.com/v1.0").rstrip("/")
PUSH_ALLOW_HTTP = os.getenv("WATCHFOX_PUSH_ALLOW_HTTP", "0") == "1"   # tests only: Graph itself requires a public https address

MODEL = os.getenv("WATCHFOX_MODEL", "typed-decisions")   # internal: which engine checkpoint serves requests


def _secret() -> str:
    """Key for encrypting stored credentials and signing sessions. Set WATCHFOX_SECRET in production."""
    env = os.getenv("WATCHFOX_SECRET", "").strip()
    if env:
        return env
    if os.getenv("DATABASE_URL", "").strip():             # a hosted install: a secret kept on a disposable disk would lock everyone out on redeploy
        raise SystemExit("WATCHFOX_SECRET must be set when DATABASE_URL is set (it encrypts mailbox credentials and signs sessions).")
    f = HOME / "secret"
    if not f.exists():
        f.write_text(_secrets.token_urlsafe(48))
        f.chmod(0o600)
    return f.read_text().strip()


SECRET = _secret()
SIGNUP_OPEN = os.getenv("WATCHFOX_SIGNUP", "1") != "0"
ALLOW_INSECURE_WEBHOOKS = os.getenv("WATCHFOX_ALLOW_INSECURE_WEBHOOKS", "0") == "1"   # http:// and private addresses (dev only)
MAX_CONNECTIONS = int(os.getenv("WATCHFOX_MAX_CONNECTIONS", "5"))
MAX_WATCHES = int(os.getenv("WATCHFOX_MAX_WATCHES", "10"))
MAX_DOWNLOAD_BYTES = int(float(os.getenv("WATCHFOX_MAX_DOWNLOAD_MB", "100")) * 1024 * 1024)   # largest single email that can be downloaded
INLINE_MAX_BYTES = int(float(os.getenv("WATCHFOX_INLINE_MAX_MB", "5")) * 1024 * 1024)   # most a webhook carries inline (.eml and files together)
NEW_ACCOUNTS_PER_IP_DAY = int(os.getenv("WATCHFOX_NEW_ACCOUNTS_PER_IP_DAY", "3"))        # new accounts that may start from one network address a day
NEW_ACCOUNTS_PER_HOUR = int(os.getenv("WATCHFOX_NEW_ACCOUNTS_PER_HOUR", "100"))         # new accounts for the whole service per hour (a brake on bot floods)
GITHUB_MIN_AGE_DAYS = float(os.getenv("WATCHFOX_GITHUB_MIN_AGE_DAYS", "3"))             # younger GitHub accounts cannot create a new account
FREE_PER_IP_DAY = int(os.getenv("WATCHFOX_FREE_PER_IP_DAY", "300"))                     # test calls and sandbox emails a day from one address across free accounts
ADMIN_EMAILS = [e.strip().lower() for e in os.getenv("WATCHFOX_ADMIN_EMAILS", "").split(",") if e.strip()]   # who may open the admin console (empty = no admin console)
ADMIN_PROVIDER = os.getenv("WATCHFOX_ADMIN_PROVIDER", "github")        # the admin must also be signed in through this provider ("" = any)
API_PER_MIN = int(os.getenv("WATCHFOX_API_PER_MIN", "1200"))              # requests per minute per account through API keys
CLASSIFY_PER_MIN = int(os.getenv("WATCHFOX_CLASSIFY_PER_MIN", "120"))

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "").strip()
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "").strip()
MS_CLIENT_ID = os.getenv("MS_CLIENT_ID", "").strip()
MS_CLIENT_SECRET = os.getenv("MS_CLIENT_SECRET", "").strip()   # recommended: a server-side app is a confidential client
MS_TENANT = os.getenv("MS_TENANT", "common").strip() or "common"

# Optional AI assistant for the type wizard. It only ever sees the description a person types, never email content.
AZURE_AI_ENDPOINT = os.getenv("AZURE_AI_ENDPOINT", "").rstrip("/")       # Azure AI Foundry resource, e.g. https://name.cognitiveservices.azure.com
AZURE_AI_KEY = os.getenv("AZURE_AI_KEY", "")
AZURE_AI_DEPLOYMENT = os.getenv("AZURE_AI_DEPLOYMENT", "tuning")        # the deployment name in Foundry; used instead of OpenAI when the endpoint and key are set
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
TUNING_MODEL = os.getenv("WATCHFOX_TUNING_MODEL", "gpt-4o-mini")
MAX_TYPES = int(os.getenv("WATCHFOX_MAX_TYPES", "20"))

LOG_RETENTION_DAYS = int(os.getenv("WATCHFOX_LOG_RETENTION_DAYS", "90"))      # activity log rows older than this are deleted

# Accounts, plans and email
ENFORCE_PLANS = os.getenv("WATCHFOX_ENFORCE_PLANS", "1") != "0"        # off only for tests and private installs
SMTP_HOST = os.getenv("SMTP_HOST", "").strip()
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "").strip()
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM = os.getenv("SMTP_FROM", "WatchFox <no-reply@watchfox.local>")
# With no mail server on a local install, links that would be emailed are also shown on screen and saved to ~/.watchfox/outbox
DEV_LINKS = (not SMTP_HOST) and BASE_URL.startswith("http://localhost")
GITHUB_CLIENT_ID = os.getenv("GITHUB_CLIENT_ID", "").strip()
GITHUB_CLIENT_SECRET = os.getenv("GITHUB_CLIENT_SECRET", "").strip()

# Where the Laya model runs. Set LAYA_URL to use a remote laya-serve (for example over Tailscale); leave it empty to run the
# model on this machine. The key is sent as "Authorization: Bearer ..." and must only ever live in .env.
LAYA_URL = os.getenv("LAYA_URL", "").strip().rstrip("/")
LAYA_API_KEY = os.getenv("LAYA_API_KEY", "").strip()
LAYA_TIMEOUT = float(os.getenv("LAYA_TIMEOUT", "30"))
LAYA_BATCH_MAX = min(64, int(os.getenv("LAYA_BATCH_MAX", "64")))      # the server accepts at most 64 states / questions per call

# Backup for when the main Laya server cannot be reached: the same model through Vercel's AI Gateway (TypeSafe-compatible API).
# Email text is sent to that service while it is in use. Leave LAYA_FALLBACK_KEY empty to switch the backup off.
LAYA_FALLBACK_URL = os.getenv("LAYA_FALLBACK_URL", "https://ai-gateway.vercel.sh/typesafe").strip().rstrip("/")
LAYA_FALLBACK_KEY = os.getenv("LAYA_FALLBACK_KEY", "").strip()
LAYA_FALLBACK_MODEL = os.getenv("LAYA_FALLBACK_MODEL", "convaiinnovations/laya").strip()
LAYA_FALLBACK_RPM = int(os.getenv("LAYA_FALLBACK_RPM", "5"))                  # requests a minute the backup service allows
LAYA_QUEUE_MAX_WAIT = float(os.getenv("LAYA_QUEUE_MAX_WAIT", "3600"))         # longest an email waits for the backup before it is retried later
LAYA_PROXY = os.getenv("LAYA_PROXY", "").strip()       # e.g. socks5://127.0.0.1:1055: reach the agent server through a Tailscale userspace proxy (set by the container)
# Email-and-password accounts are off by default: people sign in with GitHub, Google or Microsoft only (nothing to reset or maintain).
# Turn on for local development without sign-in providers, and in tests.
PASSWORD_LOGIN = os.getenv("WATCHFOX_PASSWORD_LOGIN", "0") == "1"
# Sending email through Cloudflare Email Service (REST API). Needs the domain onboarded for Email Sending and a token with "Email Sending: Edit".
CF_ACCOUNT_ID = os.getenv("CLOUDFLARE_ACCOUNT_ID", "").strip()
CF_EMAIL_TOKEN = os.getenv("CLOUDFLARE_EMAIL_TOKEN", "").strip()
EMAIL_FROM_ADDRESS = os.getenv("EMAIL_FROM_ADDRESS", "notifications@watchfox.run").strip()
EMAIL_ENABLED = bool(CF_ACCOUNT_ID and CF_EMAIL_TOKEN) or bool(SMTP_HOST)      # can WatchFox send email at all?
SIGNUPS_PER_HOUR = int(os.getenv("WATCHFOX_SIGNUPS_PER_HOUR", "10"))        # password sign-ups per address per hour (only when password accounts are on)
# Helcim payments (subscriptions). Test-mode price: for the emails listed, subscriptions are created at HELCIM_TEST_AMOUNT so the whole flow can be tried cheaply.
HELCIM_API_TOKEN = os.getenv("HELCIM_API_TOKEN", "").strip()
HELCIM_TEST_EMAILS = [e.strip().lower() for e in os.getenv("HELCIM_TEST_EMAILS", "").split(",") if e.strip()]
HELCIM_TEST_AMOUNT = float(os.getenv("HELCIM_TEST_AMOUNT", "1.00"))
