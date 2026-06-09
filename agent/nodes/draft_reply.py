"""
Node: draft_reply
Generates a professional, TechnoBrain-branded email reply.
The LLM only uses information from the RAG context — never invents services.
"""
from agent.state import NexusState, Intent
from services.llm_client import call_llm

REPLY_SYSTEM_PROMPT = """You are Nexus, the intelligent communication assistant for TechnoBrain.
TechnoBrain is a leading pan-African IT company headquartered in Nairobi, Kenya, with operations across Africa.
Specialisations: Artificial Intelligence, Business Process Outsourcing (BPO), Cloud Services, Engineering Solutions, Revenue Management Systems, Digital Government Transformation.

Your job is to write a professional, warm, and specific email reply to a contact form enquiry.

STRICT RULES:
1. Only reference information from the CONTEXT section below. Never invent services, prices, or claims.
2. Address the person by their first name.
3. Be specific — reference the exact product or service they asked about if it appears in the context.
4. Always end with a clear next step (schedule a call, visit a specific page, or contact a named person if provided).
5. Professional but warm tone — this is a B2B company but humans are reading it.
6. Keep it concise: 3–5 short paragraphs. No walls of text.
7. Sign off as "TechnoBrain Team" — never claim to be a human.
8. Write in plain HTML using only <p>, <strong>, <br> tags — no CSS, no divs.
9. Return ONLY the email body HTML. No subject line, no preamble."""

# Intent-specific call-to-action guidance injected per intent
INTENT_CTA = {
    Intent.PRODUCT_INQUIRY:  "Invite them to schedule a discovery call or request a detailed proposal.",
    Intent.DEMO_REQUEST:     "Offer to arrange a live demonstration — provide a calendar link or email to contact.",
    Intent.PARTNERSHIP:      "Express interest and invite them to discuss their partnership proposal with the business development team.",
    Intent.JOB_APPLICATION:  "Direct them to the TechnoBrain careers page and provide the HR contact email.",
    Intent.SUPPORT_REQUEST:  "Acknowledge the issue and route them to the support team with contact details.",
    Intent.GENERAL:          "Offer to connect them with the right person at TechnoBrain.",
}


def draft_reply_node(state: NexusState) -> NexusState:
    name    = state.get("sender_name", "there")
    message = state.get("message", "")
    product = state.get("product_interest", "")
    intent  = state.get("intent", Intent.GENERAL)
    chunks  = state.get("rag_chunks", [])

    first_name = name.split()[0] if name else "there"

    # Build context block from RAG chunks
    context = "\n\n---\n\n".join(chunks) if chunks else "No specific product information retrieved."
    cta     = INTENT_CTA.get(intent, INTENT_CTA[Intent.GENERAL])

    user_prompt = f"""ENQUIRY DETAILS:
Name: {name}
Product interest: {product or 'not specified'}
Message: {message}

INTENT: {intent}
CALL-TO-ACTION GUIDANCE: {cta}

CONTEXT (use ONLY this to answer):
{context}

Write the email reply body HTML now."""

    try:
        html_body = call_llm(REPLY_SYSTEM_PROMPT, user_prompt)

        # Strip markdown if LLM wrapped it
        html_body = html_body.strip()
        if html_body.startswith("```"):
            html_body = html_body.split("```", 2)[-1].strip()
            if html_body.startswith("html"):
                html_body = html_body[4:].strip()

        # Wrap in a clean outer template
        full_html = _wrap_email_template(html_body, first_name)

        # Generate plain text version (strip HTML tags)
        import re
        plain = re.sub(r"<[^>]+>", "", full_html).strip()

        state["draft_reply_html"] = full_html
        state["draft_reply_text"] = plain
        state["status_message"]   = "Reply drafted ✓"

    except Exception as e:
        print(f"[draft_reply] Error: {e}")
        state["draft_reply_html"] = _fallback_reply(first_name)
        state["draft_reply_text"] = f"Dear {first_name},\n\nThank you for contacting TechnoBrain. A member of our team will be in touch shortly.\n\nWarm regards,\nTechnoBrain Team"
        state["status_message"]   = f"Reply used fallback (LLM error: {e})"

    return state


def _wrap_email_template(body_html: str, first_name: str) -> str:
    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>
  body {{ font-family: Arial, sans-serif; font-size: 15px; color: #333333; margin: 0; padding: 0; }}
  .container {{ max-width: 600px; margin: 0 auto; padding: 32px 24px; }}
  .header {{ border-bottom: 3px solid #F4801A; padding-bottom: 16px; margin-bottom: 24px; }}
  .logo-techno {{ color: #2D3561; font-size: 22px; font-weight: 800; }}
  .logo-brain  {{ color: #F4801A; font-size: 22px; font-weight: 800; }}
  .body {{ line-height: 1.7; }}
  .body p {{ margin: 0 0 14px; }}
  .footer {{ border-top: 1px solid #eeeeee; margin-top: 32px; padding-top: 16px; font-size: 12px; color: #888888; }}
  .cta-button {{ display: inline-block; background: #F4801A; color: #ffffff !important; padding: 10px 24px; border-radius: 4px; text-decoration: none; font-weight: 600; margin: 8px 0; }}
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <span class="logo-techno">TECHNO</span><span class="logo-brain">BRAIN</span>
    <div style="font-size: 11px; color: #888888; margin-top: 2px;">Empowering Lives</div>
  </div>
  <div class="body">
    {body_html}
  </div>
  <div class="footer">
    <strong>TechnoBrain Group</strong><br>
    Nairobi, Kenya | www.technobraingroup.com<br>
    This message was sent in response to your enquiry via our contact form.
  </div>
</div>
</body>
</html>"""


def _fallback_reply(first_name: str) -> str:
    return _wrap_email_template(
        f"""<p>Dear {first_name},</p>
<p>Thank you for reaching out to TechnoBrain. We have received your enquiry and a member of our team will review it and get back to you shortly.</p>
<p>In the meantime, you are welcome to explore our website at <a href="https://www.technobraingroup.com">technobraingroup.com</a> to learn more about our solutions and services.</p>
<p>Warm regards,<br><strong>TechnoBrain Team</strong></p>""",
        first_name,
    )
