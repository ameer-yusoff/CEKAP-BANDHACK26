# main_agents.py
import asyncio
import logging
from first_responder import app as fastapi_app
import uvicorn

# Import main functions from other agents
from dispatcher_agent import main as dispatcher_main
from geo_agent import main as geo_main
from manager_agent import main as manager_main
from medical_agent import main as medical_main
from triage_agent import main as triage_main

async def start_system():
    # Start all Band agents as independent background tasks
    # Each agent runs in its own task to prevent blocking
    asyncio.create_task(dispatcher_main())
    asyncio.create_task(geo_main())
    asyncio.create_task(manager_main())
    asyncio.create_task(medical_main())
    asyncio.create_task(triage_main())
    
    # Configure and run FastAPI server in the same event loop
    config = uvicorn.Config("first_responder:app", host="0.0.0.0", port=8000)
    server = uvicorn.Server(config)
    await server.serve()

if __name__ == "__main__":
    asyncio.run(start_system())