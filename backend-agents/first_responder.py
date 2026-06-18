import asyncio
import logging
import os
import time
from dotenv import load_dotenv

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from contextlib import asynccontextmanager
from supabase import create_client

# Official Band REST Client Library (as used in the warroom)
from band.client.rest import AsyncRestClient, ChatRoomRequest, ParticipantRequest, ChatMessageRequest, ChatMessageRequestMentionsItem
from thenvoi.config import load_agent_config

from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver
from thenvoi import Agent
from thenvoi.adapters import LangGraphAdapter
from prompts import FIRST_RESPONDER_PROMPT

from dispatcher_agent import main as dispatcher_main
from geo_agent import main as geo_main
from manager_agent import main as manager_main
from medical_agent import main as medical_main
from triage_agent import main as triage_main

# Log Configuration
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
load_dotenv()

app = FastAPI()

# ==========================================
# 1. CREDENTIAL MANAGEMENT (LIKE WARROOM)
# ==========================================
# We load the API key for each agent so we can 
# perform actions on their behalf without a 401 error.
AGENTS = {
    "first_responder": load_agent_config("first_responder"),
    "agent_manager": load_agent_config("agent_manager"),
    "triage_diagnoser": load_agent_config("triage_diagnoser"),
    "geo_specialist": load_agent_config("geo_specialist"),
    "medical_agent": load_agent_config("medical_agent"),
    "dispatcher": load_agent_config("dispatcher"),
}

BAND_URL = os.getenv("THENVOI_REST_URL", "https://app.thenvoi.com").rstrip("/")
CEKAP_ROOM_ID = None
first_responder_client = None
processed_msg_ids = set()

class ChatRequest(BaseModel):
    message: str

# ==========================================
# 2. PHASE 1 & 2: PROGRAMMATIC SETUP (DETERMINISTIC)
# ==========================================
async def build_cekap_infrastructure():
    global CEKAP_ROOM_ID, first_responder_client
    logger.info("Starting programmatic build of CEKAP infrastructure...")
    
    try:
        # Use the first_responder agent as the "Coordinator/Admin" for this room
        fr_id, fr_key = AGENTS["first_responder"]
        first_responder_client = AsyncRestClient(api_key=fr_key, base_url=BAND_URL)
        
        # [UPDATE: Use Supabase to prevent 'Ghost Rooms' clutter on every deployment]
        supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))
        try:
            res = supabase.table("emergency_logs").select("raw_location").eq("status", "ACTIVE_ROOM").execute()
            if res.data and len(res.data) > 0:
                CEKAP_ROOM_ID = res.data[0]["raw_location"]
                logger.info(f"Existing room found. System reusing room: {CEKAP_ROOM_ID}")
                return
        except Exception:
            pass
            
        # PHASE 1: Build Room using REST API
        resp = await first_responder_client.agent_api_chats.create_agent_chat(chat=ChatRoomRequest())
        CEKAP_ROOM_ID = resp.data.id
        logger.info(f"PHASE 1 SUCCESS: Operations Room created -> {CEKAP_ROOM_ID}")
        
        # [UPDATE: Save new room ID to Supabase for future use]
        try:
            supabase.table("emergency_logs").insert({
                "emergency_type": "SYSTEM", "priority_level": "N/A", 
                "injuries": "N/A", "raw_location": CEKAP_ROOM_ID, "status": "ACTIVE_ROOM"
            }).execute()
        except Exception:
            pass
        
        # PHASE 2: Add all support agents into the room
        for name, (agent_id, api_key) in AGENTS.items():
            if name == "first_responder":
                continue # First responder is already present as the room creator
            
            await first_responder_client.agent_api_participants.add_agent_chat_participant(
                CEKAP_ROOM_ID, 
                participant=ParticipantRequest(participant_id=agent_id, role="member")
            )
            logger.info(f"Added to room: @{name}")
            
        logger.info("PHASE 2 SUCCESS: All agents ready in the operations room.")

    except Exception as e:
        logger.error(f"Failed to build infrastructure: {str(e)}")

# ==========================================
# FIRST RESPONDER AI BRAIN FUNCTION
# ==========================================
async def first_responder_main():
    agent_id, api_key = AGENTS["first_responder"]
    llm = ChatOpenAI(
        model="deepseek/deepseek-chat",
        api_key=os.getenv("OPENAI_API_KEY"),
        base_url=os.getenv("OPENAI_BASE_URL"),
        temperature=0.0
    )
    adapter = LangGraphAdapter(
        llm=llm,
        checkpointer=InMemorySaver(),
        custom_section=FIRST_RESPONDER_PROMPT
    )
    logger.info("Connecting First Responder AI to the Band platform...")
    agent = Agent.create(adapter=adapter, agent_id=agent_id, api_key=api_key)
    await agent.run()

# ==========================================
# 3. SERVER & BACKGROUND HANDLING (STAGGERED BOOT)
# ==========================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Build the room and arrange the structure as soon as the server starts
    await build_cekap_infrastructure()
    
    agent_tasks = []
    
    async def start_agents_staggered():
        logger.info("Starting agents in stages (Staggered Boot) to avoid WebSocket Timeout...")
        
        # Pause for 2 seconds for each agent so WebSocket traffic isn't congested
        agent_tasks.append(asyncio.create_task(first_responder_main()))
        await asyncio.sleep(2)
        
        agent_tasks.append(asyncio.create_task(manager_main()))
        await asyncio.sleep(2)
        
        agent_tasks.append(asyncio.create_task(dispatcher_main()))
        await asyncio.sleep(2)
        
        agent_tasks.append(asyncio.create_task(geo_main()))
        await asyncio.sleep(2)
        
        agent_tasks.append(asyncio.create_task(medical_main()))
        await asyncio.sleep(2)
        
        agent_tasks.append(asyncio.create_task(triage_main()))
        
        logger.info("✅ ALL AGENTS SUCCESSFULLY STARTED AND READY!")

    # Start the staggered boot process
    boot_task = asyncio.create_task(start_agents_staggered())
    
    yield  
    
    boot_task.cancel()
    for task in agent_tasks:
        task.cancel()

# Initialize FastAPI app with lifespan
app = FastAPI(lifespan=lifespan)

# ==========================================
# 4. PHASE 3: PWA ENDPOINT USING REST
# ==========================================
@app.post("/api/chat")
async def handle_chat(request: ChatRequest):
    global processed_msg_ids
    if not CEKAP_ROOM_ID or not first_responder_client:
        return {"status": "ACTIVE", "reply": "System is loading, please wait..."}

    user_text = request.message.strip()
    
    try:
        # 1. Send the caller's message to the room using the Dispatcher identity (as a PWA system proxy)
        # This ensures the First Responder agent (LLM) reads it as an external message
        disp_id, disp_key = AGENTS["dispatcher"]
        disp_client = AsyncRestClient(api_key=disp_key, base_url=BAND_URL)
        
        fr_id, _ = AGENTS["first_responder"]
        items = [ChatMessageRequestMentionsItem(id=fr_id, name="first_responder")]
        
        await disp_client.agent_api_messages.create_agent_chat_message(
            CEKAP_ROOM_ID, 
            message=ChatMessageRequest(
                content=f"@first_responder [CALLER]: {user_text}", 
                mentions=items
            )
        )
        
        # 2. Polling for answer: Monitoring Band message inbox for Dispatcher (because First Responder is instructed to tag dispatcher to reply to caller)
        for _ in range(15):
            await asyncio.sleep(2)
            try:
                resp = await disp_client.agent_api_messages.get_agent_next_message(CEKAP_ROOM_ID)
                msg_data = getattr(resp, "data", None)
                if msg_data:
                    content = getattr(msg_data, "content", "")
                    msg_id = getattr(msg_data, "id", None)
                    
                    # Filter strictly so ONLY the official First Responder message to the caller is displayed
                    if content and msg_id not in processed_msg_ids and "@Caller" in content:
                        processed_msg_ids.add(msg_id)
                        
                        # Clean technical tags before sending to the user's TTS function
                        clean_reply = content.replace("@Caller", "").replace("@dispatcher", "").replace("@Dispatcher", "").replace("_", "").replace("*", "").strip()
                        
                        if "TERMINATE" in clean_reply.upper():
                            return {"status": "TERMINATE_CALL", "reply": "Call terminated forcefully."}
                            
                        return {"status": "ACTIVE", "reply": clean_reply}
            except Exception:
                pass # Continue polling if there are no new messages

        return {"status": "ACTIVE", "reply": "System is processing information and coordinating units. Please wait..."}

    except Exception as e:
        logger.error(f"Phase 3 Error: {str(e)}")
        return {"status": "ERROR", "reply": "System is experiencing network congestion."}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=10000)