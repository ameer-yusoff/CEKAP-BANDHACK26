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

# Dynamically extract platform tools from the Band SDK adapter
band_tools = getattr(adapter, 'tools', getattr(adapter, '_tools', getattr(adapter, 'additional_tools', [])))

# Initialize the ReAct agent without the 'state_modifier' keyword to prevent version conflicts
react_agent = create_react_agent(llm, tools=band_tools)

# Lock the system prompt into the main memory
chat_memory = [SystemMessage(content=FIRST_RESPONDER_PROMPT)]

band_agent = Agent.create(adapter=adapter, agent_id=agent_id, api_key=api_key)

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
    # Cancel all background agent tasks when the server shuts down
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
    global chat_memory  # Global declaration to ensure memory persists across sessions
    
    user_text = request.message.strip()
    
    if not user_text:
        raise HTTPException(status_code=400, detail="Message cannot be empty")

    try:        
        # Append the user's input to the memory
        chat_memory.append(HumanMessage(content=user_text))
        logger.info("Processing caller input and executing Band tools if triggered...")
        
        # Invoke the ReAct agent with the synchronized message structure
        response = await react_agent.ainvoke({"messages": chat_memory})
        
        # Update the global memory with the latest execution chain (including tool calls)
        chat_memory = response["messages"]
        
        # Extract the final spoken message from the AI
        final_ai_msg = chat_memory[-1].content
        
        # Clean out any markdown symbols to prevent TTS engine from reading them out loud
        clean_reply = re.sub(r'[*#_]', '', final_ai_msg) 
        
        return {
            "status": "ACTIVE",
            "reply": clean_reply
        }
        
    except Exception as e:
        logger.error(f"API Processing Error: {str(e)}")
        return {
            "status": "ERROR",
            "reply": "Sorry, the CEKAP system encountered a network error. Please try again."
        }