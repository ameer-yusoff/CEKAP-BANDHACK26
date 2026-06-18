# first_responder.py

import asyncio
import logging
import os
import re
import httpx
from dotenv import load_dotenv

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from contextlib import asynccontextmanager

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.prebuilt import create_react_agent
from langchain_core.tools import tool

from thenvoi import Agent
from thenvoi.adapters import LangGraphAdapter
from thenvoi.config import load_agent_config

from prompts import FIRST_RESPONDER_PROMPT
from dispatcher_agent import main as dispatcher_main
from geo_agent import main as geo_main
from manager_agent import main as manager_main
from manager_agent import manager_react_agent
from medical_agent import main as medical_main
from triage_agent import main as triage_main

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()

# ==========================================
# 1. BAND AGENT INITIALIZATION
# ==========================================
agent_id, api_key = load_agent_config("first_responder")

llm = ChatOpenAI(
    model="deepseek-chat",
    api_key=os.getenv("OPENAI_API_KEY"),
    base_url=os.getenv("OPENAI_BASE_URL"),
    temperature=0.0 
)

adapter = LangGraphAdapter(
    llm=llm,
    checkpointer=InMemorySaver(),
    custom_section=FIRST_RESPONDER_PROMPT
)

band_agent = Agent.create(adapter=adapter, agent_id=agent_id, api_key=api_key)

# Create a dummy tool so LangGraph doesn't crash when local tools are empty
@tool
def local_placeholder_tool():
    """A placeholder tool to maintain local LangGraph stability."""
    return "Placeholder active"

react_agent = create_react_agent(llm, tools=[local_placeholder_tool])
chat_memory = [SystemMessage(content=FIRST_RESPONDER_PROMPT)]
is_band_triggered = False

# ==========================================
# 2. BAND PLATFORM DIRECT INJECTION (THE FIX)
# ==========================================
async def trigger_band_platform(initial_message: str):
    """
    Bypasses local tool execution and room creation entirely.
    Injects a trigger message directly into the existing Band environment.
    """
    logger.info("SYSTEM OVERRIDE: Waking up Band agents via Direct API Injection...")
    try:
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        
        async with httpx.AsyncClient() as client:
            # The permanent Chat ID assigned by Band (extracted from your logs)
            chat_id = "c8c29c5a-dd66-4e7f-91c9-a9a12a353933"
            
            # Attempt to fetch the active chat dynamically just in case
            chats_res = await client.get("https://app.thenvoi.com/api/v1/agent/chats", headers=headers)
            if chats_res.status_code == 200:
                chats_data = chats_res.json()
                if isinstance(chats_data, list) and len(chats_data) > 0:
                    chat_id = chats_data[0].get("id", chat_id)
                elif isinstance(chats_data, dict) and "data" in chats_data:
                    chat_id = chats_data["data"][0].get("id", chat_id)
                    
            logger.info(f"Targeting Band Chat ID: {chat_id}")
            
            # Send the wake-up message directly to @agent_manager
            payload = {
                "text": f"@agent_manager System Online. Emergency caller has initiated contact. Initial context: {initial_message}"
            }
            
            send_res = await client.post(
                f"https://app.thenvoi.com/api/v1/agent/chats/{chat_id}/messages", 
                headers=headers, 
                json=payload
            )
            
            if send_res.status_code in [200, 201]:
                logger.info("Successfully triggered Band Platform! Agents are now communicating.")
            else:
                logger.error(f"Failed to trigger Band Platform. Status: {send_res.status_code}, Response: {send_res.text}")
                
    except Exception as e:
        logger.error(f"Direct API Injection Error: {str(e)}")

# ==========================================
# 3. FASTAPI SERVER SETUP
# ==========================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("FastAPI is starting... Allowing port binding to complete first.")
    agent_tasks = []
    
    async def start_background_agents():
        await asyncio.sleep(5) 
        logger.info("5 seconds passed. Starting all CEKAP agents in the background NOW...")
        agent_tasks.extend([
            asyncio.create_task(band_agent.run()),
            asyncio.create_task(dispatcher_main()),
            asyncio.create_task(geo_main()),
            asyncio.create_task(manager_main()),
            asyncio.create_task(medical_main()),
            asyncio.create_task(triage_main())
        ])

    boot_task = asyncio.create_task(start_background_agents())
    
    yield  
    
    boot_task.cancel()
    for task in agent_tasks:
        task.cancel()

app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class ChatRequest(BaseModel):
    message: str

@app.post("/api/chat")
async def handle_chat(request: ChatRequest):
    global chat_memory, is_band_triggered
    
    user_text = request.message.strip()
    if not user_text:
        raise HTTPException(status_code=400, detail="Message cannot be empty")

    try:        
        if not is_band_triggered:
            logger.info("SYSTEM OVERRIDE: Bypassing Band API. Using Local Memory Handoff to trigger Manager Agent...")
            
            handoff_msg = HumanMessage(
                content=f"SYSTEM ALERT: Emergency escalated from First Responder. Initial context: {user_text}. "
                        f"ACTION REQUIRED NOW: "
                        f"1. Use 'thenvoi_create_chatroom' tool to create a room named 'Emergency Incident'. "
                        f"2. Use 'thenvoi_add_participant' tool to add @triage_diagnoser, @geo_specialist, @medical_agent, and @dispatcher to that room. "
                        f"3. Use 'thenvoi_send_message' tool to send the emergency details and tag @triage_diagnoser and @geo_specialist in that room to start processing."
            )
            
            from prompts import MANAGER_PROMPT
            asyncio.create_task(manager_react_agent.ainvoke({
                "messages": [SystemMessage(content=MANAGER_PROMPT), handoff_msg]
            }))
            
            is_band_triggered = True

        chat_memory.append(HumanMessage(content=user_text))
        logger.info("Processing caller input...")
        
        response = await react_agent.ainvoke({"messages": chat_memory})
        chat_memory = response["messages"]
        final_ai_msg = chat_memory[-1].content
        
        # Strict parsing logic
        reply_to_pwa = []
        for line in final_ai_msg.split('\n'):
            line = line.strip()
            if line.lower().startswith('@caller'):
                clean_line = re.sub(r'^@caller[:,\s]*', '', line, flags=re.IGNORECASE)
                clean_line = re.sub(r'[*#_]', '', clean_line)
                reply_to_pwa.append(clean_line)
        
        final_reply = " ".join(reply_to_pwa).strip()
        
        if not final_reply:
            final_reply = "Sistem CEKAP sedang memproses laporan anda. Sila tunggu sebentar..."
            
        return {
            "status": "ACTIVE",
            "reply": final_reply
        }
        
    except Exception as e:
        logger.error(f"API Processing Error: {str(e)}")
        return {
            "status": "ERROR",
            "reply": "Harap maaf, sistem CEKAP menghadapi gangguan rangkaian. Sila cuba sebentar lagi."
        }