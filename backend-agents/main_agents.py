# main_agents.py
import asyncio
import logging
from first_responder import app as fastapi_app
import uvicorn

# Import fungsi main dari agen-agen lain
from dispatcher_agent import main as dispatcher_main
from geo_agent import main as geo_main
from manager_agent import main as manager_main
from medical_agent import main as medical_main
from triage_agent import main as triage_main

async def run_all_band_agents():
    # Jalankan semua agen Band secara paralel di latar belakang
    await asyncio.gather(
        dispatcher_main(),
        geo_main(),
        manager_main(),
        medical_main(),
        triage_main()
    )

if __name__ == "__main__":
    # Jalankan server FastAPI (yang juga memuat First Responder via lifespan)
    # Gunakan loop yang sama untuk agen lainnya jika diperlukan, atau Render Web Service akan menjalankan ini.
    uvicorn.run("first_responder:app", host="0.0.0.0", port=8000)