# Bilderrahmen für OBS

Transparente PNGs im Overlay-Stil, die Mitte ist durchsichtig.

| Datei | Größe PNG | Öffnung (Bild) | Öffnung ab |
|---|---|---|---|
| rahmen_16x9.png | 1380 × 820 | 1280 × 720 | x 50, y 50 |
| rahmen_16x9_titel.png | 1380 × 884 | 1280 × 720 | x 50, y 114 (leere Titelleiste oben) |
| rahmen_16x9_gross.png | 1700 × 1000 | 1600 × 900 | x 50, y 50 |
| rahmen_4x3.png | 1060 × 820 | 960 × 720 | x 50, y 50 |
| rahmen_hoch.png | 708 × 1080 | 608 × 980 | x 50, y 50 |

In OBS: Quelle **Bild** mit dem Rahmen, darunter die Bildquelle. Das Bild auf
die Öffnung ziehen (oder Rechtsklick › Transformieren › Transformation
bearbeiten: Position = Rahmenposition + „Öffnung ab“, Größe = Öffnung). Rahmen
und Bild zusammen in eine **Gruppe** legen, dann lassen sie sich gemeinsam
verschieben und ein- / ausblenden.

Titelleiste: bei `rahmen_16x9_titel.png` eine Textquelle (weiß, kursiv, ~38 px)
auf die Leiste legen. Fester Text direkt im PNG oder eigene Größe:

    python make_frame.py --inner 1280x720 --title "Fahrer des Tages" --out frames/dotd.png
