"""
Node: validate
Validates email address (DNS check) and phone number (KE locale).
"""
import phonenumbers
from email_validator import validate_email, EmailNotValidError
from agent.state import NexusState


def validate_node(state: NexusState) -> NexusState:
    email = state.get("sender_email", "")
    phone = state.get("sender_phone", "")

    # Email
    try:
        validate_email(email, check_deliverability=True)
        state["is_valid_email"] = True
    except EmailNotValidError as e:
        state["is_valid_email"] = False
        state["status_message"] = f"Invalid email: {email} — {e}"

    # Phone (optional field — blank phone is still valid)
    if not phone or phone.strip() in ("", "-", "N/A"):
        state["is_valid_phone"] = True  # not required
    else:
        try:
            parsed = phonenumbers.parse(phone, "KE")
            state["is_valid_phone"] = phonenumbers.is_valid_number(parsed)
            if not state["is_valid_phone"]:
                state["status_message"] = f"Invalid phone: {phone}"
        except phonenumbers.NumberParseException:
            state["is_valid_phone"] = False
            state["status_message"] = f"Unparseable phone: {phone}"

    if state["is_valid_email"] and state["is_valid_phone"]:
        state["status_message"] = "Validation passed ✓"

    return state
