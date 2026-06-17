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
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver

# Band SDK imports
from thenvoi import Agent
from thenvoi.adapters import LangGraphAdapter
from thenvoi.config import load_agent_config

# Import the centralized strict system prompt
from prompts import FIRST_RESPONDER_PROMPT

# Import the main functions of other agents
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
chat_memory = [SystemMessage(content=FIRST_RESPONDER_PROMPT)]

# [ADDITION]: Custom Tool to intercept LLM decision
@tool
def trigger_band_escalation(emergency_type: str, location: str) -> str:
    """
    CRITICAL TOOL: Use this tool IMMEDIATELY when you have complete information about 
    emergency type (emergency_type) and location (location) from the caller.
    """
    logger.info("\n" + "="*50)
    logger.warning("🚨 [BEHIND THE SCENES] AI IS COLLABORATING! 🚨")
    logger.warning(f"EMERGENCY DETAILS: {emergency_type}")
    logger.warning(f"LOCATION SET: {location}")
    logger.warning("SYSTEM: Sending instructions to Band Platform to create Room...")
    logger.info("="*50 + "\n")
    
    # HACKATHON NOTE: This is where you need to place Band SDK code (if available) 
    # to create the room automatically. Example: band_client.create_room(...)
    
    return "SUCCESS: Initial report has been sent to Agent Manager."

llm = ChatOpenAI(
    model="deepseek-chat",
    api_key=os.getenv("OPENAI_API_KEY"),
    base_url=os.getenv("OPENAI_BASE_URL"),
    temperature=0.0 
)

# Bind this tool to LLM so it can use it
llm_with_tools = llm.bind_tools([trigger_band_escalation])

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
    # Start all other agents concurrently
    logger.info("Connecting First Responder system and all agents to the Band platform...")
    
    agent_tasks = [
        asyncio.create_task(band_agent.run()),
        asyncio.create_task(dispatcher_main()),
        asyncio.create_task(geo_main()),
        asyncio.create_task(manager_main()),
        asyncio.create_task(medical_main()),
        asyncio.create_task(triage_main())
    ]
    
    yield
    
    # Stop all Band Agents safely when FastAPI 'shutdown'
    for task in agent_tasks:
        task.cancel()

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
        raise HTTPException(status_code=400, detail="Message cannot be empty")
        
    fake_keywords = ["main-main", "test", "testing", "prank", "gurau"]
    if any(keyword in user_text.lower() for keyword in fake_keywords):
        logger.warning("WARNING: Fake call detected and blocked.")
        return {
            "status": "TERMINATE_CALL",
            "reply": "Call terminated immediately as the system detected a fake call attempt."
        }

    try:        
        # [UPDATE]: Add user input using HumanMessage
        chat_memory.append(HumanMessage(content=user_text))
        
        logger.info("Waiting for First Responder analysis...")
        response = await llm_with_tools.ainvoke(chat_memory)
        
        # [ADDITION CRITICAL]: Save the original AI response object to memory to preserve tool_calls metadata
        chat_memory.append(response)
        
        if response.tool_calls:
            for tool_call in response.tool_calls:
                if tool_call["name"] == "trigger_band_escalation":
                    args = tool_call["args"]
                    tool_output = trigger_band_escalation.invoke(args)
                    
                    # [MANDATORY UPDATE]: Provide ToolMessage matched with tool call id
                    chat_memory.append(ToolMessage(content=tool_output, tool_call_id=tool_call["id"]))
                    
                    ai_reply = "Please wait on the line, I am coordinating emergency assistance with the logistics team."
                    # Save follow-up text response as AIMessage
                    chat_memory.append(AIMessage(content=ai_reply))
                    
                    return {
                        "status": "ACTIVE",
                        "reply": ai_reply
                    }

        # If just regular conversation without agent operation trigger
        raw_reply = response.content or ""
        clean_reply = re.sub(r'[*#_]', '', raw_reply) 
        
        return {
            "status": "ACTIVE",
            "reply": clean_reply
        }
        
    except Exception as e:
        logger.error(f"API Processing Error: {str(e)}")

# ==========================================
# 4. SERVER LAUNCH
# ==========================================
if __name__ == "__main__":
    try:
        uvicorn.run(app, host="127.0.0.1", port=8000)
    except KeyboardInterrupt:
        logger.info("First Responder system stopped by user.")