from flask import Flask, render_template, Response
import cv2

app = Flask(__name__)

# Kamera öffnen
kamera = cv2.VideoCapture(0)


def kamera_stream():
    while True:
        erfolg, bild = kamera.read()

        if not erfolg:
            break

        # Bild in JPEG umwandeln
        erfolg, buffer = cv2.imencode(".jpg", bild)

        if not erfolg:
            continue

        bild = buffer.tobytes()

        # Bild an den Browser schicken
        yield (
            b"--frame\r\n"
            b"Content-Type: image/jpeg\r\n\r\n"
            + bild
            + b"\r\n"
        )


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/video_feed")
def video_feed():
    return Response(
        kamera_stream(),
        mimetype="multipart/x-mixed-replace; boundary=frame"
    )


if __name__ == "__main__":
    app.run(debug=True)