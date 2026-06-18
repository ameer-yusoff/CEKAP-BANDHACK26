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

from thenvoi import Agent
from thenvoi.adapters import LangGraphAdapter
from thenvoi.config import load_agent_config

from prompts import FIRST_RESPONDER_PROMPT
from dispatcher_agent import main as dispatcher_main
from geo_agent import main as geo_main
from manager_agent import main as manager_main
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

# AGGRESSIVE TOOL EXTRACTION
band_tools = []
for obj in [adapter, band_agent]:
    for attr in ['tools', '_tools', 'platform_tools', 'additional_tools']:
        if hasattr(obj, attr):
            val = getattr(obj, attr)
            if isinstance(val, list):
                band_tools.extend(val)

unique_tools = {}
for t in band_tools:
    name = getattr(t, 'name', None)
    if name: 
        unique_tools[name] = t
final_tools = list(unique_tools.values())

react_agent = create_react_agent(llm, tools=final_tools)
chat_memory = [SystemMessage(content=FIRST_RESPONDER_PROMPT)]
is_room_setup = False

# ==========================================
# 2. ALTERNATIVE 2: PROGRAMMATIC ROOM SETUP
# ==========================================
async def programmatic_room_setup() -> str:
    """
    Execute room setup process via native API (bypassing LLM).
    100% free from hallucination errors or AI safety rejection.
    """
    logger.info("SYSTEM OVERRIDE: Starting Programmatic Room Setup via REST API...")
    try:
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        
        async with httpx.AsyncClient() as client:
            # Langkah 1: Cipta Bilik
            res = await client.post("https://app.thenvoi.com/api/v1/agent/chats", headers=headers, json={"name": "Emergency Incident"})
            if res.status_code not in [200, 201]:
                logger.error(f"Failed to create room: {res.text}")
                return None
                
            chat_id = res.json().get("id")
            logger.info(f"Room successfully created programmatically. ID: {chat_id}")
            
            # Langkah 2: Tambah Peserta
            participants = ["agent_manager", "triage_diagnoser", "geo_specialist", "medical_agent", "dispatcher"]
            for p in participants:
                await client.post(
                    f"https://app.thenvoi.com/api/v1/agent/chats/{chat_id}/participants",
                    headers=headers,
                    json={"username": p}
                )
                
            # Langkah 3: Hantar Mesej Pencetus Pemasangan (Trigger)
            await client.post(
                f"https://app.thenvoi.com/api/v1/agent/chats/{chat_id}/messages",
                headers=headers,
                json={"text": "@agent_manager System Online. Ready for triage."}
            )
            
        logger.info("Programmatic setup completed successfully.")
        return chat_id
    except Exception as e:
        logger.error(f"Programmatic Setup Error: {str(e)}")
        return None

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
    global chat_memory, is_room_setup 
    
    user_text = request.message.strip()
    if not user_text:
        raise HTTPException(status_code=400, detail="Message cannot be empty")

    try:        
        # Execute Alternative 2: Programmatic Setup
        if not is_room_setup:
            chat_id = await programmatic_room_setup()
            if chat_id:
                # Inject context awareness into LLM memory
                system_notice = HumanMessage(
                    content=f"SYSTEM NOTICE: The chat room has been created automatically. The Chat ID is '{chat_id}'. "
                            f"DO NOT execute Step 1 (Initialize Room). Proceed directly to Step 2. "
                            f"Use this Chat ID '{chat_id}' when calling 'thenvoi_send_message' in Step 3."
                )
                chat_memory.append(system_notice)
                is_room_setup = True

        chat_memory.append(HumanMessage(content=user_text))
        logger.info("Processing caller input...")
        
        response = await react_agent.ainvoke({"messages": chat_memory})
        chat_memory = response["messages"]
        final_ai_msg = chat_memory[-1].content
        
        # STRICT PARSING LOGIC: No more fallback that leaks LLM monologue
        reply_to_pwa = []
        for line in final_ai_msg.split('\n'):
            line = line.strip()
            # Only capture text explicitly generated for the user
            if line.lower().startswith('@caller'):
                clean_line = re.sub(r'^@caller[:,\s]*', '', line, flags=re.IGNORECASE)
                clean_line = re.sub(r'[*#_]', '', clean_line)
                reply_to_pwa.append(clean_line)
        
        final_reply = " ".join(reply_to_pwa).strip()
        
        # If AI is performing technical tasks in the background and hasn't replied yet:
        if not final_reply:
            final_reply = "CEKAP system is processing your report. Please wait a moment..."
            
        return {
            "status": "ACTIVE",
            "reply": final_reply
        }
        
    except Exception as e:
        logger.error(f"API Processing Error: {str(e)}")
        return {
            "status": "ERROR",
            "reply": "Sorry, the CEKAP system encountered a network disruption. Please try again shortly."
        }