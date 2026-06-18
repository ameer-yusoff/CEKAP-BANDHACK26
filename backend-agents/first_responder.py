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
        
        # [KEMASKINI: Guna Supabase untuk elak lambakan 'Ghost Rooms' setiap kali deploy]
        supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))
        try:
            res = supabase.table("emergency_logs").select("raw_location").eq("status", "ACTIVE_ROOM").execute()
            if res.data and len(res.data) > 0:
                CEKAP_ROOM_ID = res.data[0]["raw_location"]
                logger.info(f"Bilik sedia ada dijumpai. Sistem menggunakan semula bilik: {CEKAP_ROOM_ID}")
                return
        except Exception:
            pass
            
        # PHASE 1: Build Room using REST API
        resp = await first_responder_client.agent_api_chats.create_agent_chat(chat=ChatRoomRequest())
        CEKAP_ROOM_ID = resp.data.id
        logger.info(f"PHASE 1 SUCCESS: Operations Room created -> {CEKAP_ROOM_ID}")
        
        # [KEMASKINI: Simpan ID bilik baharu ke Supabase untuk penggunaan akan datang]
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
# 3. SERVER & BACKGROUND HANDLING
# ==========================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Run agent scripts in the background so they 'Listen'
    agent_tasks = [
        asyncio.create_task(dispatcher_main()),
        asyncio.create_task(geo_main()),
        asyncio.create_task(manager_main()),
        asyncio.create_task(medical_main()),
        asyncio.create_task(triage_main())
    ]
    
    # Build the room and arrange the structure as soon as the server starts
    await build_cekap_infrastructure()
    yield  
    for task in agent_tasks:
        task.cancel()

app = FastAPI(lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

class ChatRequest(BaseModel):
    message: str

# ==========================================
# 4. PHASE 3: PWA ENDPOINT USING REST
# ==========================================
@app.post("/api/chat")
async def handle_chat(request: ChatRequest):
    if not CEKAP_ROOM_ID or not first_responder_client:
        return {"status": "ACTIVE", "reply": "System is loading, please wait..."}

    user_text = request.message.strip()
    
    try:
        # Send caller input to the Band Network using a valid REST API (No 401)
        # We tag @agent_manager so the network starts analyzing
        fr_id, _ = AGENTS["first_responder"]
        mgr_id, _ = AGENTS["agent_manager"]
        
        items = [
            ChatMessageRequestMentionsItem(id=mgr_id, name="agent_manager")
        ]
        
        await first_responder_client.agent_api_messages.create_agent_chat_message(
            CEKAP_ROOM_ID, 
            message=ChatMessageRequest(
                content=f"@agent_manager [Caller via First Responder]: {user_text}", 
                mentions=items
            )
        )
        
        # Polling for answers: Peeking into the Band message inbox for First Responder
        for _ in range(15):
            await asyncio.sleep(2)
            try:
                resp = await first_responder_client.agent_api_messages.get_agent_next_message(CEKAP_ROOM_ID)
                content = getattr(getattr(resp, "data", None), "content", None)
                
                if content and any(tag in content for tag in ["@Caller", "@First_Responder", "@first_responder"]):
                    clean_reply = content.replace("@Caller", "").replace("@First_Responder", "").replace("@first_responder", "").replace("Please relay these steps to the caller:", "").replace("_", "").replace("*", "").strip()
                    
                    if "TERMINATE" in clean_reply.upper():
                        return {"status": "TERMINATE_CALL", "reply": "Panggilan ditamatkan secara paksa."}
                        
                    return {"status": "ACTIVE", "reply": clean_reply}
            except Exception:
                pass # Continue polling if there are no new messages

        return {"status": "ACTIVE", "reply": "Information received. Action units are coordinating..."}

    except Exception as e:
        logger.error(f"Phase 3 Error: {str(e)}")
        return {"status": "ERROR", "reply": "System is experiencing network congestion."}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=10000)