import os
import cv2
import re
import yaml
import numpy as np
from ultralytics import YOLO

# Import TFLite Interpreter (Otomatis mencari yang ringan dulu)
try:
    from tflite_runtime.interpreter import Interpreter
except ImportError:
    from tensorflow.lite.python.interpreter import Interpreter

class VisionEngine:
    def __init__(self, model_dir="app/model"):
        # 1. Tentukan Jalur Model
        yolo_path = os.path.join(model_dir, "best.pt")
        tflite_path = os.path.join(model_dir, "best.tflite")
        config_path = os.path.join(model_dir, "indonesian_plate_config.yaml")

        # 2. Load Model YOLO
        print("Memuat model YOLO...")
        self.model = YOLO(yolo_path)

        # 3. Load Config Karakter
        print("Memuat konfigurasi OCR...")
        with open(config_path, 'r') as f:
            config = yaml.safe_load(f)
        self.alphabet = config['alphabet']

        # 4. Load Model OCR (TFLite)
        print("Memuat Fast Plate OCR Engine (TFLite)...")
        self.interpreter = Interpreter(model_path=tflite_path)
        self.interpreter.allocate_tensors() # Wajib untuk menyiapkan memori RAM
        
        # Dapatkan detail input dan output layer dari TFLite
        self.input_details = self.interpreter.get_input_details()
        self.output_details = self.interpreter.get_output_details()

        print(f"🚀 AI Engine V2 Siap! (YOLO + TFLite OCR)")

    def detect_coords(self, frame):
        """Menerima frame, mengembalikan koordinat [x1,y1,x2,y2] dan teks plat"""
        # Konversi warna ke RGB untuk akurasi YOLO
        img_rgb_yolo = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        
        # Prediksi YOLO
        results = self.model.predict(img_rgb_yolo, conf=0.4, verbose=False)
        
        if results and len(results[0].boxes) > 0:
            box = results[0].boxes[0]
            xyxy = box.xyxy[0].cpu().numpy().tolist()
            x1, y1, x2, y2 = map(int, xyxy)
            
            # Gunting Plat (Crop) dari frame asli BGR
            plate_crop = frame[y1:y2, x1:x2]
            text = ""
            
            if plate_crop.size > 0:
                # ==========================================
                # PRE-PROCESSING TFLITE OCR
                # ==========================================
                input_shape = self.input_details[0]['shape'] 
                target_height = input_shape[1]
                target_width = input_shape[2]

                img_gray = cv2.cvtColor(plate_crop, cv2.COLOR_BGR2GRAY)
                img_resized = cv2.resize(img_gray, (target_width, target_height))
                img_rgb_ocr = cv2.cvtColor(img_resized, cv2.COLOR_GRAY2RGB)
                
                # Gunakan float32 karena model TFLite meminta input desimal
                img_input = np.expand_dims(img_rgb_ocr, axis=0).astype(np.float32)

                # ==========================================
                # EKSEKUSI TFLite
                # ==========================================
                # Set input
                self.interpreter.set_tensor(self.input_details[0]['index'], img_input)
                # Jalankan model
                self.interpreter.invoke()
                # Ambil output
                # Ambil matriks prediksi 2 dimensi [urutan karakter, probabilitas]
                preds = self.interpreter.get_tensor(self.output_details[0]['index'])[0]
                
                # ==========================================
                # DECODING KARAKTER & PEMBERSIHAN
                # ==========================================
                # Cari probabilitas tertinggi tanpa menciutkannya menjadi satu angka
                best_path = np.argmax(preds, axis=-1)
                hasil_plat = "".join([self.alphabet[idx] for idx in best_path if idx < len(self.alphabet) and self.alphabet[idx] != '_']).strip()
                
                # Hapus spasi dan simbol, hanya sisakan Huruf dan Angka
                text = re.sub(r'[^A-Z0-9]', '', hasil_plat.upper())
            
            return xyxy, text
            
        return None, None