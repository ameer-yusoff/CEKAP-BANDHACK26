# medical_agent.py

import asyncio
import logging
import os
from dotenv import load_dotenv

from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver
from thenvoi import Agent
from thenvoi.adapters import LangGraphAdapter
from thenvoi.config import load_agent_config

from prompts import MEDICAL_PROMPT

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def main():
    load_dotenv()
    agent_id, api_key = load_agent_config("medical_agent")
    
    llm = ChatOpenAI(
        model="deepseek-chat",
        api_key=os.getenv("OPENAI_API_KEY"),
        base_url=os.getenv("OPENAI_BASE_URL"),
        temperature=0.0
    )
    
    adapter = LangGraphAdapter(
        llm=llm,
        checkpointer=InMemorySaver(),
        custom_section=MEDICAL_PROMPT
    )
    
    logger.info("Connecting Medical & Safety Agent to the Band platform...")
    agent = Agent.create(adapter=adapter, agent_id=agent_id, api_key=api_key)
    await agent.run()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Medical Agent stopped.")