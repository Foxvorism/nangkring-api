from datetime import datetime, timedelta
from jose import JWTError, jwt
from passlib.context import CryptContext

# Konfigurasi Keamanan
SECRET_KEY = "NANGKRING_SUPER_SECRET_KEY" # Ganti dengan string acak
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def hash_password(password: str):
    # Pastikan password dirubah menjadi string dan tidak kosong
    if not password:
        return None
    return pwd_context.hash(password)

def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)

def create_access_token(data: dict):
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)