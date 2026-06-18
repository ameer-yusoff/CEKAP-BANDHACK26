import asyncio
import logging
import os
import re
import httpx
import time
from dotenv import load_dotenv

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from contextlib import asynccontextmanager

from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver
from thenvoi import Agent
from thenvoi.adapters import LangGraphAdapter
from thenvoi.config import load_agent_config

from prompts import FIRST_RESPONDER_PROMPT
from dispatcher_agent import main as dispatcher_main
from geo_agent import main as geo_main
from manager_agent import main as manager_main
from medical_agent import main as medical_main
from triage_agent import main as triage_main

# Configure Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()

# ==========================================
# 1. BAND AGENT INITIALIZATION
# ==========================================
agent_id, api_key = load_agent_config("first_responder")

llm = ChatOpenAI(
    model="deepseek/deepseek-chat", # AI/ML API
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

# Global variables
last_message_timestamp = time.time()
DYNAMIC_CHAT_ID = None

# ==========================================
# 2. FASTAPI SERVER SETUP
# ==========================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("FastAPI is starting...")
    agent_tasks = []
    
    async def start_background_agents():
        await asyncio.sleep(5) 
        logger.info("Starting all CEKAP agents in the background...")
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

async def get_active_chat_id(client, headers):
    """Find active chat room dynamically without static ID."""
    global DYNAMIC_CHAT_ID
    if DYNAMIC_CHAT_ID:
        return DYNAMIC_CHAT_ID
        
    try:
        res = await client.get("https://app.thenvoi.com/api/v1/agent/chats", headers=headers)
        if res.status_code == 200:
            data = res.json()
            if isinstance(data, list) and len(data) > 0:
                DYNAMIC_CHAT_ID = data[0].get("id")
            elif isinstance(data, dict) and "data" in data and len(data["data"]) > 0:
                DYNAMIC_CHAT_ID = data["data"][0].get("id")
        return DYNAMIC_CHAT_ID
    except Exception as e:
        logger.error(f"Failed to retrieve room list: {str(e)}")
        return None

@app.post("/api/chat")
async def handle_chat(request: ChatRequest):
    global last_message_timestamp
    user_text = request.message.strip()
    
    if not user_text:
        raise HTTPException(status_code=400, detail="Message cannot be empty")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    try:
        async with httpx.AsyncClient() as client:
            chat_id = await get_active_chat_id(client, headers)
            if not chat_id:
                return {"status": "ERROR", "reply": "System cannot find active room. Please create room on Band platform."}

            # 1. Send message to Band
            payload = {
                "text": f"@first_responder [Caller]: {user_text}"
            }
            send_res = await client.post(
                f"https://app.thenvoi.com/api/v1/agent/chats/{chat_id}/messages", 
                headers=headers, 
                json=payload
            )
            
            if send_res.status_code not in [200, 201]:
                return {"status": "ERROR", "reply": "Failed to connect to main server."}

            # 2. Polling: Wait for response from First Responder
            logger.info(f"Waiting for response in room: {chat_id}...")
            
            for _ in range(15): # Polling ~30 seconds
                await asyncio.sleep(2)
                chat_res = await client.get(
                    f"https://app.thenvoi.com/api/v1/agent/chats/{chat_id}/messages", 
                    headers=headers
                )
                
                if chat_res.status_code == 200:
                    messages = chat_res.json().get("data", [])
                    if messages:
                        latest_msg = messages[0] 
                        msg_text = latest_msg.get("text", "")
                        
                        if "@Caller" in msg_text and latest_msg.get("created_at_timestamp", 0) > last_message_timestamp:
                            last_message_timestamp = time.time()
                            
                            clean_reply = re.sub(r'^@Caller[:,\s]*', '', msg_text, flags=re.IGNORECASE).strip()
                            clean_reply = re.sub(r'[*#_]', '', clean_reply)
                            
                            if "TERMINATE" in clean_reply.upper():
                                return {"status": "TERMINATE_CALL", "reply": "Call terminated."}
                                
                            return {"status": "ACTIVE", "reply": clean_reply}

            return {
                "status": "ACTIVE",
                "reply": "System is coordinating your information with rescue team. Please wait..."
            }

    except Exception as e:
        logger.error(f"API Error: {str(e)}")
        return {"status": "ERROR", "reply": "Network disruption detected."}