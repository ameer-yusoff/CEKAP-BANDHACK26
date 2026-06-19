# dispatcher_agent.py

import asyncio
import logging
import os
import requests
from dotenv import load_dotenv

# Additional Supabase client import
from supabase import create_client, Client

from langchain_openai import ChatOpenAI
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from thenvoi import Agent
from thenvoi.adapters import LangGraphAdapter
from thenvoi.config import load_agent_config

from prompts import DISPATCHER_PROMPT

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Initialize Supabase Client
supabase_url = os.getenv("SUPABASE_URL")
supabase_key = os.getenv("SUPABASE_KEY")
supabase_client: Client = create_client(supabase_url, supabase_key)

@tool
def send_telegram_dispatch(record_id: str, emergency_details: str, latitude: str, longitude: str) -> str:
    """
    CRITICAL TOOL: Use this to send the final emergency report to the rescue team via Telegram.
    Include the record_id, emergency details, latitude, and longitude.
    """
    logger.info("Sending dispatch to Telegram and updating Supabase...")
    
    # Corrected Maps link format
    maps_link = f"https://maps.google.com/?q={latitude},{longitude}"
    
    message = (
        f"🚨 *CEKAP EMERGENCY DISPATCH* 🚨\n\n"
        f"*Details:*\n{emergency_details}\n\n"
        f"*Location (Maps):*\n{maps_link}"
    )
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }
    
    try:
        # 1. Send message to Telegram bot
        response = requests.post(url, json=payload)
        
        # 2. If successful, continue updating Maps link in Supabase database
        if response.status_code == 200:
            supabase_client.table("emergency_logs").update({"maps_link": maps_link}).eq("id", record_id).execute()
            logger.info("\n========================================================\n🚨 EMERGENCY MISSION SUCCESSFULLY COMPLETED 🚨\nAll agents finalized coordination. Dispatch network closed.\n========================================================")
            return f"SUCCESS: Message dispatched to Telegram and Supabase updated for Record ID {record_id}."
        else:
            return f"FAILED: Telegram API Error {response.text}"
    except Exception as e:
        logger.error(f"Webhook/Supabase Error: {str(e)}")
        return f"FAILED: {str(e)}"

@tool
def terminate_emergency_session() -> str:
    """
    CRITICAL TOOL: Execute this ONLY after successfully sending the Telegram dispatch.
    This securely retires the room in the database.
    """
    logger.info("EXECUTING TERMINATION PROTOCOL...")
    try:
        supabase_client.table("emergency_logs").update({"status": "RETIRED_ROOM"}).eq("status", "ACTIVE_ROOM").execute()
        return "SUCCESS: Operations room retired successfully."
    except Exception as e:
        return f"FAILED: {str(e)}"

async def main():
    agent_id, api_key = load_agent_config("dispatcher") 
    
    llm = ChatOpenAI(
        model="deepseek-chat",
        api_key=os.getenv("OPENAI_API_KEY"),
        base_url=os.getenv("OPENAI_BASE_URL"),
        temperature=0.0
    )
    
    adapter = LangGraphAdapter(
        llm=llm,
        checkpointer=InMemorySaver(),
        additional_tools=[send_telegram_dispatch, terminate_emergency_session],
        custom_section=DISPATCHER_PROMPT
    )
    
    logger.info("Connecting Dispatcher to the Band platform...")
    agent = Agent.create(adapter=adapter, agent_id=agent_id, api_key=api_key)
    await agent.run()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Dispatcher Agent stopped.")