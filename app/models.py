from sqlalchemy import Column, Integer, String, DateTime, Float, ForeignKey
from sqlalchemy.sql import func
from .database import Base, SessionLocal, engine
from . import auth

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True)
    password_hash = Column(String)
    role = Column(String, default="operator")

class ParkingLog(Base):
    __tablename__ = "parking_logs"
    id = Column(Integer, primary_key=True, index=True)
    vehicle_id = Column(Integer, ForeignKey("parking_rates.id"))
    plate_number = Column(String, index=True)
    entry_time = Column(DateTime(timezone=True), server_default=func.now())
    exit_time = Column(DateTime(timezone=True), nullable=True)
    status = Column(String, default="IN")
    total_amount = Column(Float, default=0.0)

class ParkingRate(Base):
    __tablename__ = "parking_rates"
    id = Column(Integer, primary_key=True, index=True)
    vehicle_type = Column(String, unique=True)
    first_hour_rate = Column(Float)
    additional_hour_rate = Column(Float)
    max_daily_rate = Column(Float)
    overnight_rate = Column(Float)

def seed_data():
    db = SessionLocal()
    try:
        if db.query(ParkingRate).count() == 0:
            rates = [
                ParkingRate(
                    vehicle_type="motor", 
                    first_hour_rate=3000, 
                    additional_hour_rate=2000, 
                    max_daily_rate=8000, 
                    overnight_rate=10000
                ),
                ParkingRate(
                    vehicle_type="mobil", 
                    first_hour_rate=5000, 
                    additional_hour_rate=4000, 
                    max_daily_rate=20000, 
                    overnight_rate=25000
                )
            ]
            db.add_all(rates)
            print("Seed data untuk Parking Rates berhasil ditambahkan.")

        if db.query(User).filter(User.username == "adminNangkring").first() is None:
            admin = User(
    username="adminNangkring",
    password_hash=auth.hash_password("123qweasd"), # Password ini sangat aman (jauh di bawah 72 byte)
    role="admin"
)
            db.add(admin)
            print("Seed data untuk User Admin berhasil ditambahkan.")

        db.commit()
    except Exception as e:
        print(f"Gagal seeding: {e}")
        db.rollback()
    finally:
        db.close()
