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
from langgraph.checkpoint.memory import InMemorySaver
from langchain_core.messages import SystemMessage, HumanMessage
from langgraph.prebuilt import create_react_agent
from langchain_core.tools import tool

from thenvoi import Agent
from thenvoi.adapters import LangGraphAdapter
from thenvoi.config import load_agent_config

from prompts import FIRST_RESPONDER_PROMPT
from dispatcher_agent import main as dispatcher_main
from geo_agent import main as geo_main
from manager_agent import main as manager_main
from medical_agent import main as medical_main
from triage_agent import main as triage_main

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()

# ==========================================
# 1. BAND ADAPTER (WEBSOCKET BRIDGE)
# ==========================================
agent_id, api_key = load_agent_config("first_responder")

band_llm = ChatOpenAI(
    model="deepseek/deepseek-chat",
    api_key=os.getenv("OPENAI_API_KEY"),
    base_url=os.getenv("OPENAI_BASE_URL"),
    temperature=0.0 
)

# This adapter no longer serves callers, but acts as an "invisible hand"
# to execute Band tools from within the server.
adapter = LangGraphAdapter(
    llm=band_llm,
    checkpointer=InMemorySaver(),
    custom_section="SYSTEM: You are the backend bridge to the Band platform. You must execute Band tools strictly when instructed by the local system."
)

band_agent = Agent.create(adapter=adapter, agent_id=agent_id, api_key=api_key)

# ==========================================
# 2. PHASE 3: PWA ENGINE (FAST & 401-FREE)
# ==========================================
# This custom tool allows the local caller agent (Local PWA Agent) 
# to send official reports into the Band ecosystem.
@tool
async def dispatch_to_band_network(emergency_summary: str) -> str:
    """CRITICAL: Use this tool ONLY when you have obtained the emergency type and location. Send that information to the Band Network (@agent_manager)."""
    logger.info("PHASE 3: Extracting report and sending to the Band platform...")
    
    try:
        # Programmatically extracting the LangGraph graph compiled by the Band SDK
        band_graph = getattr(adapter, 'app', getattr(adapter, 'graph', None))
        if band_graph:
            # Forcing WebSocket execution to communicate with the manager agent
            await band_graph.ainvoke({
                "messages": [HumanMessage(content=f"SYSTEM OVERRIDE: Use the 'thenvoi_send_message' tool now to send this information to @agent_manager : {emergency_summary}")]
            }, config={"configurable": {"thread_id": "cekap_bridge_thread"}})
            return "SUCCESS: Report successfully channeled to the Band rescue team."
    except Exception as e:
        logger.error(f"Band Bridge Error: {e}")
        return "FAILED: Failed to connect with the Band Network infrastructure."

# Local agent specifically for PWA smoothness
pwa_llm = ChatOpenAI(
    model="deepseek/deepseek-chat",
    api_key=os.getenv("OPENAI_API_KEY"),
    base_url=os.getenv("OPENAI_BASE_URL"),
    temperature=0.0
)
pwa_agent = create_react_agent(pwa_llm, tools=[dispatch_to_band_network])
chat_memory = [SystemMessage(content=FIRST_RESPONDER_PROMPT)]

# ==========================================
# 3. PHASE 1 & 2: AUTOMATIC PROGRAMMATIC SETUP
# ==========================================
async def setup_band_infrastructure():
    # Waiting for Band WebSocket authentication to inject tools (5 seconds)
    await asyncio.sleep(5) 
    logger.info("PHASE 1 & 2: Building Band Room Infrastructure Programmatically...")
    
    try:
        band_graph = getattr(adapter, 'app', getattr(adapter, 'graph', None))
        if band_graph:
            instruction = """
            SYSTEM OVERRIDE: 
            Execute this programmatic instruction immediately without question:
            1. Use the 'thenvoi_create_chatroom' tool to create a new room named "CEKAP Operation Center".
            2. After the room is created, use the 'thenvoi_add_participant' tool to add the following agents: @agent_manager, @triage_diagnoser, @geo_specialist, @medical_agent, @dispatcher.
            """
            await band_graph.ainvoke({
                "messages": [HumanMessage(content=instruction)]
            }, config={"configurable": {"thread_id": "cekap_setup_thread"}})
            logger.info("PHASE 1 & 2 Complete! Agent infrastructure is ready to operate.")
    except Exception as e:
        logger.error(f"Programmatic Setup Execution Error: {e}")

# ==========================================
# 4. FASTAPI SERVER & BACKGROUND MANAGEMENT
# ==========================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("CEKAP Engine starting operations...")
    agent_tasks = [
        asyncio.create_task(band_agent.run()),
        asyncio.create_task(dispatcher_main()),
        asyncio.create_task(geo_main()),
        asyncio.create_task(manager_main()),
        asyncio.create_task(medical_main()),
        asyncio.create_task(triage_main())
    ]
    
    # Automatically starting Phase 1 & 2 processes
    setup_task = asyncio.create_task(setup_band_infrastructure())
    
    yield  
    
    setup_task.cancel()
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
    global chat_memory
    user_text = request.message.strip()
    
    if not user_text:
        raise HTTPException(status_code=400, detail="Empty message.")

    try:
        # Serving the Caller quickly via the Local Engine
        chat_memory.append(HumanMessage(content=f"[Caller]: {user_text}"))
        
        response = await pwa_agent.ainvoke({
            "messages": chat_memory
        })
        
        chat_memory = response["messages"]
        final_ai_msg = chat_memory[-1].content
        
        # Filtering the response to comply with the mature PWA UI design
        reply_to_pwa = []
        for line in final_ai_msg.split('\n'):
            line = line.strip()
            if line.lower().startswith('@caller'):
                clean_line = re.sub(r'^@caller[:,\s]*', '', line, flags=re.IGNORECASE).strip()
                clean_line = re.sub(r'[*#_]', '', clean_line)
                reply_to_pwa.append(clean_line)
        
        final_reply = " ".join(reply_to_pwa).strip()
        
        # Protection logic (Fake Call)
        if "TERMINATE" in final_reply.upper() or "TERMINATE_CALL" in final_reply.upper():
            return {"status": "TERMINATE_CALL", "reply": "Call forcefully terminated due to abuse of the emergency line."}
            
        # Automatic notification when the agent is making a tool call to Band
        if not final_reply:
            return {
                "status": "ACTIVE",
                "reply": "The system is verifying coordinates and coordinating rescue units. Please stay on the line..."
            }
            
        return {"status": "ACTIVE", "reply": final_reply}

    except Exception as e:
        logger.error(f"Phase 3 Logic Failure: {str(e)}")
        return {"status": "ERROR", "reply": "The system is coordinating technical files."}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=10000)