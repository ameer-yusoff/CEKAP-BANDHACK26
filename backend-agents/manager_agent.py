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

# 1. LLM INITIALIZATION
llm = ChatOpenAI(
    model="o3-mini",
    api_key=os.getenv("OPENAI_API_KEY"),
    base_url=os.getenv("OPENAI_BASE_URL"),
    temperature=0.0
)

# 2. Create the Band Agent for the Manager with the terminate_session tool
agent_id, api_key = load_agent_config("agent_manager")
adapter = LangGraphAdapter(
    llm=llm,
    checkpointer=InMemorySaver(),
    additional_tools=[terminate_session],
    custom_section=MANAGER_PROMPT
)

manager_band_agent = Agent.create(adapter=adapter, agent_id=agent_id, api_key=api_key)

# 3. Extract the authenticated tools from the adapter
manager_tools = []
for attr in ['tools', '_tools', 'platform_tools']:
    if hasattr(adapter, attr):
        val = getattr(adapter, attr)
        if isinstance(val, list):
            manager_tools.extend(val)
            break

if not manager_tools and hasattr(adapter, 'get_tools') and callable(adapter.get_tools):
    tools_res = adapter.get_tools()
    manager_tools.extend(tools_res if isinstance(tools_res, list) else [tools_res])

if terminate_session not in manager_tools:
    manager_tools.append(terminate_session)

# 4. Create a React Agent for the Manager to handle tool execution
manager_react_agent = create_react_agent(llm, tools=manager_tools)

async def main():
    logger.info("Connecting Agent Manager to the Band platform...")
    await manager_band_agent.run()

if __name__ == "__main__":
    asyncio.run(main())