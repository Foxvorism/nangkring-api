import cv2
import easyocr
from ultralytics import YOLO

class VisionEngine:
    def __init__(self, model_path="app/model/best.pt"):
        # Load model YOLO
        self.model = YOLO(model_path)
        self.reader = easyocr.Reader(["en"], gpu=False) 
        print(f"🚀 AI Engine Siap. Menggunakan perangkat: {self.model.device}")

    def detect_coords(self, frame):
        """Menerima frame gambar, mengembalikan koordinat [x1,y1,x2,y2] dan teks plat"""
        # Matikan verbose agar terminal tidak spam log setiap frame
        results = self.model.predict(frame, conf=0.4, verbose=False)
        
        # Jika ada objek yang terdeteksi
        if results and results[0].boxes:
            # Ambil deteksi dengan tingkat kepercayaan tertinggi (index 0)
            box = results[0].boxes[0] 
            xyxy = box.xyxy[0].cpu().numpy().tolist() # Format: [x1, y1, x2, y2]
            
            # Koordinat untuk memotong (crop) gambar
            x1, y1, x2, y2 = map(int, xyxy)
            plate_crop = frame[y1:y2, x1:x2]
            
            text = ""
            # Pastikan gambar hasil potongan tidak kosong
            if plate_crop.size > 0:
                result_ocr = self.reader.readtext(plate_crop, detail=0)
                # Gabungkan hasil teks dan ubah menjadi huruf kapital
                text = "".join(result_ocr).upper()
            
            return xyxy, text
            
        return None, None