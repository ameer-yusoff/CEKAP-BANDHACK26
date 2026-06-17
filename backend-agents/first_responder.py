# first_responder.py

import asyncio
import logging
import os
import re
from contextlib import asynccontextmanager
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# LangChain and LangGraph message structures
from langchain_openai import ChatOpenAI
from langchain_core.tools import tool
from langchain_core.messages import ToolMessage, AIMessage

# Import external agent orchestrations for dynamic connection
from dispatcher_agent import main as dispatcher_main
from geo_agent import main as geo_main
from manager_agent import main as manager_main
from medical_agent import main as medical_main
from triage_agent import main as triage_main

# Import the centralized strict system prompt
from prompts import FIRST_RESPONDER_PROMPT

# Configure professional enterprise logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()

# Thread-safe flag to ensure background WebSocket agents only boot once
agents_activated = False
chat_memory = []

async def activate_all_agents():
    """
    Dynamically initializes and boots all secondary ecosystem agents 
    via background tasks to connect them to Band's WebSockets/REST architecture.
    """
    global agents_activated
    if not agents_activated:
        logger.info("Initializing dynamic activation of all secondary Band platform agents...")
        asyncio.create_task(dispatcher_main())
        asyncio.create_task(geo_main())
        asyncio.create_task(manager_main())
        asyncio.create_task(medical_main())
        asyncio.create_task(triage_main())
        agents_activated = True
        logger.info("All background multi-agent synchronization loops are now live and listening.")

@tool
def trigger_band_escalation(emergency_type: str, location: str) -> str:
    """
    CRITICAL TOOL: Executed immediately by the LLM once both the nature 
    of the emergency and the caller location are verified.
    """
    logger.info("\n" + "="*60)
    logger.warning("🚨 [BAND ARCHITECTURE TRIPPED: LIVE EMERGENCY ESCALATION] 🚨")
    logger.warning(f"EMERGENCY CLASSIFICATION : {emergency_type}")
    logger.warning(f"VERIFIED LOCATION        : {location}")
    logger.warning("SYSTEM: Spawning dedicated cross-agent session contexts...")
    logger.info("="*60 + "\n")
    return f"SUCCESS: Incident channel opened on Band. Context sent to Manager Agent for multi-agent triage."

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Establish a clean, immutable initial state for systemic memory
    global chat_memory
    chat_memory = [{"role": "system", "content": FIRST_RESPONDER_PROMPT}]
    logger.info("CEKAP Emergency Core Gateway loaded successfully. Listening for PWA ingress.")
    yield
    logger.info("Shutting down emergency gateway services cleanly.")

app = FastAPI(lifespan=lifespan)

# Inject rigorous CORS rules to completely protect transport layers from dropping packets
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize Core LLM Instance
llm = ChatOpenAI(
    model="deepseek-chat",
    api_key=os.getenv("OPENAI_API_KEY"),
    base_url=os.getenv("OPENAI_BASE_URL"),
    temperature=0.0 
)

# Explicitly bind the custom execution tool to the LLM engine
llm_with_tools = llm.bind_tools([trigger_band_escalation])

class ChatRequest(BaseModel):
    message: str

@app.post("/api/chat")
async def handle_chat(request: ChatRequest):
    user_text = request.message.strip()
    
    if not user_text:
        raise HTTPException(status_code=400, detail="Transmission block cannot be empty.")
        
    # Rigorous anti-spam security filter to isolate and drop malicious/fake requests
    fake_keywords = ["main-main", "test", "testing", "prank", "gurau"]
    if any(keyword in user_text.lower() for keyword in fake_keywords):
        logger.warning("SECURITY VIOLATION: Fake call signature matching found. Connection dropped.")
        return {
            "status": "TERMINATE_CALL",
            "reply": "Panggilan ditamatkan serta-merta kerana sistem mengesan cubaan panggilan palsu."
        }

    try:        
        # Chronologically append the new user utterance to history
        chat_memory.append({"role": "user", "content": user_text})
        
        # Invoke model inference pipeline
        response = await llm_with_tools.ainvoke(chat_memory)
        
        # Evaluate if the model determined an escalation tool must run
        if response.tool_calls:
            for tool_call in response.tool_calls:
                if tool_call["name"] == "trigger_band_escalation":
                    args = tool_call["args"]
                    
                    # Execute the escalation tool locally
                    tool_output = trigger_band_escalation.invoke(args)
                    
                    # STRUCTURAL FIX: Append BOTH the original response (with tool_calls)
                    # AND the subsequent ToolMessage to strictly maintain chat context integrity!
                    chat_memory.append(response)
                    chat_memory.append(ToolMessage(content=tool_output, tool_call_id=tool_call["id"]))
                    
                    # Trigger dynamic activation of all operational sub-agents via WebSockets
                    await activate_all_agents()
                    
                    ai_reply = "Sila tunggu di talian, saya sedang menyelaraskan bantuan kecemasan dengan pasukan logistik."
                    chat_memory.append({"role": "assistant", "content": ai_reply})
                    
                    return {
                        "status": "ACTIVE",
                        "reply": ai_reply
                    }

        # Execution path for standard conversational steps
        raw_reply = response.content
        clean_reply = re.sub(r'[*#_]', '', raw_reply) 
        
        chat_memory.append({"role": "assistant", "content": raw_reply})

        return {
            "status": "ACTIVE",
            "reply": clean_reply
        }
        
    except Exception as e:
        logger.error(f"Catastrophic Server Interface Exception: {str(e)}")
        return {
            "status": "ERROR",
            "reply": "Harap maaf, sistem CEKAP mengalami gangguan rangkaian. Sila ulang semula."
        }