import math
import time
import random
import json
from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi import WebSocket, WebSocketDisconnect
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from . import models, database

# Membuat tabel otomatis di PostgreSQL saat server nyala
models.Base.metadata.create_all(bind=database.engine)
models.seed_data()

app = FastAPI(title="nAngkrIng API")

# Tambahkan baris ini tepat di bawah app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # Di produksi nanti, ini harus spesifik
    allow_methods=["*"],
    allow_headers=["*"],
)

# Simpan daftar koneksi yang aktif
class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def broadcast(self, message: str):
        for connection in self.active_connections[:]: # Gunakan slice [:] untuk menghindari error saat remove
            try:
                await connection.send_text(message)
            except Exception:
                self.active_connections.remove(connection)

manager = ConnectionManager()

# Dependency untuk koneksi database
def get_db():
    db = database.SessionLocal()
    try:
        yield db
    finally:
        db.close()

# --- FAKE AI SERVICE ---
def fake_ai_detector():
    # Simulasi berbagai plat nomor agar tidak perlu edit file terus
    plates = ["B 1234 XYZ", "B 5678 ABC", "F 1963 TRPL", "D 9999 BOSS"]
    time.sleep(1.0)
    return random.choice(plates)  # Simulasi nomor plat yang terdeteksi

def calculate_fee(entry_time, exit_time, rate: models.ParkingRate):
    duration = exit_time - entry_time
    total_seconds = duration.total_seconds()
    
    # Konversi ke jam (dibulatkan ke atas)
    hours = math.ceil(total_seconds / 3600)
    days = duration.days

    # Logika Menginap (Overnight)
    if days >= 1:
        return days * rate.overnight_rate
    
    # Logika Per Jam
    fee = rate.first_hour_rate + (max(0, hours - 1) * rate.additional_hour_rate)
    
    # Batasi dengan harga maksimal harian
    return min(fee, rate.max_daily_rate)

@app.post("/simulate-entry")
async def simulate_entry(db: Session = Depends(get_db)):
    # 1. Fake AI memberikan plat dan tipe kendaraan
    detected_plate = fake_ai_detector()

    # 2. Cek kendaraan di dalam
    existing = db.query(models.ParkingLog).filter(
        models.ParkingLog.plate_number == detected_plate,
        models.ParkingLog.status == "IN"
    ).first()

    if existing:
        # Ambil tarif berdasarkan kendaraan yang memang sedang parkir.
        rate_info = db.query(models.ParkingRate).filter(models.ParkingRate.id == existing.vehicle_id).first()
        if not rate_info:
            raise HTTPException(status_code=400, detail="Tarif kendaraan untuk log ini tidak ditemukan!")

        # LOGIKA KELUAR
        existing.exit_time = datetime.now(timezone.utc)
        existing.status = "OUT"
        existing.total_amount = calculate_fee(existing.entry_time, existing.exit_time, rate_info)
        db.commit()
        db.refresh(existing)
        v_type = rate_info.vehicle_type
        ws_payload = {
            "event": "VEHICLE_EXIT",
            "plate_number": detected_plate,
            "vehicle_type": v_type,
        }
    else:
        # Simulasi acak tipe kendaraan untuk kendaraan baru masuk.
        v_type = random.choice(["motor", "mobil"])

        # Ambil tarif dari tipe kendaraan yang terdeteksi.
        rate_info = db.query(models.ParkingRate).filter(models.ParkingRate.vehicle_type == v_type).first()
        if not rate_info:
            raise HTTPException(status_code=400, detail=f"Tarif untuk {v_type} belum diatur di database!")

        # LOGIKA MASUK
        new_log = models.ParkingLog(
            plate_number=detected_plate,
            vehicle_id=rate_info.id, # Relasi ke tabel rate
            status="IN"
        )
        db.add(new_log)
        db.commit()
        db.refresh(new_log)
        ws_payload = {
            "event": "VEHICLE_ENTRY",
            "plate_number": detected_plate,
            "vehicle_type": v_type,
        }

    await manager.broadcast(json.dumps(ws_payload))
    return {"status": "success", "type": v_type}

@app.get("/logs")
async def get_all_logs(db: Session = Depends(get_db)):
    return db.query(models.ParkingLog).all()

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)