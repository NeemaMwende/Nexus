"""
Nexus LangGraph StateGraph.
This is the complete agent pipeline — compile once, invoke per email.
"""
from langgraph.graph import StateGraph, END

from agent.state import NexusState
from agent.nodes.fetch_email   import fetch_email_node
from agent.nodes.validate      import validate_node
from agent.nodes.spam_check    import spam_check_node
from agent.nodes.classify_intent import classify_intent_node
from agent.nodes.rag_retrieve  import rag_retrieve_node
from agent.nodes.draft_reply   import draft_reply_node
from agent.nodes.send_reply    import (
    send_reply_node,
    mark_invalid_node,
    mark_spam_node,
)


# ── Conditional edge functions ────────────────────────────────────────────────

def route_after_validate(state: NexusState) -> str:
    """After validation: if invalid → mark_invalid, else → spam_check."""
    if not state.get("is_valid_email") or not state.get("is_valid_phone"):
        return "mark_invalid"
    return "spam_check"


def route_after_spam(state: NexusState) -> str:
    """After spam check: if spam → mark_spam, else → classify_intent."""
    if state.get("is_spam"):
        return "mark_spam"
    return "classify_intent"


# ── Build the graph ───────────────────────────────────────────────────────────

def build_nexus_graph():
    graph = StateGraph(NexusState)

    # Add all nodes
    graph.add_node("fetch_email",      fetch_email_node)
    graph.add_node("validate",         validate_node)
    graph.add_node("spam_check",       spam_check_node)
    graph.add_node("classify_intent",  classify_intent_node)
    graph.add_node("rag_retrieve",     rag_retrieve_node)
    graph.add_node("draft_reply",      draft_reply_node)
    graph.add_node("send_reply",       send_reply_node)
    graph.add_node("mark_invalid",     mark_invalid_node)
    graph.add_node("mark_spam",        mark_spam_node)

    # Entry point
    graph.set_entry_point("fetch_email")

    # Linear edges
    graph.add_edge("fetch_email", "validate")

    # Conditional: validate → mark_invalid OR spam_check
    graph.add_conditional_edges(
        "validate",
        route_after_validate,
        {
            "mark_invalid": "mark_invalid",
            "spam_check":   "spam_check",
        }
    )

    # Conditional: spam_check → mark_spam OR classify_intent
    graph.add_conditional_edges(
        "spam_check",
        route_after_spam,
        {
            "mark_spam":       "mark_spam",
            "classify_intent": "classify_intent",
        }
    )

    # Linear: classify → retrieve → draft → send
    graph.add_edge("classify_intent", "rag_retrieve")
    graph.add_edge("rag_retrieve",    "draft_reply")
    graph.add_edge("draft_reply",     "send_reply")

    # Terminal nodes → END
    graph.add_edge("send_reply",   END)
    graph.add_edge("mark_invalid", END)
    graph.add_edge("mark_spam",    END)

    return graph.compile()


# Compiled graph — import this everywhere
nexus_graph = build_nexus_graph()
