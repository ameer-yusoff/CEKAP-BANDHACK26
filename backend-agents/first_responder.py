import asyncio
import logging
import os
import time
import re
from dotenv import load_dotenv

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from contextlib import asynccontextmanager
from supabase import create_client

# Official Band REST Client Library
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

# ==========================================
# 1. CREDENTIAL MANAGEMENT & GLOBALS
# ==========================================
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

# ==========================================
# 2. PHASE 1 & 2: DYNAMIC INFRASTRUCTURE
# ==========================================
async def build_cekap_infrastructure():
    global CEKAP_ROOM_ID, first_responder_client
    logger.info("Starting DYNAMIC CEKAP infrastructure build...")
    
    try:
        fr_id, fr_key = AGENTS["first_responder"]
        first_responder_client = AsyncRestClient(api_key=fr_key, base_url=BAND_URL)
        supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))
        
        needs_new_room = True
        
        # 1. Check for an existing active room
        try:
            res = supabase.table("emergency_logs").select("id, raw_location").eq("status", "ACTIVE_ROOM").execute()
            if res.data and len(res.data) > 0:
                potential_room_id = res.data[0]["raw_location"]
                record_id = res.data[0]["id"]
                
                # 2. DYNAMIC CHECK: Verify message count to avoid 403 limit
                # We fetch the first page of messages and check total_count (or simulate check)
                try:
                    msg_res = await first_responder_client.agent_api_messages.get_agent_chat_messages(chat_id=potential_room_id, page=1)
                    # Safe check if the attribute exists or fallback to guessing it's safe if < 900
                    total_messages = getattr(getattr(msg_res, "meta", None), "total", 0)
                    
                    if total_messages < 900:
                        CEKAP_ROOM_ID = potential_room_id
                        needs_new_room = False
                        logger.info(f"Existing healthy room found ({total_messages} msgs). Reusing: {CEKAP_ROOM_ID}")
                    else:
                        logger.warning(f"Room {potential_room_id} is reaching limit ({total_messages} msgs). Retiring room.")
                        # Retire the old room
                        supabase.table("emergency_logs").update({"status": "RETIRED_ROOM"}).eq("id", record_id).execute()
                except Exception as e:
                    logger.warning(f"Could not verify room health, assuming it's full. Retiring. Error: {e}")
                    supabase.table("emergency_logs").update({"status": "RETIRED_ROOM"}).eq("id", record_id).execute()
        except Exception as e:
            logger.info(f"No active room found in DB. Creating new one. {e}")

        # 3. Create a new room if needed
        if needs_new_room:
            logger.info("Creating a fresh Operations Room...")
            resp = await first_responder_client.agent_api_chats.create_agent_chat(chat=ChatRoomRequest())
            CEKAP_ROOM_ID = resp.data.id
            logger.info(f"PHASE 1 SUCCESS: New Room created -> {CEKAP_ROOM_ID}")
            
            # Save new room ID to Supabase with the consistent "ACTIVE_ROOM" tag
            try:
                supabase.table("emergency_logs").insert({
                    "emergency_type": "SYSTEM", "priority_level": "N/A", 
                    "injuries": "N/A", "raw_location": CEKAP_ROOM_ID, "status": "ACTIVE_ROOM" 
                }).execute()
            except Exception as e:
                logger.error(f"Failed to log new room in DB: {e}")
            
            # PHASE 2: Add all support agents into the new room
            for name, (agent_id, api_key) in AGENTS.items():
                if name == "first_responder":
                    continue 
                
                await first_responder_client.agent_api_participants.add_agent_chat_participant(
                    CEKAP_ROOM_ID, 
                    participant=ParticipantRequest(participant_id=agent_id, role="member")
                )
                logger.info(f"Added to room: @{name}")
                
            logger.info("PHASE 2 SUCCESS: All agents are ready in the new operations room.")

    except Exception as e:
        logger.error(f"Failed to build dynamic infrastructure: {str(e)}")

# ==========================================
# FIRST RESPONDER AI BRAIN
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
    await build_cekap_infrastructure()
    
    agent_tasks = []
    
    async def start_agents_staggered():
        logger.info("Booting agents in a staggered sequence to prevent WebSocket Timeout...")
        
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
        
        logger.info("ALL AGENTS SUCCESSFULLY BOOTED AND READY!")

    boot_task = asyncio.create_task(start_agents_staggered())
    yield  
    boot_task.cancel()
    for task in agent_tasks:
        task.cancel()

# ==========================================
# APP INITIALIZATION
# ==========================================
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

# ==========================================
# 4. PHASE 3: PWA ENDPOINT USING REST PROXY
# ==========================================
@app.post("/api/chat")
async def handle_chat(request: ChatRequest):
    global processed_msg_ids
    if not CEKAP_ROOM_ID or not first_responder_client:
        return {"status": "ACTIVE", "reply": "System is booting up, please wait..."}

    user_text = request.message.strip()
    
    try:
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
        
        for _ in range(15):
            await asyncio.sleep(2)
            try:
                resp = await disp_client.agent_api_messages.get_agent_next_message(CEKAP_ROOM_ID)
                msg_data = getattr(resp, "data", None)
                if msg_data:
                    content = getattr(msg_data, "content", "")
                    msg_id = getattr(msg_data, "id", None)
                    
                    if content and msg_id not in processed_msg_ids and "@Caller" in content:
                        processed_msg_ids.add(msg_id)
                        
                        # ADVANCED CLEANUP
                        clean_reply = re.sub(r'\[\{.*?\}\]', '', content) 
                        clean_reply = re.sub(r'@[a-zA-Z0-9_]+', '', clean_reply) 
                        clean_reply = clean_reply.replace("Please relay these steps to the caller:", "")
                        clean_reply = re.sub(r'[*#_]', '', clean_reply).strip()
                        
                        if "TERMINATE" in clean_reply.upper():
                            return {"status": "TERMINATE_CALL", "reply": "Call forcefully terminated due to policy violation."}
                            
                        return {"status": "ACTIVE", "reply": clean_reply}
            except Exception:
                pass 

        return {"status": "ACTIVE", "reply": "System is processing information and coordinating units. Please wait..."}

    except Exception as e:
        logger.error(f"Phase 3 Error: {str(e)}")
        return {"status": "ERROR", "reply": "System is experiencing network congestion."}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=10000)