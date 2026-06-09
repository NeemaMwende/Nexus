"""
Run this ONCE from the terminal to generate the OAuth2 refresh token.

    python scripts/gmail_auth.py

It opens a browser, you log in as technobrain6@gmail.com, and it saves
data/gmail_token.json. After that, Nexus uses the token silently forever
(auto-refreshes when it expires).

Required BEFORE running this:
1. Go to console.cloud.google.com
2. Create a project (or use existing)
3. Enable "Gmail API"
4. OAuth consent screen → External → add your gmail as test user
5. Credentials → Create → OAuth 2.0 Client ID → Desktop App
6. Download JSON → save as data/credentials.json
7. Run this script
"""

import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
import json

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify",   # mark read/label
]

CREDENTIALS_FILE = "data/credentials.json"
TOKEN_FILE       = "data/gmail_token.json"

os.makedirs("data", exist_ok=True)

if not os.path.exists(CREDENTIALS_FILE):
    print(f"\n✗ Missing {CREDENTIALS_FILE}")
    print("  Download your OAuth2 credentials from Google Cloud Console")
    print("  and save them as data/credentials.json\n")
    sys.exit(1)

creds = None
if os.path.exists(TOKEN_FILE):
    creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

if not creds or not creds.valid:
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        print("✓ Token refreshed")
    else:
        flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
        creds = flow.run_local_server(port=0)
        print("✓ Authentication successful")

    with open(TOKEN_FILE, "w") as f:
        f.write(creds.to_json())
    print(f"✓ Token saved to {TOKEN_FILE}")
else:
    print("✓ Token already valid")

# Test it
from googleapiclient.discovery import build
service = build("gmail", "v1", credentials=creds)
profile = service.users().getProfile(userId="me").execute()
print(f"✓ Connected as: {profile['emailAddress']}")
print(f"  Total messages: {profile['messagesTotal']}")
print("\nSetup complete. Nexus can now access Gmail.")
