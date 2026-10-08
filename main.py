import json
import os
import time
from typing import Dict, List, Optional
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from openai import OpenAI
from tuya_iot import TuyaOpenAPI
from apscheduler.schedulers.background import BackgroundScheduler
from contextlib import asynccontextmanager

# ==========================================
# КОНФІГУРАЦІЯ
# ==========================================
TUYA_ENDPOINT = "https://openapi.tuyaeu.com"  # Для Європи
TUYA_ACCESS_ID = os.getenv("TUYA_ACCESS_ID", "")
TUYA_ACCESS_KEY = os.getenv("TUYA_ACCESS_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

tuya = TuyaOpenAPI(TUYA_ENDPOINT, TUYA_ACCESS_ID, TUYA_ACCESS_KEY)

openai_client = OpenAI(api_key=OPENAI_API_KEY)

# База пристроїв, групування та сценаріїв
DEVICES_DB = {
    "dev_1": {"name": "Світло в вітальні", "type": "light", "room": "Вітальня", "tuya_id": "eb1234567890", "code": "switch_led"},
    "dev_2": {"name": "Кондиціонер", "type": "climate", "room": "Вітальня", "tuya_id": "eb0987654321", "code": "switch"},
    "dev_3": {"name": "Розетка", "type": "switch", "room": "Кухня", "tuya_id": "eb1122334455", "code": "switch_1"}
}

SCENARIOS_DB = {
    "вечір": [{"dev_id": "dev_1", "state": True}],
    "ніч": [{"dev_id": "dev_1", "state": False}]
}

SCHEDULED_TASKS = []

def control_device(device_name: str, state: bool):
    target = None
    for d_id, d in DEVICES_DB.items():
        if d["name"].lower() == device_name.lower():
            target = d
            break

    if not target:
        return f"Пристрій '{device_name}' не знайдено."

    commands = [{"code": target["code"], "value": state}]
    res = tuya.post(f"/v1.0/smart/devices/{target['tuya_id']}/commands", {"commands": commands})
    if res.get("success"):
        st = "увімкнено" if state else "вимкнено"
        return f"{target['name']} {st}."
    return f"Помилка Tuya: {res.get('msg')}"

def run_scenario(scenario_name: str):
    sc = SCENARIOS_DB.get(scenario_name.lower())
    if not sc:
        return f"Сценарій '{scenario_name}' не знайдено."
    for item in sc:
        d = DEVICES_DB.get(item["dev_id"])
        if d:
            control_device(d["name"], item["state"])
    return f"Сценарій '{scenario_name}' успішно виконано."

def check_and_run_schedule():
    current_time = time.strftime("%H:%M")
    for task in SCHEDULED_TASKS:
        if task["time"] == current_time:
            if task["action"] == "scenario":
                run_scenario(task["name"])
            elif task["action"] == "device":
                control_device(task["device_name"], task["state"])

scheduler = BackgroundScheduler()

@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler.add_job(check_and_run_schedule, 'cron', minute='*')
    scheduler.start()
    yield
    scheduler.shutdown()

app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

tools = [
    {
        "type": "function",
        "function": {
            "name": "control_device",
            "description": "Керує фізичним пристроєм Tuya (увімкнути/вимкнути)",
            "parameters": {
                "type": "object",
                "properties": {
                    "device_name": {"type": "string", "description": "Назва пристрою"},
                    "state": {"type": "boolean", "description": "True = увімкнути, False = вимкнути"}
                },
                "required": ["device_name", "state"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "run_scenario",
            "description": "Запускає збережений сценарій (наприклад 'вечір', 'ніч')",
            "parameters": {
                "type": "object",
                "properties": {
                    "scenario_name": {"type": "string", "description": "Назва сценарію"}
                },
                "required": ["scenario_name"]
            }
        }
    }
]

class VoicePayload(BaseModel):
    text: str

@app.post("/api/voice")
async def voice_endpoint(payload: VoicePayload):
    messages = [
        {"role": "system", "content": "Ти помічник 'Мій дім'. Відповідай дуже стисло (1 речення)."},
        {"role": "user", "content": payload.text}
    ]

    res = openai_client.chat.completions.create(
        model="gpt-4o",
        messages=messages,
        tools=tools,
        tool_choice="auto"
    )

    msg = res.choices[0].message
    if msg.tool_calls:
        for tool in msg.tool_calls:
            args = json.loads(tool.function.arguments)
            if tool.function.name == "control_device":
                r = control_device(args.get("device_name"), args.get("state"))
                return {"response": r}
            elif tool.function.name == "run_scenario":
                r = run_scenario(args.get("scenario_name"))
                return {"response": r}

    return {"response": msg.content}

@app.get("/api/devices")
async def get_devices():
    return DEVICES_DB

@app.get("/api/scenarios")
async def get_scenarios():
    return list(SCENARIOS_DB.keys())

@app.get("/")
async def root():
    return FileResponse("index.html")

@app.get("/manifest.json")
async def manifest():
    return FileResponse("manifest.json")
  
