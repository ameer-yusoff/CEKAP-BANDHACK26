# triage_agent.py

import asyncio
import logging
import os
from dotenv import load_dotenv

# Supabase database client
from supabase import create_client, Client

# LangChain and LangGraph imports
from langchain_openai import ChatOpenAI
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver

# Band SDK imports
from thenvoi import Agent
from thenvoi.adapters import LangGraphAdapter
from thenvoi.config import load_agent_config

# Import the Triage system prompt
from prompts import TRIAGE_PROMPT

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize Supabase Client globally so the tool can access it
load_dotenv()
supabase_url = os.getenv("SUPABASE_URL")
supabase_key = os.getenv("SUPABASE_KEY")
supabase_client: Client = create_client(supabase_url, supabase_key)

# Define the custom tool to save data to the Supabase database
@tool
def save_triage_data(emergency_type: str, priority_level: str, injuries: str, raw_location: str) -> str:
    """
    CRITICAL TOOL: Use this to save the classified emergency data into the Supabase database.
    Always execute this BEFORE mentioning the Geo_Locator agent.
    """
    logger.info(f"Saving to database: {priority_level} - {emergency_type}")
    try:
        data = {
            "emergency_type": emergency_type,
            "priority_level": priority_level,
            "injuries": injuries,
            "raw_location": raw_location,
            "status": "TRIAGED"
        }
        # Insert data into the 'emergency_logs' table
        response = supabase_client.table("emergency_logs").insert(data).execute()
        return f"SUCCESS: Data saved to database with record ID: {response.data[0]['id']}"
    except Exception as e:
        logger.error(f"Database error: {str(e)}")
        return f"FAILED to save data: {str(e)}"

async def main():
    agent_id, api_key = load_agent_config("triage_diagnoser")
    
    llm = ChatOpenAI(
        model="deepseek/deepseek-chat",
        api_key=os.getenv("OPENAI_API_KEY"),
        base_url=os.getenv("OPENAI_BASE_URL"),
        temperature=0.1
    )
    
    adapter = LangGraphAdapter(
        llm=llm,
        checkpointer=InMemorySaver(),
        additional_tools=[save_triage_data],
        custom_section=TRIAGE_PROMPT
    )
    
    logger.info("Connecting Triage & Diagnoser Agent to the Band platform...")
    
    agent = Agent.create(
        adapter=adapter,
        agent_id=agent_id,
        api_key=api_key
    )
    
    await agent.run()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Triage Agent execution stopped.")