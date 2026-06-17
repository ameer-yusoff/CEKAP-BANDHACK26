# first_responder.py

import asyncio
import logging
import os
import re
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

# LangChain and Tool imports
from langchain_openai import ChatOpenAI
from langchain_core.tools import tool

# Band platform HTTP/REST API Simulation/Client utilities
import requests

# Configure logging to console in English
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("CEKAP_FirstResponder")

load_dotenv()

# ==========================================
# 1. GLOBAL SETTINGS & CONFIGURATIONS
# ==========================================
# The designated active Room Session provided by the user
TARGET_ROOM_ID = "c8c29c5a-dd66-4e7f-91c9-a9a12a353933"
BAND_API_URL = "https://api.band.ai/v1" # Target Band Platform endpoint base URL

# Centralized storage for multi-turn user conversation context to prevent memory crash
session_history = {}

# Import prompt internally to ensure code clarity
from prompts import FIRST_RESPONDER_PROMPT

# Initialize the LLM Engine using the AI/ML API credentials
llm = ChatOpenAI(
    model="deepseek-chat",
    api_key=os.getenv("OPENAI_API_KEY"),
    base_url=os.getenv("OPENAI_BASE_URL"),
    temperature=0.0
)

# ==========================================
# 2. DEFINING CORE BAND PLATFORM INTERACTIONS
# ==========================================

@tool
def automatic_band_escalation(emergency_type: str, location: str) -> str:
    """
    CRITICAL TOOL: Automatically hooks the session, sends alerts into the Band Chat Room,
    and brings the Agent swarm (Manager, Triage, Geo, Dispatcher) into full coordination.
    """
    logger.info(f"[BAND SYSTEM] Triggering automatic escalation for {emergency_type} at {location}")
    
    # Payload alignment matching the Band architecture requirements
    headers = {
        "Authorization": f"Bearer {os.getenv('OPENAI_API_KEY')}",
        "Content-Type": "application/json"
    }
    
    # 1. Invite core agents into the active room session
    agents_to_invite = ["agent_manager", "triage_diagnoser", "geo_specialist", "dispatcher"]
    for agent in agents_to_invite:
        invite_payload = {"participant_id": agent, "role": "agent"}
        try:
            # REST API call to add participants to the specified room session
            requests.post(
                f"{BAND_API_URL}/rooms/{TARGET_ROOM_ID}/participants", 
                json=invite_payload, 
                headers=headers,
                timeout=5
            )
            logger.info(f"[BAND SYSTEM] Successfully added @{agent} to room {TARGET_ROOM_ID}")
        except Exception as e:
            logger.error(f"[BAND SYSTEM] Non-blocking failure adding agent {agent}: {str(e)}")

    # 2. Broadcast initial structural report to the room for the Manager Agent to intercept
    broadcast_msg = {
        "text": f"@Agent_Manager EMERGENCY CRITICAL ALERT! Type: {emergency_type}. Location: {location}. Initiating smart dispatch routing."
    }
    
    try:
        requests.post(
            f"{BAND_API_URL}/rooms/{TARGET_ROOM_ID}/messages", 
            json=broadcast_msg, 
            headers=headers,
            timeout=5
        )
        logger.info("[BAND SYSTEM] Dispatched structural alert to Agent Swarm session room.")
    except Exception as e:
        logger.error(f"[BAND SYSTEM] Failed to broadcast to session room: {str(e)}")

    return "SUCCESS: Swarm activated. Emergency room session linked and operating via WebSockets/REST."

# Bind the operational coordination tools directly to the LLM
llm_with_tools = llm.bind_tools([automatic_band_escalation])

# ==========================================
# 3. FASTAPI BACKEND APP CONFIGURATION
# ==========================================

class ChatRequest(BaseModel):
    message: str

@asynccontextmanager
async def lifespan(app: FastAPI):
    # This keeps the environment loop robustly running without crashing
    logger.info("[SYSTEM INITIALIZATION] CEKAP First Responder is online and continuously listening...")
    yield
    logger.info("[SYSTEM SHUTDOWN] First Responder going offline cleanly.")

app = FastAPI(lifespan=lifespan)

# Allow Cross-Origin Resource Sharing for secure PWA connections
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==========================================
# 4. REST API CHAT ENDPOINT LOGIC
# ==========================================

@app.post("/api/chat")
async def handle_chat(request: ChatRequest):
    user_text = request.message.strip()
    
    if not user_text:
        raise HTTPException(status_code=400, detail="Input message cannot be empty")
        
    # Anti-Spam / Fake Call Guardrails (Instant Termination Mechanism)
    fake_keywords = ["main-main", "test", "testing", "prank", "gurau"]
    if any(keyword in user_text.lower() for keyword in fake_keywords):
        logger.warning("[SECURITY COMPLIANCE] Malicious or fake interaction intercepted. Call terminated.")
        return {
            "status": "TERMINATE_CALL",
            "reply": "Call terminated immediately as the system detected a fake call attempt."
        }

    # Isolated session memory layout initialization to prevent state contamination/crashes
    session_id = "default_caller" 
    if session_id not in session_history:
        session_history[session_id] = [{"role": "system", "content": FIRST_RESPONDER_PROMPT}]
        
    # Append newest raw caller transcript to the memory queue
    session_history[session_id].append({"role": "user", "content": user_text})

    try:        
        logger.info(f"[PROCESS] Processing text stream via AI Engine: '{user_text}'")
        
        # Invoke the AI framework equipped with the Band coordination tools
        response = await llm_with_tools.ainvoke(session_history[session_id])
        
        # Intercept tool calling execution structures natively
        if response.tool_calls:
            for tool_call in response.tool_calls:
                if tool_call["name"] == "automatic_band_escalation":
                    args = tool_call["args"]
                    # Execute the room bridging tool logic
                    automatic_band_escalation.invoke(args)
                    
                    hold_reply = "Please stay calm, your information has been verified. Please wait on the line while I coordinate emergency assistance to your location."
                    session_history[session_id].append({"role": "assistant", "content": hold_reply})
                    
                    return {
                        "status": "ACTIVE",
                        "reply": hold_reply
                    }

        # Regular textual multi-turn communication handling
        raw_reply = response.content
        # Strip markdown syntax for optimal TTS voice rendering
        
        # Append response to memory tracker to keep the engine conversational
        session_history[session_id].append({"role": "assistant", "content": raw_reply})

        return {
            "status": "ACTIVE",
            "reply": clean_reply
        }
        
    except Exception as e:
        logger.error(f"[CRITICAL ERROR] Exception captured during execution cycle: {str(e)}")
        # Graceful error payload back to PWA without dropping the backend pipeline server connection
        return {
            "status": "ERROR",
            "reply": "The line is experiencing temporary disruption. Please state your emergency details again."
        }