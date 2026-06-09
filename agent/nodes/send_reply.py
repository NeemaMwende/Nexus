"""
Node: send_reply
Sends the drafted reply via Gmail API, marks original as read, updates DB.
"""
from agent.state import NexusState
from services.gmail_client import gmail
from services import db


def send_reply_node(state: NexusState) -> NexusState:
    to_addr    = state.get("sender_email", "")
    name       = state.get("sender_name", "")
    html_body  = state.get("draft_reply_html", "")
    text_body  = state.get("draft_reply_text", "")
    thread_id  = state.get("thread_id", None)
    email_id   = state.get("email_id", "")
    sub_id     = state.get("submission_id")
    intent     = state.get("intent", "")

    if not to_addr or not html_body:
        state["reply_sent"]       = False
        state["error"]            = "Missing recipient or body — reply not sent"
        state["status_message"]   = "Reply skipped: no recipient or body"
        return state

    subject = f"Re: Your TechnoBrain Enquiry"

    # Send
    sent = gmail.send_email(
        to=to_addr,
        subject=subject,
        html_body=html_body,
        text_body=text_body,
        reply_to_thread=thread_id,
    )

    if sent:
        state["reply_sent"]     = True
        state["status_message"] = f"Reply sent to {to_addr} ✓"

        # Mark original Gmail message as read
        gmail.mark_as_read(email_id)

        # Update DB
        if sub_id:
            db.update_status(
                submission_id=sub_id,
                status="replied",
                intent=intent,
                notes=f"Nexus replied automatically",
            )
    else:
        state["reply_sent"]     = False
        state["error"]          = "Gmail send failed"
        state["status_message"] = f"Reply FAILED for {to_addr} ✗"

        if sub_id:
            db.update_status(sub_id, "error", notes="Gmail send failed")

    return state


def mark_invalid_node(state: NexusState) -> NexusState:
    """Called when validation fails. Marks read, updates DB, no reply."""
    email_id = state.get("email_id", "")
    sub_id   = state.get("submission_id")

    gmail.mark_as_read(email_id)

    if sub_id:
        db.update_status(
            sub_id, "invalid",
            notes=f"email={state.get('is_valid_email')}, phone={state.get('is_valid_phone')}"
        )

    state["reply_sent"]     = False
    state["status_message"] = "Marked invalid — no reply sent"
    return state


def mark_spam_node(state: NexusState) -> NexusState:
    """Called when spam is detected. Marks read, updates DB, no reply."""
    email_id = state.get("email_id", "")
    sub_id   = state.get("submission_id")

    gmail.mark_as_read(email_id)

    if sub_id:
        db.update_status(
            sub_id, "spam",
            notes=state.get("spam_reason", ""),
        )

    state["reply_sent"]     = False
    state["status_message"] = f"Spam — no reply. Reason: {state.get('spam_reason', 'unknown')}"
    return state
