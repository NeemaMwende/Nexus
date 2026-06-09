"""
Node: spam_check
Two-stage: fast keyword rules, then LLM single-word classification.
"""
import re
from agent.state import NexusState
from services.llm_client import call_llm

SPAM_KEYWORDS = [
    "buy followers", "seo services", "casino", "gambling", "crypto invest",
    "bitcoin investment", "make money fast", "click here", "free money",
    "adult content", "xxx", "viagra", "loan offer", "congratulations you won",
    "weight loss", "diet pill",
]

MIN_MESSAGE_LENGTH = 10
MAX_URLS = 3

SPAM_SYSTEM_PROMPT = """You are a spam classifier for a professional B2B technology company's contact form.
Respond with exactly ONE word: GENUINE or SPAM.

SPAM if:
- Unsolicited marketing or advertising
- Irrelevant to IT/tech/business services
- Gibberish or random characters
- Automated bot submission
- Offensive or inappropriate content

GENUINE if:
- Asking about IT products or services
- Requesting a demo, proposal, or meeting
- Job inquiry
- Partnership discussion
- Any reasonable business enquiry

Reply with ONLY: GENUINE or SPAM"""


def spam_check_node(state: NexusState) -> NexusState:
    message = state.get("message", "")
    name    = state.get("sender_name", "")
    email   = state.get("sender_email", "")

    # Stage 1: fast rules (no LLM call)
    reason = _keyword_check(message)
    if reason:
        state["is_spam"]       = True
        state["spam_reason"]   = reason
        state["status_message"] = f"Spam (rule): {reason}"
        return state

    # Stage 2: LLM classification (only if rules pass)
    user_input = f"Name: {name}\nEmail: {email}\nMessage: {message}"
    try:
        result = call_llm(SPAM_SYSTEM_PROMPT, user_input)
        is_spam = result.strip().upper().startswith("SPAM")
        state["is_spam"]       = is_spam
        state["spam_reason"]   = "LLM classified as spam" if is_spam else ""
        state["status_message"] = "Spam check: SPAM ✗" if is_spam else "Spam check: genuine ✓"
    except Exception as e:
        # LLM unavailable — default to not-spam to avoid silently dropping real enquiries
        print(f"[spam_check] LLM error: {e}, defaulting to genuine")
        state["is_spam"]     = False
        state["spam_reason"] = ""
        state["status_message"] = "Spam check: LLM unavailable, treated as genuine"

    return state


def _keyword_check(message: str) -> str:
    """Returns a reason string if spam, empty string if clean."""
    msg_lower = message.lower()

    for kw in SPAM_KEYWORDS:
        if kw in msg_lower:
            return f"keyword match: '{kw}'"

    if len(message.strip()) < MIN_MESSAGE_LENGTH:
        return f"message too short ({len(message.strip())} chars)"

    url_count = len(re.findall(r"https?://", message))
    if url_count > MAX_URLS:
        return f"too many URLs ({url_count})"

    return ""
