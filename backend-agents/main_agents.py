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

async def run_all_band_agents():
    # Run all Band agents in parallel in the background
    await asyncio.gather(
        dispatcher_main(),
        geo_main(),
        manager_main(),
        medical_main(),
        triage_main()
    )

async def start_system():
    # Start all other Band agents in the background
    asyncio.create_task(run_all_band_agents())
    
    # Configure and run FastAPI server in the same event loop
    config = uvicorn.Config("first_responder:app", host="0.0.0.0", port=8000)
    server = uvicorn.Server(config)
    await server.serve()

if __name__ == "__main__":
    asyncio.run(start_system())