"""
Node: classify_intent
Uses LLM to determine what the sender wants.
Returns intent + confidence as JSON.
"""
import json, re
from agent.state import NexusState, Intent
from services.llm_client import call_llm

INTENT_SYSTEM_PROMPT = """You are an intent classifier for TechnoBrain, an African IT company.
Classify the contact form message into exactly one intent.

Return ONLY valid JSON, no other text:
{"intent": "<intent>", "confidence": <0.0-1.0>}

Intents:
- product_inquiry      : asking about products, services, pricing, capabilities
- demo_request         : wants a demo, trial, presentation, or proof of concept
- partnership          : wants to partner, resell, or collaborate
- job_application      : looking for employment, internship, attachment
- support_request      : existing customer needing help or support
- general_information  : general question about the company

Pick the single best match. Be confident — do not return unknown unless truly impossible."""


def classify_intent_node(state: NexusState) -> NexusState:
    message  = state.get("message", "")
    product  = state.get("product_interest", "")
    name     = state.get("sender_name", "")

    user_input = f"""Name: {name}
Product interest: {product or 'not specified'}
Message: {message}"""

    try:
        raw = call_llm(INTENT_SYSTEM_PROMPT, user_input)

        # Strip markdown code fences if the LLM added them
        raw = re.sub(r"```json|```", "", raw).strip()
        data = json.loads(raw)

        intent     = data.get("intent", Intent.UNKNOWN)
        confidence = float(data.get("confidence", 0.5))

        # Validate intent is a known value
        valid_intents = [i.value for i in Intent]
        if intent not in valid_intents:
            intent = Intent.GENERAL

        state["intent"]             = intent
        state["intent_confidence"]  = confidence
        state["status_message"]     = f"Intent: {intent} ({confidence:.0%})"

    except Exception as e:
        print(f"[classify_intent] Error: {e}")
        state["intent"]            = Intent.GENERAL
        state["intent_confidence"] = 0.5
        state["status_message"]    = "Intent classification failed — defaulting to general"

    return state
