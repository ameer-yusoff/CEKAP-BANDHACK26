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
processed_msg_ids = set()

# ==========================================
# 2. PHASE 1 & 2: DYNAMIC INFRASTRUCTURE
# ==========================================
async def build_cekap_infrastructure():
    global CEKAP_ROOM_ID
    logger.info("Starting DYNAMIC CEKAP infrastructure build...")
    
    try:
        fr_id, fr_key = AGENTS["first_responder"]
        first_responder_client = AsyncRestClient(api_key=fr_key, base_url=BAND_URL)
        supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))
        
        needs_new_room = True
        
        try:
            res = supabase.table("emergency_logs").select("id, raw_location").eq("status", "ACTIVE_ROOM").execute()
            if res.data and len(res.data) > 0:
                potential_room_id = res.data[0]["raw_location"]
                record_id = res.data[0]["id"]
                
                try:
                    msg_res = await first_responder_client.agent_api_messages.get_agent_chat_messages(chat_id=potential_room_id, page=1)
                    total_messages = getattr(getattr(msg_res, "meta", None), "total", 0)
                    
                    if total_messages < 900:
                        CEKAP_ROOM_ID = potential_room_id
                        needs_new_room = False
                        logger.info(f"Reusing healthy room: {CEKAP_ROOM_ID}")
                    else:
                        supabase.table("emergency_logs").update({"status": "RETIRED_ROOM"}).eq("id", record_id).execute()
                except Exception:
                    supabase.table("emergency_logs").update({"status": "RETIRED_ROOM"}).eq("id", record_id).execute()
        except Exception:
            pass

        if needs_new_room:
            logger.info("Creating a fresh Operations Room...")
            resp = await first_responder_client.agent_api_chats.create_agent_chat(chat=ChatRoomRequest())
            CEKAP_ROOM_ID = resp.data.id
            
            try:
                supabase.table("emergency_logs").insert({
                    "emergency_type": "SYSTEM", "priority_level": "N/A", 
                    "injuries": "N/A", "raw_location": CEKAP_ROOM_ID, "status": "ACTIVE_ROOM" 
                }).execute()
            except Exception:
                pass
            
            for name, (agent_id, api_key) in AGENTS.items():
                if name == "first_responder": continue 
                await first_responder_client.agent_api_participants.add_agent_chat_participant(
                    CEKAP_ROOM_ID, participant=ParticipantRequest(participant_id=agent_id, role="member")
                )
            logger.info("All agents added to new operations room.")

    except Exception as e:
        logger.error(f"Infrastructure build failed: {str(e)}")

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
    adapter = LangGraphAdapter(llm=llm, checkpointer=InMemorySaver(), custom_section=FIRST_RESPONDER_PROMPT)
    agent = Agent.create(adapter=adapter, agent_id=agent_id, api_key=api_key)
    await agent.run()

# ==========================================
# 3. SERVER & STAGGERED BOOT
# ==========================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    await build_cekap_infrastructure()
    agent_tasks = []
    
    async def start_agents_staggered():
        logger.info("Booting agents sequentially to prevent WebSocket Timeout...")
        for agent_func in [first_responder_main, manager_main, dispatcher_main, geo_main, medical_main, triage_main]:
            agent_tasks.append(asyncio.create_task(agent_func()))
            await asyncio.sleep(2)
        logger.info("ALL AGENTS SYSTEM ONLINE!")

    boot_task = asyncio.create_task(start_agents_staggered())
    yield  
    boot_task.cancel()
    for task in agent_tasks: task.cancel()

app = FastAPI(lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

class ChatRequest(BaseModel):
    message: str

# ==========================================
# 4. PHASE 3: PWA ENDPOINT (CLEAN RELAY)
# ==========================================
@app.post("/api/chat")
async def handle_chat(request: ChatRequest):
    global processed_msg_ids, CEKAP_ROOM_ID
    
    # AUTOMATED RESET: Build new room if previous was retired/completed
    if not CEKAP_ROOM_ID:
        await build_cekap_infrastructure()
        
    if not CEKAP_ROOM_ID:
        return {"status": "ACTIVE", "reply": "System is booting up, please wait..."}

    user_text = request.message.strip()
    
    try:
        disp_id, disp_key = AGENTS["dispatcher"]
        disp_client = AsyncRestClient(api_key=disp_key, base_url=BAND_URL)
        fr_id, _ = AGENTS["first_responder"]
        
        # --- BUG FIX: PRELOAD OLD MESSAGES TO AVOID GHOSTING ---
        try:
            old_msgs = await disp_client.agent_api_messages.get_agent_chat_messages(chat_id=CEKAP_ROOM_ID, page=1)
            for m in getattr(old_msgs, "data", []):
                processed_msg_ids.add(getattr(m, "id"))
        except Exception as e:
            logger.warning(f"Failed to preload messages: {e}")

        # Inject Caller's Message
        send_resp = await disp_client.agent_api_messages.create_agent_chat_message(
            CEKAP_ROOM_ID, 
            message=ChatMessageRequest(
                content=f"@first_responder [CALLER_INPUT]: {user_text}", 
                mentions=[ChatMessageRequestMentionsItem(id=fr_id, name="first_responder")]
            )
        )
        
        # Avoid parsing our own proxy message
        my_msg_id = getattr(getattr(send_resp, "data", None), "id", None)
        if my_msg_id:
            processed_msg_ids.add(my_msg_id)
        
        # Poll for 30 seconds
        for _ in range(15):
            await asyncio.sleep(2)
            try:
                # READ-ONLY polling
                resp = await disp_client.agent_api_messages.get_agent_chat_messages(chat_id=CEKAP_ROOM_ID, page=1)
                messages = getattr(resp, "data", [])
                
                # Check chronologically (oldest to newest)
                for msg in reversed(messages):
                    content = getattr(msg, "content", "")
                    msg_id = getattr(msg, "id", None)
                    
                    if content and msg_id not in processed_msg_ids:
                        processed_msg_ids.add(msg_id)
                        
                        # Trigger system termination upon successful dispatch
                        if "MISSION_SUCCESS" in content:
                            CEKAP_ROOM_ID = None # Force the next caller to generate a new room
                            return {"status": "TERMINATE_CALL", "reply": "Pasukan penyelamat sedang bergegas ke lokasi anda. Panggilan ditamatkan."}
                        
                        # Cleanly extract messages meant for the Caller
                        if "CALLER:" in content:
                            clean_reply = content.split("CALLER:")[-1].strip()
                            return {"status": "ACTIVE", "reply": clean_reply}
            except Exception:
                pass

        return {"status": "ACTIVE", "reply": "Sistem sedang menyelaras unit tindakan. Sila tunggu..."}

    except Exception as e:
        logger.error(f"Phase 3 Error: {str(e)}")
        return {"status": "ERROR", "reply": "System network congestion."}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=10000)