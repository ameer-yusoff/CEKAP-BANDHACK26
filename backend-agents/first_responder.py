# first_responder.py

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

# Connect to Band first to ensure platform tools are injected into the objects
band_agent = Agent.create(adapter=adapter, agent_id=agent_id, api_key=api_key)

# AGGRESSIVE TOOL EXTRACTION
# Ensure local react_agent gets absolute access to all Band platform tools
band_tools = []
for obj in [adapter, band_agent]:
    for attr in ['tools', '_tools', 'platform_tools', 'additional_tools']:
        if hasattr(obj, attr):
            val = getattr(obj, attr)
            if isinstance(val, list):
                band_tools.extend(val)

# Deduplicate extracted tools to prevent errors
unique_tools = {}
for t in band_tools:
    name = getattr(t, 'name', None)
    if name: 
        unique_tools[name] = t
final_tools = list(unique_tools.values())

# Initialize local ReAct agent WITH the fully loaded platform tools
react_agent = create_react_agent(llm, tools=final_tools)

chat_memory = [SystemMessage(content=FIRST_RESPONDER_PROMPT)]
is_room_setup = False

# ==========================================
# 2. FASTAPI SERVER SETUP
# ==========================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting all CEKAP agents in the background...")
    agent_tasks = [
        asyncio.create_task(band_agent.run()),
        asyncio.create_task(dispatcher_main()),
        asyncio.create_task(geo_main()),
        asyncio.create_task(manager_main()),
        asyncio.create_task(medical_main()),
        asyncio.create_task(triage_main())
    ]
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

@app.post("/api/chat")
async def handle_chat(request: ChatRequest):
    global chat_memory, is_room_setup 
    
    user_text = request.message.strip()
    if not user_text:
        raise HTTPException(status_code=400, detail="Message cannot be empty")

    try:        
        if not is_room_setup:
            logger.info("SYSTEM OVERRIDE: Force AI to build room automatically via code...")
            setup_cmd = HumanMessage(content="SYSTEM DIRECTIVE: 1. Execute 'thenvoi_create_chatroom'. 2. Execute 'thenvoi_add_participant' to add @agent_manager, @triage_diagnoser, @geo_specialist, @medical_agent, @dispatcher to that room. 3. Execute 'thenvoi_send_message' to say '@agent_manager System Online' in that room. Execute tools NOW without asking questions.")
            
            # Execute tool operations behind the scenes first
            setup_res = await react_agent.ainvoke({"messages": chat_memory + [setup_cmd]})
            chat_memory = setup_res["messages"]
            
            logger.info(f"ROOM SETUP RESULT: {chat_memory[-1].content}")
            
            is_room_setup = True

        chat_memory.append(HumanMessage(content=user_text))
        logger.info("Processing caller input...")
        
        response = await react_agent.ainvoke({"messages": chat_memory})
        chat_memory = response["messages"]
        
        final_ai_msg = chat_memory[-1].content
        
        # PARSING LOGIC: Strictly extract only messages tagged with @Caller
        reply_to_pwa = []
        for line in final_ai_msg.split('\n'):
            line = line.strip()
            if line.lower().startswith('@caller'):
                # Remove the @Caller tag and markdown formatting
                clean_line = re.sub(r'^@caller[:,\s]*', '', line, flags=re.IGNORECASE)
                clean_line = re.sub(r'[*#_]', '', clean_line)
                reply_to_pwa.append(clean_line)
        
        # FALLBACK LOGIC: If AI forgets the tag, return text but block system/internal tags
        if not reply_to_pwa:
            for line in final_ai_msg.split('\n'):
                line_lower = line.strip().lower()
                if not line_lower.startswith('@internal') and not line_lower.startswith('@agent_'):
                    reply_to_pwa.append(re.sub(r'[*#_]', '', line.strip()))
        
        final_reply = " ".join(reply_to_pwa).strip()
        if not final_reply:
            final_reply = "Processing your request..."
            
        return {
            "status": "ACTIVE",
            "reply": final_reply
        }
        
    except Exception as e:
        logger.error(f"API Processing Error: {str(e)}")
        return {
            "status": "ERROR",
            "reply": "Sorry, the CEKAP system encountered a network error. Please try again."
        }