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

# Import prompts & other agents
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

# ==========================================
# 2. CUSTOM TOOLS & MEMORY
# ==========================================
@tool
def trigger_band_escalation(emergency_type: str, location: str) -> str:
    """
    CRITICAL TOOL: Supplementary logging tool.
    """
    logger.info("\n" + "="*50)
    logger.warning("🚨 [BEHIND THE SCENES] AI IS COLLABORATING! 🚨")
    logger.warning(f"EMERGENCY DETAILS: {emergency_type}")
    logger.warning(f"LOCATION SET: {location}")
    logger.info("="*50 + "\n")
    return "SUCCESS: Log recorded. Please make sure you are also using the thenvoi_create_chatroom tool now to contact Agent_Manager."

# Lock system prompt in memory using official LangChain structure
chat_memory = [SystemMessage(content=FIRST_RESPONDER_PROMPT)]

# ==========================================
# 3. FASTAPI SERVER SETUP
# ==========================================
band_agent = Agent.create(adapter=adapter, agent_id=agent_id, api_key=api_key)

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Connecting all CEKAP agents to Band platform simultaneously...")
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
        chat_memory.append(HumanMessage(content=user_text))
        logger.info("Waiting for First Responder analysis...")
        
        # [CRITICAL UPDATE]: Extract official Band tools from adapter dynamically
        band_tools = getattr(adapter, 'tools', getattr(adapter, '_tools', []))
        all_tools = band_tools + [trigger_band_escalation]
        
        # Bind all tools so LLM has the ability to communicate to Band server
        local_llm_with_tools = llm.bind_tools(all_tools)
        
        response = await local_llm_with_tools.ainvoke(chat_memory)
        chat_memory.append(response)
        
        if response.tool_calls:
            for tool_call in response.tool_calls:
                tool_name = tool_call["name"]
                args = tool_call["args"]
                logger.info(f"Executing Action: {tool_name}")
                
                executed = False
                for t in all_tools:
                    t_name = getattr(t, 'name', getattr(t, '__name__', None))
                    if t_name == tool_name:
                        try:
                            # Execute tool to communicate in real-time with other agents
                            if asyncio.iscoroutinefunction(t.invoke):
                                tool_output = await t.invoke(args)
                            else:
                                tool_output = t.invoke(args)
                            chat_memory.append(ToolMessage(content=str(tool_output), tool_call_id=tool_call["id"]))
                            executed = True
                        except Exception as e:
                            logger.error(f"Error executing tool {t_name}: {e}")
                            chat_memory.append(ToolMessage(content="Action failed.", tool_call_id=tool_call["id"]))
                            executed = True
                        break
                        
                if not executed:
                    chat_memory.append(ToolMessage(content="Action completed.", tool_call_id=tool_call["id"]))

            # [CRITICAL UPDATE]: Let LLM generate natural sentences based on current state
            # after opening chat room. No more static/hardcoded responses.
            final_response = await local_llm_with_tools.ainvoke(chat_memory)
            chat_memory.append(final_response)
            
            clean_reply = re.sub(r'[*#_]', '', final_response.content or "")
            return {
                "status": "ACTIVE",
                "reply": clean_reply
            }

        raw_reply = response.content or ""
        clean_reply = re.sub(r'[*#_]', '', raw_reply) 
        
        return {
            "status": "ACTIVE",
            "reply": clean_reply
        }
        
    except Exception as e:
        logger.error(f"API Processing Error: {str(e)}")
        return {
            "status": "ERROR",
            "reply": "Sorry, CEKAP system is experiencing network disruption. Please try again."
        }