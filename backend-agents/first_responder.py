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

# Mature & Professional Log Configuration
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()

# ==========================================
# 1. BAND AGENT INITIALIZATION
# ==========================================
agent_id, api_key = load_agent_config("first_responder")

llm = ChatOpenAI(
    model="deepseek/deepseek-chat", # Powered by AI/ML API
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
# Pull Static Chat Room ID from Environment (Render)
BAND_CHAT_ID = "0ba97885-a5e2-4387-ba01-405f0b988941"

# ==========================================
# 2. FASTAPI SERVER SETUP
# ==========================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    if not BAND_CHAT_ID:
        logger.error("CRITICAL WARNING: BAND_CHAT_ID not found in environment (Environment Variables)!")

    logger.info("CEKAP Engine is starting...")
    agent_tasks = []
    
    async def start_background_agents():
        await asyncio.sleep(5) 
        logger.info("Activating agent infrastructure in parallel (Parallel Processing)...")
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
    global last_message_timestamp
    user_text = request.message.strip()
    
    if not user_text:
        raise HTTPException(status_code=400, detail="Error message: Empty input.")

    if not BAND_CHAT_ID:
        return {"status": "ERROR", "reply": "System is being upgraded. BAND_CHAT_ID configuration not yet set."}

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    try:
        async with httpx.AsyncClient() as client:
            # 1. Send message directly to specific verified Band room
            payload = {
                "text": f"@first_responder [Caller]: {user_text}"
            }
            send_res = await client.post(
                f"https://app.thenvoi.com/api/v1/agent/chats/{BAND_CHAT_ID}/messages", 
                headers=headers, 
                json=payload
            )
            
            if send_res.status_code not in [200, 201]:
                logger.error(f"Band API Rejected. Code: {send_res.status_code}")
                return {"status": "ERROR", "reply": "Failed to connect to emergency server infrastructure."}

            # 2. Polling Mechanism: Wait for confirmation from First Responder agent in that room
            logger.info(f"Monitoring operation room for coordination: {BAND_CHAT_ID}...")
            
            for _ in range(15): # Active monitoring ~30 seconds
                await asyncio.sleep(2)
                chat_res = await client.get(
                    f"https://app.thenvoi.com/api/v1/agent/chats/{BAND_CHAT_ID}/messages", 
                    headers=headers
                )
                
                if chat_res.status_code == 200:
                    messages = chat_res.json().get("data", [])
                    if messages:
                        latest_msg = messages[0] 
                        msg_text = latest_msg.get("text", "")
                        
                        # Filter responses specifically addressed to caller
                        if "@Caller" in msg_text and latest_msg.get("created_at_timestamp", 0) > last_message_timestamp:
                            last_message_timestamp = time.time()
                            
                            # Execute clean filtering logic before Text-to-Speech
                            clean_reply = re.sub(r'^@Caller[:,\s]*', '', msg_text, flags=re.IGNORECASE).strip()
                            clean_reply = re.sub(r'[*#_]', '', clean_reply)
                            
                            # Detection of fake call intervention directed by Manager
                            if "TERMINATE" in clean_reply.upper():
                                return {"status": "TERMINATE_CALL", "reply": "Line terminated. Fake call usage detected."}
                                
                            return {"status": "ACTIVE", "reply": clean_reply}

            return {
                "status": "ACTIVE",
                "reply": "Logic coordination is running in the background with support agents. Please wait..."
            }

    except Exception as e:
        logger.error(f"Encoding Error: {str(e)}")
        return {"status": "ERROR", "reply": "CEKAP system is experiencing unforeseen disruption."}