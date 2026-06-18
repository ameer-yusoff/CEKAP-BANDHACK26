# manager_agent.py

import asyncio
import logging
import os
from dotenv import load_dotenv

from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.prebuilt import create_react_agent  
from langchain_core.messages import SystemMessage
from thenvoi import Agent
from thenvoi.adapters import LangGraphAdapter
from thenvoi.config import load_agent_config
from prompts import MANAGER_PROMPT

from langchain_core.tools import tool

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@tool
def terminate_session(reason: str) -> str:
    """
    CRITICAL TOOL: Use this to cleanly end the entire emergency session.
    """
    logger.info(f"SESSION TERMINATED BY MANAGER: {reason}")
    return f"SYSTEM_ACTION: TERMINATE_CALL. Reason: {reason}."

load_dotenv()

llm = ChatOpenAI(
    model="o3-mini",
    api_key=os.getenv("OPENAI_API_KEY"),
    base_url=os.getenv("OPENAI_BASE_URL"),
    temperature=0.0
)

manager_react_agent = create_react_agent(llm, tools=[terminate_session])
# --------------------------------------------------------

async def main():
    agent_id, api_key = load_agent_config("agent_manager")
    
    adapter = LangGraphAdapter(
        llm=llm,
        checkpointer=InMemorySaver(),
        additional_tools=[terminate_session],
        custom_section=MANAGER_PROMPT
    )
    
    logger.info("Connecting Agent Manager to the Band platform...")
    agent = Agent.create(adapter=adapter, agent_id=agent_id, api_key=api_key)
    await agent.run()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Agent Manager stopped.")