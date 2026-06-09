"""
Node: rag_retrieve
Queries Qdrant with an intent-aware search query.
Returns the top relevant passages from TechnoBrain's Salesmate knowledge base.
"""
from agent.state import NexusState, Intent
from services.qdrant_store import vector_store

# Intent → search query enrichment
INTENT_QUERY_PREFIX = {
    Intent.PRODUCT_INQUIRY:  "TechnoBrain products services capabilities offerings",
    Intent.DEMO_REQUEST:     "TechnoBrain demo trial presentation proof of concept",
    Intent.PARTNERSHIP:      "TechnoBrain partnership reseller collaboration",
    Intent.JOB_APPLICATION:  "TechnoBrain careers jobs employment opportunities",
    Intent.SUPPORT_REQUEST:  "TechnoBrain support help customer service",
    Intent.GENERAL:          "TechnoBrain company overview about us",
}


def rag_retrieve_node(state: NexusState) -> NexusState:
    message = state.get("message", "")
    product = state.get("product_interest", "")
    intent  = state.get("intent", Intent.GENERAL)

    # Build a rich query combining the user's message + intent context
    prefix = INTENT_QUERY_PREFIX.get(intent, "TechnoBrain")
    product_clause = f" specifically about {product}" if product else ""
    query = f"{prefix}{product_clause}. {message}"

    try:
        results = vector_store.search(query, top_k=5)

        if not results:
            # Knowledge base empty — use a graceful fallback message
            state["rag_chunks"]     = ["TechnoBrain is a pan-African IT company specialising in AI, BPO, cloud services, and engineering solutions. Please visit technobraingroup.com for more information."]
            state["status_message"] = "RAG: no results — using fallback"
        else:
            # Filter by minimum relevance score
            relevant = [r for r in results if r["score"] >= 0.5]
            if not relevant:
                relevant = results[:2]  # take top 2 even if low score

            chunks = [r["text"] for r in relevant]
            state["rag_chunks"]     = chunks
            state["status_message"] = f"RAG: retrieved {len(chunks)} passages ✓"

    except Exception as e:
        print(f"[rag_retrieve] Qdrant error: {e}")
        state["rag_chunks"]     = []
        state["status_message"] = f"RAG error: {e}"

    return state
