from flask import Flask, render_template, Response, request, jsonify
import cv2

app = Flask(__name__)

kamera = cv2.VideoCapture("/dev/video0", cv2.CAP_V4L2)

kamera.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
kamera.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
kamera.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
kamera.set(cv2.CAP_PROP_FPS, 30)

print("Kamera geöffnet:", kamera.isOpened())

# Zoom
zoom = 1.0

# Position des Bildausschnitts
position_x = 0.5
position_y = 0.5


def kamera_stream():
    global zoom, position_x, position_y

    while True:

        erfolg, bild = kamera.read()

        if not erfolg:
            print("Kein Kamerabild erhalten")
            break

        hoehe, breite = bild.shape[:2]

        if zoom > 1.0:

            # Größe des Ausschnitts berechnen
            ausschnitt_breite = int(breite / zoom)
            ausschnitt_hoehe = int(hoehe / zoom)

            # Maximale Position
            max_x = breite - ausschnitt_breite
            max_y = hoehe - ausschnitt_hoehe

            # Position berechnen
            x1 = int(position_x * max_x)
            y1 = int(position_y * max_y)

            x2 = x1 + ausschnitt_breite
            y2 = y1 + ausschnitt_hoehe

            # Bild ausschneiden
            bild = bild[y1:y2, x1:x2]

            # Wieder auf 1280x720 vergrößern
            bild = cv2.resize(
                bild,
                (breite, hoehe),
                interpolation=cv2.INTER_LINEAR
            )

        erfolg, buffer = cv2.imencode(".jpg", bild)

        if not erfolg:
            continue

        bild_bytes = buffer.tobytes()

        yield (
            b"--frame\r\n"
            b"Content-Type: image/jpeg\r\n\r\n"
            + bild_bytes
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


@app.route("/zoom", methods=["POST"])
def set_zoom():

    global zoom, position_x, position_y

    daten = request.get_json()

    if "zoom" in daten:
        zoom = float(daten["zoom"])

    zoom = max(1.0, min(4.0, zoom))

    # Beim Herauszoomen Position korrigieren
    if zoom == 1.0:
        position_x = 0.5
        position_y = 0.5

    return jsonify({
        "zoom": zoom,
        "x": position_x,
        "y": position_y
    })


@app.route("/move", methods=["POST"])
def move():

    global position_x, position_y

    daten = request.get_json()

    schritt = 0.1

    if daten["richtung"] == "links":
        position_x -= schritt

    elif daten["richtung"] == "rechts":
        position_x += schritt

    elif daten["richtung"] == "oben":
        position_y -= schritt

    elif daten["richtung"] == "unten":
        position_y += schritt

    # Position begrenzen
    position_x = max(0.0, min(1.0, position_x))
    position_y = max(0.0, min(1.0, position_y))

    return jsonify({
        "x": position_x,
        "y": position_y
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)