import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "data", "grok.db")
SIGNUP_URL = "https://accounts.x.ai/sign-up?redirect=grok-com"
REDIRECT_URI = "http://localhost:53682"
AUTHORIZE_URL = "https://login.live.com/oauth20_authorize.srf"
TOKEN_URL = "https://login.live.com/oauth20_token.srf"
SCOPES = "offline_access https://graph.microsoft.com/Mail.Read https://graph.microsoft.com/User.Read"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 5000
OAUTH_TIMEOUT = 120
