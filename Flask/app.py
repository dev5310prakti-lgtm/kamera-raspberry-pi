
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

load_dotenv("/home/superuser/Downloads/projekt/.env")

AIS_API_KEY = os.getenv("AISSTREAM_API_KEY")

AIS_URL = "wss://stream.aisstream.io/v0/stream"

# Position der Kamera
CAMERA_LAT = 53.544999
CAMERA_LON = 9.9503613

# Maximale Entfernung für Schiffe
MAX_SHIP_DISTANCE_KM = 1.0

# AIS-Bereich
AIS_BOUNDING_BOX = [
    [53.5360, 9.9340],
    [53.5540, 9.9667]
]

# Kamera
CAMERA_DEVICE = "/dev/video0"
CAMERA_WIDTH = 1280
CAMERA_HEIGHT = 720
CAMERA_FPS = 20
JPEG_QUALITY = 78


# ============================================================
# FLASK
# ============================================================

app = Flask(__name__)


# ============================================================
# KAMERA
# ============================================================

kamera = cv2.VideoCapture(
    CAMERA_DEVICE,
    cv2.CAP_V4L2
)

kamera.set(
    cv2.CAP_PROP_FOURCC,
    cv2.VideoWriter_fourcc(*"MJPG")
)

kamera.set(
    cv2.CAP_PROP_FRAME_WIDTH,
    CAMERA_WIDTH
)

kamera.set(
    cv2.CAP_PROP_FRAME_HEIGHT,
    CAMERA_HEIGHT
)

kamera.set(
    cv2.CAP_PROP_FPS,
    CAMERA_FPS
)

kamera.set(
    cv2.CAP_PROP_BUFFERSIZE,
    1
)

kamera_lock = threading.Lock()

aktuelles_jpeg = None

kamera_laeuft = True

zoom = 1.0
position_x = 0.5
position_y = 0.5

print(
    "Kamera geöffnet:",
    kamera.isOpened()
)


# ============================================================
# KAMERA BILDVERARBEITUNG
# ============================================================

def verarbeite_kamerabild(bild):

    global zoom
    global position_x
    global position_y

    with kamera_lock:

        aktueller_zoom = zoom
        aktuelles_x = position_x
        aktuelles_y = position_y

    hoehe, breite = bild.shape[:2]

    if aktueller_zoom > 1.0:

        ausschnitt_breite = int(
            breite / aktueller_zoom
        )

        ausschnitt_hoehe = int(
            hoehe / aktueller_zoom
        )

        ausschnitt_breite = max(
            1,
            min(ausschnitt_breite, breite)
        )

        ausschnitt_hoehe = max(
            1,
            min(ausschnitt_hoehe, hoehe)
        )

        max_x = breite - ausschnitt_breite
        max_y = hoehe - ausschnitt_hoehe

        x1 = int(
            aktuelles_x * max_x
        )

        y1 = int(
            aktuelles_y * max_y
        )

        x1 = max(
            0,
            min(x1, max_x)
        )

        y1 = max(
            0,
            min(y1, max_y)
        )

        x2 = x1 + ausschnitt_breite
        y2 = y1 + ausschnitt_hoehe

        bild = bild[
            y1:y2,
            x1:x2
        ]

        bild = cv2.resize(
            bild,
            (CAMERA_WIDTH, CAMERA_HEIGHT),
            interpolation=cv2.INTER_LINEAR
        )

    return bild


# ============================================================
# KAMERA THREAD
# ============================================================

def kamera_thread():

    global aktuelles_jpeg

    print(
        "Kamera-Thread gestartet."
    )

    while kamera_laeuft:

        erfolg, bild = kamera.read()

        if not erfolg:

            print(
                "Kein Kamerabild erhalten."
            )

            time.sleep(0.1)
            continue

        try:

            bild = verarbeite_kamerabild(
                bild
            )

            erfolg, buffer = cv2.imencode(
                ".jpg",
                bild,
                [
                    cv2.IMWRITE_JPEG_QUALITY,
                    JPEG_QUALITY
                ]
            )

            if not erfolg:
                continue

            jpeg = buffer.tobytes()

            with kamera_lock:
                aktuelles_jpeg = jpeg

        except Exception as error:

            print(
                "Kamera-Fehler:",
                repr(error)
            )

            time.sleep(0.05)


# ============================================================
# KAMERA STREAM
# ============================================================

def kamera_stream():

    while True:

        with kamera_lock:
            frame = aktuelles_jpeg

        if frame is None:

            time.sleep(0.01)
            continue

        yield (
            b"--frame\r\n"
            b"Content-Type: image/jpeg\r\n"
            b"Content-Length: "
            + str(len(frame)).encode()
            + b"\r\n"
            b"Cache-Control: no-cache, no-store, must-revalidate\r\n"
            b"Pragma: no-cache\r\n"
            b"\r\n"
            + frame
            + b"\r\n"
        )

        time.sleep(0.01)


# ============================================================
# KAMERA THREAD STARTEN
# ============================================================

kamera_thread_handle = threading.Thread(
    target=kamera_thread,
    daemon=True,
    name="CameraCapture"
)

kamera_thread_handle.start()


# ============================================================
# AIS
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
        * math.cos(lat2)
        * math.sin(dlon / 2) ** 2
    )

    c = 2 * math.atan2(
        math.sqrt(a),
        math.sqrt(1 - a)
    )

    return erdradius * c


# ============================================================
# RICHTUNG / BEARING
# ============================================================

def berechne_richtung(
    lat1,
    lon1,
    lat2,
    lon2
):

    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)

    dlon = math.radians(
        lon2 - lon1
    )

    x = (
        math.sin(dlon)
        * math.cos(lat2_rad)
    )

    y = (
        math.cos(lat1_rad)
        * math.sin(lat2_rad)
        -
        math.sin(lat1_rad)
        * math.cos(lat2_rad)
        * math.cos(dlon)
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

    if data.get("MessageType") != "PositionReport":
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

    if latitude is None or longitude is None:
        return

    try:

        latitude = float(latitude)
        longitude = float(longitude)

    except Exception:

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
    # SPEED OVER GROUND
    # --------------------------------------------------------

    speed = position.get(
        "Sog"
    )

    try:

        if speed is None:
            speed = 0.0
        else:
            speed = float(speed)

    except Exception:

        speed = 0.0

    # --------------------------------------------------------
    # COURSE
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
    # ZEIT
    # --------------------------------------------------------

    jetzt = time.time()

    # --------------------------------------------------------
    # ALTE POSITION
    # --------------------------------------------------------

    alte_position = None

    with schiffe_lock:

        if mmsi in schiffe:

            alte_position = {
                "latitude": schiffe[mmsi].get(
                    "latitude"
                ),
                "longitude": schiffe[mmsi].get(
                    "longitude"
                ),
                "last_seen": schiffe[mmsi].get(
                    "last_seen"
                )
            }

    # --------------------------------------------------------
    # BEWEGUNG AUS POSITIONEN BERECHNEN
    # --------------------------------------------------------

    berechnete_geschwindigkeit = 0.0

    position_bewegt = False

    if alte_position:

        alte_lat = alte_position.get(
            "latitude"
        )

        alte_lon = alte_position.get(
            "longitude"
        )

        alte_zeit = alte_position.get(
            "last_seen"
        )

        if (
            alte_lat is not None
            and alte_lon is not None
            and alte_zeit is not None
        ):

            zeit_diff = jetzt - alte_zeit

            if (
                zeit_diff > 1.0
                and zeit_diff < 300
            ):

                strecke_km = berechne_entfernung(
                    alte_lat,
                    alte_lon,
                    latitude,
                    longitude
                )

                # km/h
                kmh = (
                    strecke_km
                    /
                    (zeit_diff / 3600)
                )

                # Knoten
                berechnete_geschwindigkeit = (
                    kmh / 1.852
                )

                # GPS/AIS-Rauschen ignorieren
                if (
                    strecke_km >= 0.03
                    and berechnete_geschwindigkeit >= 0.5
                ):

                    position_bewegt = True

    # --------------------------------------------------------
    # FAHREND / STEHEND
    # --------------------------------------------------------

    moving = (
        speed >= 0.5
        or
        position_bewegt
    )

    # Tatsächliche Geschwindigkeit für Anzeige
    motion_speed = max(
        speed,
        berechnete_geschwindigkeit
    )

    # --------------------------------------------------------
    # SPEICHERN
    # --------------------------------------------------------

    with schiffe_lock:

        schiffe[mmsi] = {

            "mmsi": mmsi,

            "name": name,

            "latitude": latitude,

            "longitude": longitude,

            "speed": round(
                speed,
                2
            ),

            "speed_kmh": round(
                speed * 1.852,
                1
            ),

            "motion_speed": round(
                motion_speed,
                2
            ),

            "motion_speed_kmh": round(
                motion_speed * 1.852,
                1
            ),

            "course": course,

            "heading": heading,

            "moving": moving,

            "last_seen": jetzt
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
            "Datei:"
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

                        if (
                            message_type
                            ==
                            "SubscriptionConfirmation"
                        ):

                            print(
                                "AIS Subscription bestätigt!"
                            )

                            continue

                        if (
                            message_type
                            ==
                            "PositionReport"
                        ):

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
        ),
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache"
        }
    )


# ============================================================
# KAMERA STATUS
# ============================================================

@app.route("/camera_status")
def camera_status():

    with kamera_lock:

        hat_bild = (
            aktuelles_jpeg is not None
        )

        aktueller_zoom = zoom

    return jsonify({

        "camera_open": kamera.isOpened(),

        "has_frame": hat_bild,

        "zoom": aktueller_zoom,

        "width": CAMERA_WIDTH,

        "height": CAMERA_HEIGHT,

        "fps": CAMERA_FPS
    })


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

        neuer_zoom = float(
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
        min(4.0, neuer_zoom)
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

        "y": position_y,

        "zoom": zoom
    })


# ============================================================
# KAMERA RESET
# ============================================================

@app.route(
    "/camera_reset",
    methods=["POST"]
)
def camera_reset():

    global zoom
    global position_x
    global position_y

    zoom = 1.0
    position_x = 0.5
    position_y = 0.5

    return jsonify({

        "zoom": zoom,

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

            # ================================================
            # NUR 1 KM UM DIE KAMERA
            # ================================================

            if entfernung > MAX_SHIP_DISTANCE_KM:
                continue

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

            ausgabe.append(
                daten
            )

    # Fahrende zuerst
    ausgabe.sort(
        key=lambda x: (
            not x.get("moving", False),
            x["distance_km"]
        )
    )

    return jsonify({

        "ships": ausgabe,

        "count": len(ausgabe),

        "ais_connected": ais_status[
            "connected"
        ],

        "camera": {
            "latitude": CAMERA_LAT,
            "longitude": CAMERA_LON
        },

        "radius_km": MAX_SHIP_DISTANCE_KM
    })


# ============================================================
# AIS STATUS
# ============================================================

@app.route("/ais_status")
def ais_status_api():

    status = dict(
        ais_status
    )

    with schiffe_lock:

        status[
            "ship_count"
        ] = len(schiffe)

    if status["last_message"]:

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
        "       RASPBERRY PI ELBE VISION"
    )
    print(
        "========================================"
    )

    print(
        "Kamera:",
        kamera.isOpened()
    )

    print(
        "Kamera Gerät:",
        CAMERA_DEVICE
    )

    print(
        "Auflösung:",
        f"{CAMERA_WIDTH}x{CAMERA_HEIGHT}"
    )

    print(
        "FPS:",
        CAMERA_FPS
    )

    print(
        "AIS API Key:",
        "OK"
        if AIS_API_KEY
        else "FEHLT"
    )

    print(
        "AIS Radius:",
        f"{MAX_SHIP_DISTANCE_KM} km"
    )

    print(
        "========================================"
    )
    print()

    threading.Thread(
        target=starte_ais,
        daemon=True,
        name="AISStream"
    ).start()

    app.run(
        host="0.0.0.0",
        port=5000,
        debug=False,
        threaded=True,
        use_reloader=False
    )
