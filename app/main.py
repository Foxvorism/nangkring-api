import math
import time
import random
import json
import cv2
import asyncio
import base64
import numpy as np
import os
from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi import WebSocket, WebSocketDisconnect
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from . import models, database
from . import auth
from .vision import VisionEngine
from pydantic import BaseModel
from fastapi import APIRouter, HTTPException
from datetime import datetime, timezone
import math

# Membuat tabel otomatis di PostgreSQL saat server nyala
models.Base.metadata.create_all(bind=database.engine)
models.seed_data()

app = FastAPI(title="nAngkrIng API")
vision = VisionEngine()

_default_cors_origins = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "https://nangkring.vercel.app",
]
_cors_origins_env = os.getenv("CORS_ORIGINS", "")
_cors_origins = list(
    dict.fromkeys(
        _default_cors_origins
        + [origin.strip() for origin in _cors_origins_env.split(",") if origin.strip()]
    )
)

# Tambahkan baris ini tepat di bawah app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
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

class GateInRequest(BaseModel):
    plate_number: str
    vehicle_type: str

class GateOutRequest(BaseModel):
    plate_number: str

manager = ConnectionManager()


def get_local_date(value: datetime) -> datetime.date:
    if value.tzinfo is not None:
        return value.astimezone().date()

    return value.date()

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
    entry_date = get_local_date(entry_time)
    exit_date = get_local_date(exit_time)
    overstay_days = max(0, (exit_date - entry_date).days)
    
    if overstay_days >= 1:
        return overstay_days * rate.overnight_rate

    # Konversi ke jam (dibulatkan ke atas)
    hours = math.ceil(total_seconds / 3600)
    
    # Logika Per Jam
    fee = rate.first_hour_rate + (max(0, hours - 1) * rate.additional_hour_rate)
    
    # Batasi dengan harga maksimal harian
    return min(fee, rate.max_daily_rate)


def ensure_overstay_columns():
    with database.engine.begin() as connection:
        connection.exec_driver_sql(
            "ALTER TABLE parking_logs ADD COLUMN IF NOT EXISTS overstay_days INTEGER NOT NULL DEFAULT 0"
        )


async def sync_overstay_logs():
    db = database.SessionLocal()
    try:
        today = datetime.now().astimezone().date()
        active_logs = db.query(models.ParkingLog).filter(
            models.ParkingLog.exit_time.is_(None),
            models.ParkingLog.status.in_(["parked-in", "overstay"]),
        ).all()

        for log in active_logs:
            entry_date = get_local_date(log.entry_time)
            desired_overstay_days = max(0, (today - entry_date).days)

            if desired_overstay_days <= log.overstay_days:
                continue

            rate_info = db.query(models.ParkingRate).filter(
                models.ParkingRate.id == log.vehicle_id
            ).first()

            if not rate_info:
                continue

            added_days = desired_overstay_days - log.overstay_days
            log.overstay_days = desired_overstay_days
            log.status = "overstay"
            log.total_amount = (log.total_amount or 0) + (
                added_days * rate_info.overnight_rate
            )

            db.commit()
            db.refresh(log)

            await manager.broadcast(
                json.dumps(
                    {
                        "event": "OVERSTAY_ALERT",
                        "plate_number": log.plate_number,
                        "vehicle_type": rate_info.vehicle_type,
                        "status": log.status,
                        "overstay_days": log.overstay_days,
                        "added_fee": added_days * rate_info.overnight_rate,
                        "total_amount": log.total_amount,
                    }
                )
            )
    finally:
        db.close()


async def overstay_monitor(stop_event: asyncio.Event):
    while not stop_event.is_set():
        await sync_overstay_logs()
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=60)
        except asyncio.TimeoutError:
            continue


@app.on_event("startup")
async def startup_tasks():
    ensure_overstay_columns()
    app.state.overstay_stop_event = asyncio.Event()
    app.state.overstay_task = asyncio.create_task(
        overstay_monitor(app.state.overstay_stop_event)
    )


@app.on_event("shutdown")
async def shutdown_tasks():
    stop_event = getattr(app.state, "overstay_stop_event", None)
    task = getattr(app.state, "overstay_task", None)

    if stop_event:
        stop_event.set()

    if task:
        await task

@app.post("/login")
async def login(login_data: dict, db: Session = Depends(get_db)):
    try:
        user = db.query(models.User).filter(models.User.username == login_data["username"]).first()
        
        if not user:
            raise HTTPException(status_code=401, detail="User tidak ditemukan")

        # Cek apakah password cocok
        is_valid = auth.verify_password(login_data["password"], user.password_hash)
        
        if not is_valid:
            raise HTTPException(status_code=401, detail="Password salah")
        
        token = auth.create_access_token(data={"sub": user.username, "role": user.role})
        return {"access_token": token, "token_type": "bearer"}
        
    except Exception as e:
        # Ini akan memunculkan pesan error asli di terminal uvicorn
        print(f"❌ LOGIN ERROR: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Internal Error: {str(e)}")

@app.post("/simulate-entry")
async def simulate_entry(db: Session = Depends(get_db)):
    # 1. Fake AI memberikan plat dan tipe kendaraan
    detected_plate = fake_ai_detector()

    # 2. Cek kendaraan di dalam
    existing = db.query(models.ParkingLog).filter(
        models.ParkingLog.plate_number == detected_plate,
        models.ParkingLog.status == "parked-in"
    ).first()

    if existing:
        # Ambil tarif berdasarkan kendaraan yang memang sedang parkir.
        rate_info = db.query(models.ParkingRate).filter(models.ParkingRate.id == existing.vehicle_id).first()
        if not rate_info:
            raise HTTPException(status_code=400, detail="Tarif kendaraan untuk log ini tidak ditemukan!")

        # LOGIKA KELUAR
        existing.exit_time = datetime.now(timezone.utc)
        existing.status = "parked-out"
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
        v_type = random.choice(["motorbike", "car"])

        # Ambil tarif dari tipe kendaraan yang terdeteksi.
        rate_info = db.query(models.ParkingRate).filter(models.ParkingRate.vehicle_type == v_type).first()
        if not rate_info:
            raise HTTPException(status_code=400, detail=f"Tarif untuk {v_type} belum diatur di database!")

        # LOGIKA MASUK
        new_log = models.ParkingLog(
            plate_number=detected_plate,
            vehicle_id=rate_info.id, # Relasi ke tabel rate
            status="parked-in"
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
def get_logs(
    db: Session = Depends(get_db),
    vehicle_type: str = None,
    status: str = None,
    start_date: str = None,
    end_date: str = None,
    limit: int = 10,
    offset: int = 0
):
    # 1. Gunakan join agar bisa mengambil data dari tabel parking_rates
    query = db.query(
        models.ParkingLog, 
        models.ParkingRate.vehicle_type # Ambil kolom vehicle_type dari tabel rates
    ).join(models.ParkingRate, models.ParkingLog.vehicle_id == models.ParkingRate.id)

    # 2. Filter tetap sama seperti sebelumnya
    if vehicle_type:
        query = query.filter(models.ParkingRate.vehicle_type == vehicle_type)
    
    if status:
        query = query.filter(models.ParkingLog.status == status)
    
    if start_date:
        query = query.filter(models.ParkingLog.entry_time >= start_date)
    if end_date:
        query = query.filter(models.ParkingLog.entry_time <= end_date)

    total_count = query.count()
    results = query.order_by(models.ParkingLog.entry_time.desc()).offset(offset).limit(limit).all()

    # 3. Format ulang data agar log.vehicle_type bisa dibaca langsung oleh frontend
    formatted_logs = []
    for log_obj, v_type in results:
        # Kita tambahkan attribute vehicle_type ke dalam objek log sebelum dikirim
        log_data = {
            "id": log_obj.id,
            "plate_number": log_obj.plate_number,
            "entry_time": log_obj.entry_time,
            "exit_time": log_obj.exit_time,
            "status": log_obj.status,
            "total_amount": log_obj.total_amount,
            "vehicle_type": v_type # Ini yang dicari oleh frontend Anda
        }
        formatted_logs.append(log_data)

    return {
        "total": total_count,
        "logs": formatted_logs
    }

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    # HAPUS cv2.VideoCapture(0) dari sini!
    
    try:
        while True:
            # 1. Tunggu kiriman foto dari Frontend
            data = await websocket.receive_text()
            
            try:
                payload = json.loads(data)
                if "image" not in payload:
                    continue
                
                # 2. Decode gambar Base64 dari React menjadi matrix OpenCV
                # Format dari web biasanya: "data:image/jpeg;base64,/9j/4AAQSkZJRg..."
                encoded_data = payload["image"].split(',')[1]
                nparr = np.frombuffer(base64.b64decode(encoded_data), np.uint8)
                frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

                if frame is not None:
                    # 3. Jalankan AI Engine pada foto tersebut
                    coords, plate_text = vision.detect_coords(frame)
                    
                    if coords:
                        ws_payload = {
                            "event": "PLATE_DETECTED",
                            "plate_number": plate_text,
                            "box": coords # [x1, y1, x2, y2]
                        }
                        # 4. Kirim hasil deteksi kembali ke frontend
                        await websocket.send_text(json.dumps(ws_payload))
                        
            except Exception as e:
                print(f"Error processing frame: {e}")
                
    except WebSocketDisconnect:
        manager.disconnect(websocket)

@app.post("/api/gate-in")
async def process_gate_in(req: GateInRequest, db: Session = Depends(get_db)):
    # 1. Cek apakah plat ini sudah masuk dan belum keluar (mencegah double entry)
    existing = db.query(models.ParkingLog).filter(
        models.ParkingLog.plate_number == req.plate_number,
        models.ParkingLog.status == "parked-in"
    ).first()

    if existing:
        return {"status": "error", "message": "Kendaraan ini masih ada di dalam area parkir!"}

    # 2. Ambil tarif berdasarkan tipe kendaraan
    rate_info = db.query(models.ParkingRate).filter(models.ParkingRate.vehicle_type == req.vehicle_type).first()
    if not rate_info:
        raise HTTPException(status_code=400, detail=f"Tarif untuk {req.vehicle_type} belum diatur!")

    # 3. Simpan ke database
    new_log = models.ParkingLog(
        plate_number=req.plate_number,
        vehicle_id=rate_info.id,
        status="parked-in"
    )
    db.add(new_log)
    db.commit()
    db.refresh(new_log)

    # 4. Beri tahu semua client (Dashboard/Log) ada mobil masuk
    ws_payload = {
        "event": "VEHICLE_ENTRY",
        "plate_number": req.plate_number,
        "vehicle_type": req.vehicle_type,
    }
    await manager.broadcast(json.dumps(ws_payload))

    return {"status": "success", "message": "Berhasil masuk"}


@app.post("/api/gate-out")
async def process_gate_out(req: GateOutRequest, db: Session = Depends(get_db)):
    # 1. Cari data parkir masuknya
    existing = db.query(models.ParkingLog).filter(
        models.ParkingLog.plate_number == req.plate_number,
        models.ParkingLog.status == "parked-in"
    ).first()

    if not existing:
        raise HTTPException(status_code=404, detail="Kendaraan tidak ditemukan atau sudah keluar")

    rate_info = db.query(models.ParkingRate).filter(models.ParkingRate.id == existing.vehicle_id).first()

    # 2. Catat waktu keluar dan hitung biaya
    existing.exit_time = datetime.now(timezone.utc)
    existing.status = "parked-out"
    existing.total_amount = calculate_fee(existing.entry_time, existing.exit_time, rate_info)
    existing.overstay_days = max(
        0,
        (get_local_date(existing.exit_time) - get_local_date(existing.entry_time)).days,
    )
    
    db.commit()
    db.refresh(existing)

    # 3. Hitung durasi (jam) untuk dikirim ke layar frontend
    duration = existing.exit_time - existing.entry_time
    hours = math.ceil(duration.total_seconds() / 3600)

    # 4. Beri tahu semua client mobil sudah keluar
    ws_payload = {
        "event": "VEHICLE_EXIT",
        "plate_number": req.plate_number,
        "vehicle_type": rate_info.vehicle_type,
        "fee": existing.total_amount
    }
    await manager.broadcast(json.dumps(ws_payload))

    return {
        "status": "success",
        "fee": existing.total_amount,
        "duration_hours": hours
    }

@app.get("/api/check-vehicle")
async def check_vehicle_fee(plate_number: str, db: Session = Depends(get_db)):
    # 1. Cari data kendaraan yang MASIH PARKIR
    log = db.query(models.ParkingLog).filter(
        models.ParkingLog.plate_number == plate_number.upper(),
        models.ParkingLog.status == "parked-in"
    ).first()

    if not log:
        raise HTTPException(status_code=404, detail="Kendaraan tidak ditemukan di area parkir.")

    # 2. Ambil informasi tarif
    rate_info = db.query(models.ParkingRate).filter(models.ParkingRate.id == log.vehicle_id).first()

    # 3. Hitung durasi dan tarif REAL-TIME (tanpa menyimpannya ke DB)
    current_time = datetime.now(timezone.utc)
    duration = current_time - log.entry_time
    total_seconds = duration.total_seconds()
    
    hours = math.ceil(total_seconds / 3600)
    
    # Gunakan ulang fungsi calculate_fee yang sudah Anda buat di atas
    current_fee = calculate_fee(log.entry_time, current_time, rate_info)

    return {
        "status": "success",
        "vehicle_type": rate_info.vehicle_type,
        "plate_number": log.plate_number,
        "entry_time": log.entry_time.isoformat(),
        "duration_hours": hours,
        "current_fee": current_fee
    }