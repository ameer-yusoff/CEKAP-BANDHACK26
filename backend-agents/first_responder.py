import asyncio
import logging
import os
import re
from dotenv import load_dotenv

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from contextlib import asynccontextmanager

from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent
from langchain_core.messages import SystemMessage, HumanMessage
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

# Konfigurasi Log (Matang & Realistik untuk Pemantauan)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()

# ==========================================
# INISIALISASI TERAS (CORE INITIALIZATION)
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

# Pembolehubah Kawalan Global
local_react_agent = None
chat_memory = []
is_setup_complete = False

# ==========================================
# FASA 1 & 2: SETUP BERPROGRAM (PROGRAMMATIC)
# ==========================================
async def execute_programmatic_setup():
    global local_react_agent, chat_memory, is_setup_complete
    
    # Tunggu pengesahan WebSocket Band menyuntik tools ke dalam memori
    logger.info("Menyegerakkan SDK Band (Mengambil masa 5-8 saat)...")
    await asyncio.sleep(8)
    
    # Mengekstrak tools secara berprogram daripada adapter
    band_tools = []
    for attr in ['tools', '_tools', 'platform_tools']:
        if hasattr(adapter, attr):
            val = getattr(adapter, attr)
            if isinstance(val, list):
                band_tools.extend(val)
    
    if hasattr(adapter, 'get_tools') and callable(adapter.get_tools):
        t = adapter.get_tools()
        band_tools.extend(t if isinstance(t, list) else [t])

    # Menyaring tools untuk mengelakkan duplikasi fungsi
    unique_tools = {t.name: t for t in band_tools}.values()
    tool_names = [t.name for t in unique_tools]
    logger.info(f"Modul Band diekstrak dengan jayanya: {tool_names}")

    # Membina 'Local Orchestrator' menggunakan alat yang telah disahkan
    local_react_agent = create_react_agent(llm, tools=list(unique_tools))
    
    sys_msg = SystemMessage(content=FIRST_RESPONDER_PROMPT)
    chat_memory = [sys_msg]
    
    # Menjalankan FASA 1 (Cipta Bilik) & FASA 2 (Masukkan Ejen) secara programatik
    setup_instruction = """
    SYSTEM OVERRIDE - PROGRAMMATIC EXECUTION:
    You must prepare the Band environment immediately using your tools.
    Step 1: Use 'thenvoi_create_chatroom' to create a room named "CEKAP Operation Center".
    Step 2: Use 'thenvoi_add_participant' to add exactly these agents into the room:
    - @agent_manager
    - @triage_diagnoser
    - @geo_specialist
    - @medical_agent
    - @dispatcher
    
    Do not ask questions. Reply 'SETUP_SUCCESS' when done.
    """
    
    try:
        logger.info("Melaksanakan Automasi FASA 1 & FASA 2...")
        res = await local_react_agent.ainvoke({"messages": [sys_msg, HumanMessage(content=setup_instruction)]})
        logger.info(f"Status Infrastruktur: {res['messages'][-1].content}")
        is_setup_complete = True
    except Exception as e:
        logger.error(f"Ralat Eksekusi Setup Berprogram: {e}")

# ==========================================
# SERVER FASTAPI & PENGURUSAN LATAR BELAKANG
# ==========================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Enjin CEKAP mula beroperasi...")
    agent_tasks = []
    
    # Menjalankan kesemua entiti ejen di latar belakang (Windows / Render Serasi)
    agent_tasks.extend([
        asyncio.create_task(band_agent.run()),
        asyncio.create_task(dispatcher_main()),
        asyncio.create_task(geo_main()),
        asyncio.create_task(manager_main()),
        asyncio.create_task(medical_main()),
        asyncio.create_task(triage_main())
    ])
    
    # Memulakan proses Fasa 1 & 2
    asyncio.create_task(execute_programmatic_setup())
    
    yield  
    
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

# ==========================================
# FASA 3: OPERASI PWA & PENGENDALIAN CALLER
# ==========================================
@app.post("/api/chat")
async def handle_chat(request: ChatRequest):
    global chat_memory
    
    if not is_setup_complete:
        return {"status": "ACTIVE", "reply": "Infrastruktur kecemasan sedang dimuatkan. Sila tunggu seketika..."}

    user_text = request.message.strip()
    if not user_text:
        raise HTTPException(status_code=400, detail="Mesej kosong.")

    try:
        # Pengekalan konteks memori setempat
        chat_memory.append(HumanMessage(content=f"[Caller]: {user_text}"))
        
        logger.info("Memproses FASA 3: Interaksi logik pemanggil...")
        response = await local_react_agent.ainvoke({
            "messages": chat_memory
        })
        
        chat_memory = response["messages"]
        final_ai_msg = chat_memory[-1].content
        
        # Format penapisan bersih (Clean UI/UX Output)
        reply_to_pwa = []
        for line in final_ai_msg.split('\n'):
            line = line.strip()
            if line.lower().startswith('@caller'):
                clean_line = re.sub(r'^@caller[:,\s]*', '', line, flags=re.IGNORECASE).strip()
                clean_line = re.sub(r'[*#_]', '', clean_line)
                reply_to_pwa.append(clean_line)
        
        final_reply = " ".join(reply_to_pwa).strip()
        
        # Perlindungan Pemutusan Panggilan Palsu
        if "TERMINATE" in final_reply.upper():
            return {"status": "TERMINATE_CALL", "reply": "Panggilan ditamatkan secara paksa akibat penyalahgunaan talian kecemasan."}
            
        # Logik lencongan apabila ejen sedang menghantar data ke Band Room
        if not final_reply:
            return {
                "status": "ACTIVE",
                "reply": "Butiran anda sedang disalurkan ke bilik komander operasi utama. Sistem sedang menyelaras..."
            }
            
        return {"status": "ACTIVE", "reply": final_reply}

    except Exception as e:
        logger.error(f"Kegagalan Logik Fasa 3: {str(e)}")
        return {"status": "ERROR", "reply": "Sistem CEKAP sedang mengalami bebanan rangkaian yang luar biasa."}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=10000)