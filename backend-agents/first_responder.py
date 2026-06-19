import asyncio
import logging
import os
import re
from dotenv import load_dotenv

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from contextlib import asynccontextmanager
from supabase import create_client

from band.client.rest import AsyncRestClient, ChatRoomRequest, ParticipantRequest, ChatMessageRequest, ChatMessageRequestMentionsItem
from thenvoi.config import load_agent_config

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
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
# 1. CREDENTIALS & GLOBALS
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
medical_alert_given = False # Track if medical advice was already given to caller

# LOCAL MEMORY FOR FIRST RESPONDER
pwa_chat_memory = [SystemMessage(content=FIRST_RESPONDER_PROMPT)]
local_llm = ChatOpenAI(
    model="deepseek/deepseek-chat",
    api_key=os.getenv("OPENAI_API_KEY"),
    base_url=os.getenv("OPENAI_BASE_URL"),
    temperature=0.0
)

# ==========================================
# 2. DYNAMIC INFRASTRUCTURE (ROOM CREATION)
# ==========================================
async def build_cekap_infrastructure():
    global CEKAP_ROOM_ID
    logger.info("Starting DYNAMIC CEKAP infrastructure build...")
    
    try:
        fr_id, fr_key = AGENTS["first_responder"]
        rest_client = AsyncRestClient(api_key=fr_key, base_url=BAND_URL)
        supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))
        
        # Always create a fresh room for a new mission
        logger.info("Creating a fresh Operations Room...")
        resp = await rest_client.agent_api_chats.create_agent_chat(chat=ChatRoomRequest())
        CEKAP_ROOM_ID = resp.data.id
        
        try:
            supabase.table("emergency_logs").insert({
                "emergency_type": "SYSTEM", "priority_level": "N/A", 
                "injuries": "N/A", "raw_location": CEKAP_ROOM_ID, "status": "ACTIVE_ROOM" 
            }).execute()
        except Exception:
            pass
        
        # Add all support agents (except First Responder, because FR is now LOCAL)
        for name, (agent_id, api_key) in AGENTS.items():
            if name == "first_responder": continue 
            await rest_client.agent_api_participants.add_agent_chat_participant(
                CEKAP_ROOM_ID, participant=ParticipantRequest(participant_id=agent_id, role="member")
            )
        logger.info("All support agents added to the new operations room.")

    except Exception as e:
        logger.error(f"Infrastructure build failed: {str(e)}")

# ==========================================
# 3. SERVER & STAGGERED BOOT
# ==========================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Do not pre-build room here. Let the first call trigger it.
    agent_tasks = []
    
    async def start_agents_staggered():
        logger.info("Booting support agents sequentially...")
        for agent_func in [manager_main, dispatcher_main, geo_main, medical_main, triage_main]:
            agent_tasks.append(asyncio.create_task(agent_func()))
            await asyncio.sleep(2)
        logger.info("ALL SUPPORT AGENTS ONLINE!")

    boot_task = asyncio.create_task(start_agents_staggered())
    yield  
    boot_task.cancel()
    for task in agent_tasks: task.cancel()

app = FastAPI(lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

class ChatRequest(BaseModel):
    message: str

# ==========================================
# 4. PHASE 3: PWA DIRECT CHAT & RELAY
# ==========================================
@app.post("/api/chat")
async def handle_chat(request: ChatRequest):
    global CEKAP_ROOM_ID, pwa_chat_memory, medical_alert_given
    user_text = request.message.strip()

    if CEKAP_ROOM_ID:
        try:
            supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))
            res = supabase.table("emergency_logs").select("status").eq("raw_location", CEKAP_ROOM_ID).execute()
            if res.data and res.data[0]["status"] == "RETIRED_ROOM":
                logger.info("Bilik operasi terdahulu telah ditamatkan. Memulakan sesi kecemasan baharu.")
                CEKAP_ROOM_ID = None
                pwa_chat_memory = [SystemMessage(content=FIRST_RESPONDER_PROMPT)]
                medical_alert_given = False
        except Exception:
            pass

    pwa_chat_memory.append(HumanMessage(content=user_text))
    
    try:
        response = local_llm.invoke(pwa_chat_memory)
        ai_reply = response.content.strip()
        
        if "<TRANSFER_TO_MANAGER:" in ai_reply:
            extracted_info = ai_reply.split("<TRANSFER_TO_MANAGER:")[-1].split(">")[0].strip()
            
            clean_ui_reply = ai_reply.split("<TRANSFER_TO_MANAGER:")[0].strip()
            
            if not clean_ui_reply:
                fallback_prompt = "Say 'Thank you, please wait while help is dispatched.' naturally in the exact language the caller used."
                fallback = local_llm.invoke(pwa_chat_memory + [HumanMessage(content=fallback_prompt)]).content
                clean_ui_reply = fallback.replace('"', '').strip()
                
            pwa_chat_memory.append(AIMessage(content=clean_ui_reply))
            medical_alert_given = False 
            
            if not CEKAP_ROOM_ID:
                await build_cekap_infrastructure()
                
            fr_id, fr_key = AGENTS["first_responder"]
            mgr_id, _ = AGENTS["agent_manager"]
            rest_client = AsyncRestClient(api_key=fr_key, base_url=BAND_URL)
            
            await rest_client.agent_api_messages.create_agent_chat_message(
                CEKAP_ROOM_ID, 
                message=ChatMessageRequest(
                    content=f"@agent_manager EMERGENCY REPORT FROM FRONT DESK: {extracted_info}", 
                    mentions=[ChatMessageRequestMentionsItem(id=mgr_id, name="agent_manager")]
                )
            )
            
            for _ in range(8):
                await asyncio.sleep(1.5)
                try:
                    resp = await rest_client.agent_api_messages.get_agent_chat_messages(chat_id=CEKAP_ROOM_ID, page=1)
                    messages = getattr(resp, "data", [])
                    
                    for msg in messages:
                        content = getattr(msg, "content", "")
                        
                        if "SYSTEM_MEDICAL_ALERT:" in content and not medical_alert_given:
                            medical_steps = content.split("SYSTEM_MEDICAL_ALERT:")[-1].strip()
                            medical_alert_given = True
                            
                            trans_prompt = f"Translate and format these first-aid steps naturally into the exact language the caller is using: {medical_steps}"
                            translated_med = local_llm.invoke(pwa_chat_memory + [HumanMessage(content=trans_prompt)]).content.strip()
                            
                            final_reply = f"{clean_ui_reply}\n\n{translated_med}"
                            pwa_chat_memory.append(AIMessage(content=translated_med))
                            return {"status": "ACTIVE", "reply": final_reply}
                            
                        if "MISSION_SUCCESS" in content:
                            CEKAP_ROOM_ID = None 
                            term_prompt = "Say EXACTLY 'Rescue units have been successfully dispatched. Terminating call.' but naturally in the caller's language."
                            translated_term = local_llm.invoke(pwa_chat_memory + [HumanMessage(content=term_prompt)]).content.strip()
                            
                            final_reply = f"{clean_ui_reply}\n\n{translated_term}"
                            pwa_chat_memory = [SystemMessage(content=FIRST_RESPONDER_PROMPT)] 
                            return {"status": "TERMINATE_CALL", "reply": final_reply}
                            
                except Exception:
                    pass

            return {"status": "ACTIVE", "reply": clean_ui_reply}

        clean_ui_reply = re.sub(r'<TRANSFER_TO_MANAGER:.*?>', '', ai_reply).strip()
        pwa_chat_memory.append(AIMessage(content=clean_ui_reply))
        return {"status": "ACTIVE", "reply": clean_ui_reply}

    except Exception as e:
        logger.error(f"PWA Processing Error: {str(e)}")
        
        try:
            err_prompt = "Say 'The system is processing. Please stay calm.' naturally in the exact language the caller used."
            err_reply = local_llm.invoke(pwa_chat_memory + [HumanMessage(content=err_prompt)]).content.replace('"', '').strip()
        except Exception:
            err_reply = "Sistem sedang memproses. Harap bertenang."
            
        return {"status": "ERROR", "reply": err_reply}

@app.get("/api/logs")
async def get_emergency_logs():
    try:
        supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))
        
        res = supabase.table("emergency_logs") \
                      .select("*") \
                      .neq("status", "ACTIVE_ROOM") \
                      .neq("status", "RETIRED_ROOM") \
                      .order("created_at", desc=True) \
                      .execute()
                      
        return {"status": "success", "data": res.data}
    except Exception as e:
        logger.error(f"Fetch Logs Error from Admin Dashboard: {str(e)}")
        return {"status": "error", "message": "Gagal mengakses pangkalan data."}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=10000)