# 🚨 CEKAP: Centralized Emergency Knitted Assistance Platform

![CEKAP Banner](https://img.shields.io/badge/Band_of_Agents_Hackathon-Track_3_Winner_Candidate-00ff88?style=for-the-badge&logo=hackaday)
![Python](https://img.shields.io/badge/Python-3776AB?style=flat&logo=python&logoColor=white)
![AI/ML API](https://img.shields.io/badge/AI/ML_API-Powered-blueviolet?style=flat)
![Band](https://img.shields.io/badge/Band-Multi--Agent_Collab-black?style=flat)
![Supabase](https://img.shields.io/badge/Supabase-Database-3ECF8E?style=flat&logo=supabase&logoColor=white)

**CEKAP** is an AI-powered, multi-agent emergency response system designed to eliminate dispatch latency, overcome human dispatcher fatigue, and seamlessly coordinate cross-departmental rescue efforts. Built specifically for Track 3 (Regulated & High-Stakes Workflows) of the **Band of Agents Hackathon (June 2026)**.

---

## ⚠️ The Problem
During high-volume crises, traditional human-operated emergency hotlines experience systemic collapse. Callers in panic struggle with IVR menus, local dialects cause miscommunications, and manual handoffs between police, fire, and medical departments consume critical minutes. **In emergency response, every delayed second costs a human life.**

## 💡 The Solution
CEKAP replaces serial human workflows with a **parallel, self-coordinating multi-agent enterprise system**. Built as an accessible Progressive Web App (PWA) with a mature, professional glassmorphism interface, it requires no app downloads. Callers use universal voice interaction to report emergencies, which are instantly processed, triaged, and dispatched to rescue agencies via Telegram.

---

## 🧠 6-Agent Collaboration Architecture (Band-Powered)
To ensure maximum efficiency and reliability, CEKAP utilizes a robust **6-agent ecosystem**. These agents collaborate seamlessly in a shared Band room, passing context and coordinating actions in real-time.

1. **🎛️ Manager Agent (Central Coordinator & KPI Reviewer)**
   - The "nervous system" and middleman of the entire operation. It receives the initial transcript and dictates the entire workflow.
   - Assigns specific tasks to specialized agents using Band's `@mention` routing.
   - Ensures all KPIs and data points (location, severity) are met before approving the final dispatch.

2. **🎧 First Responder (Frontline Interface)**
   - Operates directly with the PWA frontend. Uses native Web Speech API to transcribe caller voice (Malay/Manglish/English) with zero latency.
   - **Smart Filtering:** Detects fake calls or pranks and terminates them immediately to save system resources and prevent false dispatches.
   - Comforts the caller and extracts the initial `Emergency Type` and `Location` to pass to the Manager.

3. **🏥 Medical Agent (On-Demand First Aid)**
   - A specialized agent activated *only* if the Manager Agent detects physical harm (e.g., drowning, choking, cardiac arrest).
   - Dynamically generates 3 critical survival/first-aid steps that are relayed back to the caller in real-time while waiting for the dispatch to complete.

4. **📋 Triage Diagnoser**
   - Receives context from the Manager to perform high-reasoning analysis and classify the emergency severity (Priority 1-4).
   - Extracts structured JSON and logs it directly into the Supabase audit database.

5. **📍 Geo Specialist**
   - Receives the raw location string from the Manager.
   - Converts conversational location descriptions into precise Latitude/Longitude coordinates for the rescue team.

6. **🚀 Dispatcher**
   - Waits for the Manager's final dispatch order.
   - Compiles a complete emergency report with a shareable Google Maps link.
   - Fires a webhook directly to the rescue agency's Telegram group and cleanly retires the active Band session.

---

## 🛠️ Technology Stack
* **AI Gateway:** AI/ML API (Accessing `deepseek-chat` and `gemini-2.5-flash`)
* **Agent Orchestration:** Band SDK & LangGraph
* **Backend:** FastAPI / Python
* **Frontend UI:** HTML/CSS/JS PWA
* **Database / Audit Trail:** Supabase (PostgreSQL)
* **Dispatch System:** Telegram Bot API
* **Speech Processing:** Web Speech API (Native Browser STT/TTS)

---

## 🖥️ System Interfaces
1. **Caller PWA (Mobile Web App):** A zero-friction web interface mimicking a natural phone call. Designed with a realistic, premium dark-mode glassmorphism UI to maintain a calm, authoritative presence during panics.
2. **Command Center (Admin Dashboard):** A real-time Supabase-synced web dashboard for operators to monitor active rooms, triage statuses, and structured emergency logs.
3. **Agency Telegram Bot:** Direct push notifications to on-the-ground responders featuring severity priority, incident details, and one-click Google Maps navigation.

---

## 👥 The Team
Built with precision and passion for the Band of Agents Hackathon 2026 by **Kolej Tualang**:
* **Azfar Ahir** (@sigorolos)
* **Ameer Yusoff** (@meerysff)
* **Haziq Dawam** (@ziqh_11)

> *"Every Second Counts. Every Voice Matters."*
