from typing import TypedDict, Optional, List
from enum import Enum


class Intent(str, Enum):
    PRODUCT_INQUIRY   = "product_inquiry"
    DEMO_REQUEST      = "demo_request"
    PARTNERSHIP       = "partnership"
    JOB_APPLICATION   = "job_application"
    SUPPORT_REQUEST   = "support_request"
    GENERAL           = "general_information"
    SPAM              = "spam"
    UNKNOWN           = "unknown"


class NexusState(TypedDict):
    # Source email from Gmail
    email_id:        str
    thread_id:       str
    subject:         str
    sender_address:  str     # raw "From" header
    raw_html:        str
    raw_text:        str

    # DB row ID (from Next.js contact_messages table)
    submission_id:   Optional[int]

    # Parsed form fields
    sender_name:     str
    sender_email:    str
    sender_phone:    str
    message:         str
    product_interest: str

    # Validation
    is_valid_email:  bool
    is_valid_phone:  bool

    # Spam check
    is_spam:         bool
    spam_reason:     str

    # Intent
    intent:          str          # one of Intent values
    intent_confidence: float

    # RAG
    rag_chunks:      List[str]    # retrieved context passages

    # Reply
    draft_reply_html: str
    draft_reply_text: str

    # Status
    reply_sent:      bool
    error:           Optional[str]

    # SSE broadcast (not persisted — used to push live updates to admin UI)
    status_message:  str
