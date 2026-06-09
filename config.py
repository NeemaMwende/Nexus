from dotenv import load_dotenv
import os

load_dotenv()

# Gmail
GMAIL_CLIENT_ID        = os.getenv("GMAIL_CLIENT_ID", "")
GMAIL_CLIENT_SECRET    = os.getenv("GMAIL_CLIENT_SECRET", "")
GMAIL_TOKEN_FILE       = os.getenv("GMAIL_TOKEN_FILE", "data/gmail_token.json")
GMAIL_MONITORED_ACCOUNT= os.getenv("GMAIL_MONITORED_ACCOUNT", "technobrain6@gmail.com")
GMAIL_LABEL            = os.getenv("GMAIL_LABEL", "nexus-contact-form")

# LLM
LLM_PROVIDER   = os.getenv("LLM_PROVIDER", "groq")
GROQ_API_KEY   = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL     = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
OLLAMA_BASE_URL= os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL   = os.getenv("OLLAMA_MODEL", "qwen2.5:14b")

# Qdrant
QDRANT_HOST       = os.getenv("QDRANT_HOST", "localhost")
QDRANT_PORT       = int(os.getenv("QDRANT_PORT", "6333"))
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "salesmate_knowledge")
EMBED_MODEL       = os.getenv("EMBED_MODEL", "nomic-embed-text")
EMBED_DIM         = int(os.getenv("EMBED_DIM", "768"))

# Salesmate
SALESMATE_API_KEY  = os.getenv("SALESMATE_API_KEY", "")
SALESMATE_LINKNAME = os.getenv("SALESMATE_LINKNAME", "technobrain.salesmate.io")

# Database
DATABASE_URL = os.getenv("DATABASE_URL", "")

# Form API
FORM_API_URL = os.getenv("FORM_API_URL", "http://localhost:3000")
FORM_API_KEY = os.getenv("FORM_API_KEY", "")

# Server
NEXUS_PORT      = int(os.getenv("NEXUS_PORT", "8000"))
NEXUS_SECRET    = os.getenv("NEXUS_SECRET", "change-me")
NEXUS_PUBLIC_URL= os.getenv("NEXUS_PUBLIC_URL", "")
POLL_INTERVAL   = int(os.getenv("POLL_INTERVAL", "60"))
