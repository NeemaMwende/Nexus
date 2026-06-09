"""
Single LLM client that works with either Groq (free API) or Ollama (local).
Switch via LLM_PROVIDER env var.
"""
from typing import Optional
import config


def get_llm():
    """Return a LangChain chat model based on config."""
    if config.LLM_PROVIDER == "groq":
        from langchain_groq import ChatGroq
        return ChatGroq(
            api_key=config.GROQ_API_KEY,
            model=config.GROQ_MODEL,
            temperature=0.2,
        )
    else:
        from langchain_community.chat_models import ChatOllama
        return ChatOllama(
            base_url=config.OLLAMA_BASE_URL,
            model=config.OLLAMA_MODEL,
            temperature=0.2,
        )


def call_llm(system_prompt: str, user_message: str) -> str:
    """
    Simple single-turn LLM call.
    Returns the response text, strips whitespace.
    """
    from langchain_core.messages import SystemMessage, HumanMessage

    llm = get_llm()
    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_message),
    ]
    response = llm.invoke(messages)
    return response.content.strip()
