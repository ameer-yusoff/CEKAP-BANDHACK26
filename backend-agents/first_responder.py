# first_responder.py

import asyncio
import logging
import os
import re
from dotenv import load_dotenv

# Add Import for FastAPI
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from contextlib import asynccontextmanager
import uvicorn

# LangChain and LangGraph imports
from langchain_openai import ChatOpenAI
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver

# Band SDK imports
from thenvoi import Agent
from thenvoi.adapters import LangGraphAdapter
from thenvoi.config import load_agent_config

# Import the centralized strict system prompt
from prompts import FIRST_RESPONDER_PROMPT

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()

# ==========================================
# 1. BAND AGENT INITIALIZATION
# ==========================================
agent_id, api_key = load_agent_config("first_responder")
chat_memory = [{"role": "system", "content": FIRST_RESPONDER_PROMPT}]

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

band_agent = Agent.create(
    adapter=adapter,
    agent_id=agent_id,
    api_key=api_key
)

# ==========================================
# 2. FASTAPI & LIFESPAN CONFIGURATION
# ==========================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Mulakan Band Agent di latar belakang apabila FastAPI 'start'
    logger.info("Menyambungkan First Responder ke platform Band di latar belakang...")
    agent_task = asyncio.create_task(band_agent.run())
    yield
    # Hentikan Band Agent dengan selamat apabila FastAPI 'shutdown'
    agent_task.cancel()

app = FastAPI(title="CEKAP First Responder API", lifespan=lifespan)

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
# 3. ENDPOINT API FOR PWA FRONTEND
# ==========================================
@app.post("/api/chat")
async def handle_chat(request: ChatRequest):
    user_text = request.message.strip()
    
    if not user_text:
        raise HTTPException(status_code=400, detail="Mesej tidak boleh kosong")
        
    fake_keywords = ["main-main", "test", "testing", "prank", "gurau"]
    if any(keyword in user_text.lower() for keyword in fake_keywords):
        logger.warning("AMARAN: Panggilan palsu dikesan dan disekat.")
        return {
            "status": "TERMINATE_CALL",
            "reply": "Panggilan ditamatkan serta-merta kerana sistem mengesan cubaan panggilan palsu."
        }

    try:        
        chat_memory.append({"role": "user", "content": user_text})
        
        response = await llm.ainvoke(chat_memory)
        
        raw_reply = response.content
        clean_reply = re.sub(r'[*#_]', '', raw_reply) 
        
        chat_memory.append({"role": "assistant", "content": raw_reply})

        return {
            "status": "ACTIVE",
            "reply": clean_reply
        }
        
    except Exception as e:
        logger.error(f"Ralat Pemprosesan API: {str(e)}")
        return {
            "status": "ERROR",
            "reply": "Harap maaf, sistem CEKAP mengalami gangguan rangkaian. Sila ulang semula."
        }

# ==========================================
# 4. SERVER LAUNCH
# ==========================================
if __name__ == "__main__":
    try:
        uvicorn.run(app, host="127.0.0.1", port=8000)
    except KeyboardInterrupt:
        logger.info("Sistem First Responder dihentikan oleh pengguna.")