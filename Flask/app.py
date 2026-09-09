from flask import Flask, render_template, Response, request, jsonify

import cv2
import asyncio
import json
import math
import os
import threading
import time

import websockets
from dotenv import load_dotenv


# ============================================================
# KONFIGURATION
# ============================================================

load_dotenv(
    "/home/superuser/Downloads/projekt/.env"
)

AIS_API_KEY = os.getenv("AISSTREAM_API_KEY")

AIS_URL = "wss://stream.aisstream.io/v0/stream"

CAMERA_LAT = 53.544999
CAMERA_LON = 9.9503613

# Hamburg / Elbe
AIS_BOUNDING_BOX = [
    [
        53.52,
        9.88
    ],
    [
        53.56,
        10.00
    ]
]


# ============================================================
# FLASK
# ============================================================

app = Flask(__name__)


# ============================================================
# KAMERA
# ============================================================

kamera = cv2.VideoCapture(
    "/dev/video0",
    cv2.CAP_V4L2
)

kamera.set(
    cv2.CAP_PROP_FOURCC,
    cv2.VideoWriter_fourcc(*"MJPG")
)

kamera.set(
    cv2.CAP_PROP_FRAME_WIDTH,
    1280
)

kamera.set(
    cv2.CAP_PROP_FRAME_HEIGHT,
    720
)

kamera.set(
    cv2.CAP_PROP_FPS,
    30
)

print(
    "Kamera geöffnet:",
    kamera.isOpened()
)


# ============================================================
# KAMERA ZOOM
# ============================================================

zoom = 1.0

position_x = 0.5
position_y = 0.5


# ============================================================
# AIS DATEN
# ============================================================

schiffe = {}

schiffe_lock = threading.Lock()

ais_status = {
    "connected": False,
    "messages": 0,
    "last_message": None,
    "last_error": None,
    "close_code": None,
    "close_reason": None
}


# ============================================================
# DISTANZ
# ============================================================

def berechne_entfernung(
    lat1,
    lon1,
    lat2,
    lon2
):

    erdradius = 6371.0

    lat1 = math.radians(lat1)
    lon1 = math.radians(lon1)

    lat2 = math.radians(lat2)
    lon2 = math.radians(lon2)

    dlat = lat2 - lat1
    dlon = lon2 - lon1

    a = (
        math.sin(dlat / 2) ** 2
        +
        math.cos(lat1)
        *
        math.cos(lat2)
        *
        math.sin(dlon / 2) ** 2
    )

    c = 2 * math.atan2(
        math.sqrt(a),
        math.sqrt(1 - a)
    )

    return erdradius * c


# ============================================================
# RICHTUNG
# ============================================================

def berechne_richtung(
    lat1,
    lon1,
    lat2,
    lon2
):

    lat1 = math.radians(lat1)
    lat2 = math.radians(lat2)

    dlon = math.radians(
        lon2 - lon1
    )

    x = (
        math.sin(dlon)
        *
        math.cos(lat2)
    )

    y = (
        math.cos(lat1)
        *
        math.sin(lat2)
        -
        math.sin(lat1)
        *
        math.cos(lat2)
        *
        math.cos(dlon)
    )

    richtung = math.degrees(
        math.atan2(x, y)
    )

    return (
        richtung + 360
    ) % 360


# ============================================================
# AIS POSITION VERARBEITEN
# ============================================================

def verarbeite_ais_nachricht(data):

    message_type = data.get(
        "MessageType"
    )

    if message_type != "PositionReport":
        return

    message = data.get(
        "Message",
        {}
    )

    position = message.get(
        "PositionReport",
        {}
    )

    metadata = data.get(
        "MetaData",
        {}
    )

    # --------------------------------------------------------
    # MMSI
    # --------------------------------------------------------

    mmsi = (
        metadata.get("MMSI")
        or
        position.get("UserID")
    )

    if not mmsi:
        return

    mmsi = str(mmsi)

    # --------------------------------------------------------
    # POSITION
    # --------------------------------------------------------

    latitude = metadata.get(
        "Latitude"
    )

    longitude = metadata.get(
        "Longitude"
    )

    if latitude is None:
        latitude = position.get(
            "Latitude"
        )

    if longitude is None:
        longitude = position.get(
            "Longitude"
        )

    if latitude is None:
        return

    if longitude is None:
        return

    # --------------------------------------------------------
    # NAME
    # --------------------------------------------------------

    name = metadata.get(
        "ShipName"
    )

    if not name:
        name = f"MMSI {mmsi}"

    name = str(name).strip()

    if not name:
        name = f"MMSI {mmsi}"

    # --------------------------------------------------------
    # GESCHWINDIGKEIT
    # --------------------------------------------------------

    speed = position.get(
        "Sog",
        0
    )

    try:

        speed = float(speed)

    except Exception:

        speed = 0.0

    # --------------------------------------------------------
    # KURS
    # --------------------------------------------------------

    course = position.get(
        "Cog"
    )

    if course is not None:

        try:

            course = float(course)

        except Exception:

            course = None

    # --------------------------------------------------------
    # HEADING
    # --------------------------------------------------------

    heading = position.get(
        "TrueHeading"
    )

    if heading is not None:

        try:

            heading = float(heading)

            if heading >= 360:
                heading = None

        except Exception:

            heading = None

    # --------------------------------------------------------
    # SPEICHERN
    # --------------------------------------------------------

    with schiffe_lock:

        schiffe[mmsi] = {
            "mmsi": mmsi,
            "name": name,

            "latitude": float(
                latitude
            ),

            "longitude": float(
                longitude
            ),

            "speed": speed,

            "speed_kmh": round(
                speed * 1.852,
                1
            ),

            "course": course,

            "heading": heading,

            "last_seen": time.time()
        }


# ============================================================
# AIS VERBINDUNG
# ============================================================

async def ais_verbindung():

    if not AIS_API_KEY:

        print()
        print(
            "=========================================="
        )
        print(
            "AIS FEHLER: API KEY FEHLT"
        )
        print(
            "=========================================="
        )
        print(
            "Erwartete Datei:"
        )
        print(
            "/home/superuser/Downloads/projekt/.env"
        )
        print()
        print(
            "AISSTREAM_API_KEY=DEIN_KEY"
        )
        print(
            "=========================================="
        )
        print()

        ais_status[
            "last_error"
        ] = "API Key fehlt"

        return

    print(
        "Verbinde mit AISStream..."
    )

    while True:

        try:

            async with websockets.connect(
                AIS_URL,
                compression="deflate",
                ping_interval=20,
                ping_timeout=20,
                close_timeout=10,
                max_size=None
            ) as websocket:

                print(
                    "AIS verbunden!"
                )

                ais_status[
                    "connected"
                ] = True

                ais_status[
                    "last_error"
                ] = None

                # ------------------------------------------------
                # SUBSCRIPTION
                # ------------------------------------------------

                subscription = {
                    "APIKey": AIS_API_KEY,

                    "BoundingBoxes": [
                        AIS_BOUNDING_BOX
                    ],

                    "FilterMessageTypes": [
                        "PositionReport"
                    ]
                }

                await websocket.send(
                    json.dumps(
                        subscription
                    )
                )

                print(
                    "AIS Subscription gesendet!"
                )

                # ------------------------------------------------
                # DATEN EMPFANGEN
                # ------------------------------------------------

                async for raw_data in websocket:

                    try:

                        if isinstance(
                            raw_data,
                            bytes
                        ):

                            raw_data = raw_data.decode(
                                "utf-8"
                            )

                        data = json.loads(
                            raw_data
                        )

                        message_type = data.get(
                            "MessageType"
                        )

                        if message_type == "SubscriptionConfirmation":

                            print(
                                "AIS Subscription bestätigt!"
                            )

                            continue

                        if message_type == "PositionReport":

                            verarbeite_ais_nachricht(
                                data
                            )

                            ais_status[
                                "messages"
                            ] += 1

                            ais_status[
                                "last_message"
                            ] = time.time()

                    except Exception as error:

                        print(
                            "AIS Datenfehler:",
                            repr(error)
                        )

        except websockets.ConnectionClosed as error:

            ais_status[
                "connected"
            ] = False

            ais_status[
                "close_code"
            ] = error.code

            ais_status[
                "close_reason"
            ] = error.reason

            print(
                "AIS Verbindung geschlossen!"
            )

            print(
                "Close Code:",
                error.code
            )

            print(
                "Close Reason:",
                repr(error.reason)
            )

        except Exception as error:

            ais_status[
                "connected"
            ] = False

            ais_status[
                "last_error"
            ] = repr(error)

            print(
                "AIS Fehler:",
                repr(error)
            )

        ais_status[
            "connected"
        ] = False

        print(
            "Neue Verbindung in 5 Sekunden..."
        )

        await asyncio.sleep(5)


# ============================================================
# AIS THREAD
# ============================================================

def starte_ais():

    asyncio.run(
        ais_verbindung()
    )


# ============================================================
# KAMERA STREAM
# ============================================================

def kamera_stream():

    global zoom
    global position_x
    global position_y

    while True:

        erfolg, bild = kamera.read()

        if not erfolg:

            print(
                "Kein Kamerabild erhalten"
            )

            time.sleep(
                0.1
            )

            continue

        hoehe, breite = bild.shape[:2]

        if zoom > 1.0:

            ausschnitt_breite = int(
                breite / zoom
            )

            ausschnitt_hoehe = int(
                hoehe / zoom
            )

            max_x = (
                breite
                -
                ausschnitt_breite
            )

            max_y = (
                hoehe
                -
                ausschnitt_hoehe
            )

            x1 = int(
                position_x * max_x
            )

            y1 = int(
                position_y * max_y
            )

            x2 = (
                x1
                +
                ausschnitt_breite
            )

            y2 = (
                y1
                +
                ausschnitt_hoehe
            )

            bild = bild[
                y1:y2,
                x1:x2
            ]

            bild = cv2.resize(
                bild,
                (
                    breite,
                    hoehe
                ),
                interpolation=cv2.INTER_LINEAR
            )

        erfolg, buffer = cv2.imencode(
            ".jpg",
            bild
        )

        if not erfolg:
            continue

        bild_bytes = buffer.tobytes()

        yield (
            b"--frame\r\n"
            b"Content-Type: image/jpeg\r\n\r\n"
            +
            bild_bytes
            +
            b"\r\n"
        )


# ============================================================
# STARTSEITE
# ============================================================

@app.route("/")
def home():

    return render_template(
        "index.html"
    )


# ============================================================
# VIDEO
# ============================================================

@app.route("/video_feed")
def video_feed():

    return Response(
        kamera_stream(),
        mimetype=(
            "multipart/x-mixed-replace;"
            " boundary=frame"
        )
    )


# ============================================================
# ZOOM
# ============================================================

@app.route(
    "/zoom",
    methods=["POST"]
)
def set_zoom():

    global zoom
    global position_x
    global position_y

    daten = request.get_json(
        silent=True
    ) or {}

    try:

        zoom = float(
            daten.get(
                "zoom",
                zoom
            )
        )

    except Exception:

        return jsonify({
            "error": "Ungültiger Zoom"
        }), 400

    zoom = max(
        1.0,
        min(4.0, zoom)
    )

    if zoom == 1.0:

        position_x = 0.5
        position_y = 0.5

    return jsonify({
        "zoom": zoom,
        "x": position_x,
        "y": position_y
    })


# ============================================================
# KAMERA BEWEGEN
# ============================================================

@app.route(
    "/move",
    methods=["POST"]
)
def move():

    global position_x
    global position_y

    daten = request.get_json(
        silent=True
    ) or {}

    richtung = daten.get(
        "richtung"
    )

    schritt = 0.1

    if richtung == "links":

        position_x -= schritt

    elif richtung == "rechts":

        position_x += schritt

    elif richtung == "oben":

        position_y -= schritt

    elif richtung == "unten":

        position_y += schritt

    else:

        return jsonify({
            "error": "Ungültige Richtung"
        }), 400

    position_x = max(
        0.0,
        min(1.0, position_x)
    )

    position_y = max(
        0.0,
        min(1.0, position_y)
    )

    return jsonify({
        "x": position_x,
        "y": position_y
    })


# ============================================================
# SCHIFFE
# ============================================================

@app.route("/ships")
def ships_api():

    jetzt = time.time()

    ausgabe = []

    with schiffe_lock:

        # alte Schiffe löschen
        alte_mmsi = []

        for mmsi, schiff in schiffe.items():

            if (
                jetzt
                -
                schiff["last_seen"]
                >
                600
            ):

                alte_mmsi.append(
                    mmsi
                )

        for mmsi in alte_mmsi:

            del schiffe[mmsi]

        # --------------------------------------------------------
        # Schiffe ausgeben
        # --------------------------------------------------------

        for schiff in schiffe.values():

            lat = schiff.get(
                "latitude"
            )

            lon = schiff.get(
                "longitude"
            )

            if lat is None or lon is None:
                continue

            entfernung = berechne_entfernung(
                CAMERA_LAT,
                CAMERA_LON,
                lat,
                lon
            )

            richtung = berechne_richtung(
                CAMERA_LAT,
                CAMERA_LON,
                lat,
                lon
            )

            daten = dict(
                schiff
            )

            daten[
                "distance_km"
            ] = round(
                entfernung,
                2
            )

            daten[
                "distance_nm"
            ] = round(
                entfernung / 1.852,
                2
            )

            daten[
                "bearing"
            ] = round(
                richtung,
                1
            )

            daten[
                "moving"
            ] = (
                schiff["speed"]
                >
                0.5
            )

            ausgabe.append(
                daten
            )

    # Fahrende Schiffe zuerst
    ausgabe.sort(
        key=lambda x: (
            not x["moving"],
            x["distance_km"]
        )
    )

    return jsonify({
        "ships": ausgabe,
        "count": len(ausgabe),
        "ais_connected": ais_status[
            "connected"
        ]
    })


# ============================================================
# AIS STATUS
# ============================================================

@app.route("/ais_status")
def ais_status_api():

    status = dict(
        ais_status
    )

    status[
        "ship_count"
    ] = len(schiffe)

    if status[
        "last_message"
    ]:

        status[
            "last_message_ago"
        ] = round(
            time.time()
            -
            status["last_message"],
            1
        )

    else:

        status[
            "last_message_ago"
        ] = None

    return jsonify(
        status
    )


# ============================================================
# PROGRAMM START
# ============================================================

if __name__ == "__main__":

    print()
    print(
        "========================================"
    )
    print(
        " RASPBERRY PI ELBE VISION"
    )
    print(
        "========================================"
    )

    print(
        "Kamera:",
        kamera.isOpened()
    )

    print(
        "AIS API Key:",
        "OK"
        if AIS_API_KEY
        else "FEHLT"
    )

    print(
        "========================================"
    )
    print()

    # AIS im Hintergrund
    threading.Thread(
        target=starte_ais,
        daemon=True
    ).start()

    # Flask
    app.run(
        host="0.0.0.0",
        port=5000,
        debug=False,
        threaded=True
    )