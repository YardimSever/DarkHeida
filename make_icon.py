"""
DarkHeida İkon Oluşturucu
Büyük PNG'yi çoklu boyutlu .ico dosyasına çevirir
"""
from PIL import Image
import os

# Kaynak dosya (büyük logo)
SOURCE = "logo.png"  # veya elindeki büyük resmin adı

# Çıktı dosyası
OUTPUT = "darkheida.ico"

# Windows için ideal 6 boyut
SIZES = [
    (16, 16),
    (32, 32),
    (48, 48),
    (64, 64),
    (128, 128),
    (256, 256),
]

def main():
    if not os.path.exists(SOURCE):
        print(f"❌ HATA: '{SOURCE}' bulunamadı!")
        print(f"   Lütfen büyük logoyu '{SOURCE}' olarak kaydet.")
        return

    print(f"📁 Kaynak: {SOURCE}")
    img = Image.open(SOURCE).convert("RGBA")
    print(f"📐 Mevcut boyut: {img.size}")

    # Eğer kare değilse, kare yap (kenarları kırp)
    w, h = img.size
    if w != h:
        side = min(w, h)
        left = (w - side) // 2
        top = (h - side) // 2
        img = img.crop((left, top, left + side, top + side))
        print(f"✂️ Kare yapıldı: {img.size}")

    # .ico olarak kaydet — çoklu boyut
    img.save(OUTPUT, format="ICO", sizes=SIZES)
    print(f"✅ Başarılı! '{OUTPUT}' oluşturuldu.")
    print(f"📐 İçerdiği boyutlar: {SIZES}")

if __name__ == "__main__":
    main()