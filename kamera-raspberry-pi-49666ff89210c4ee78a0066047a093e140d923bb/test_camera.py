import cv2

kamera = cv2.VideoCapture("/dev/video0", cv2.CAP_V4L2)

print("Kamera geöffnet:", kamera.isOpened())

if kamera.isOpened():
    erfolg, bild = kamera.read()
    print("Frame erhalten:", erfolg)

    if erfolg:
        print("Bildgröße:", bild.shape)

kamera.release()