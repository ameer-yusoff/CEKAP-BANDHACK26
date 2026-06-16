# geo_agent.py

import asyncio
import logging
import os
import requests
from dotenv import load_dotenv

from langchain_openai import ChatOpenAI
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from thenvoi import Agent
from thenvoi.adapters import LangGraphAdapter
from thenvoi.config import load_agent_config

from prompts import GEO_PROMPT

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()

async def main():
    agent_id, api_key = load_agent_config("geo_specialist")
    
    llm = ChatOpenAI(
        model="google/gemini-2.5-flash",
        api_key=os.getenv("OPENAI_API_KEY"),
        base_url=os.getenv("OPENAI_BASE_URL"),
        temperature=0.0
    )
    
    adapter = LangGraphAdapter(
        llm=llm,
        checkpointer=InMemorySaver(),
        custom_section=GEO_PROMPT
    )
    
    logger.info("Connecting Geo_Specialist to the Band platform...")
    agent = Agent.create(adapter=adapter, agent_id=agent_id, api_key=api_key)
    await agent.run()

if __name__ == "__main__":
    asyncio.run(main())