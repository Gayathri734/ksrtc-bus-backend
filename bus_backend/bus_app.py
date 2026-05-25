from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
import requests
import csv
import io
from gtts import gTTS
from html.parser import HTMLParser
from bs4 import BeautifulSoup

app = Flask(__name__)
# Enable CORS so your Lovable frontend website can connect seamlessly
CORS(app)


# -------------------------------
# Robust HTML option parser
# -------------------------------
class OptionValueFinder(HTMLParser):
    def __init__(self, target_name):
        super().__init__()
        self.target = target_name.strip().upper()
        self.current_value = None
        self.result = None

    def handle_starttag(self, tag, attrs):
        if tag == "option":
            for k, v in attrs:
                if k == "value":
                    self.current_value = v

    def handle_data(self, data):
        if self.current_value:
            # Clean up all trailing spaces and hidden control strings (\n, \t)
            clean_data = data.strip().replace("\n", "").replace("\t", "").upper()
            
            # Use partial matching in case the server uses "ALUVA JN" or similar variations
            if self.target in clean_data and self.current_value != "":
                self.result = self.current_value
                
            self.current_value = None


def get_value_from_html(html, name):
    parser = OptionValueFinder(name)
    parser.feed(html)
    return parser.result


# -------------------------------
# CSV lookup
# -------------------------------
def get_value_by_name(csv_file, search_name):
    with open(csv_file, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row["name"].strip().upper() == search_name.strip().upper():
                return row["value"]
        return None


# -------------------------------
# MAIN DATA SCRAPER PIPELINE
# -------------------------------
def get_bus_data(departure_name, destination_name):
    session = requests.Session()

    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://ksrtcedp.com/route/index.php",
        "Origin": "https://ksrtcedp.com"
    })

    # Initialize the cookies session
    try:
        session.get("https://ksrtcedp.com/route/index.php", timeout=10)
    except requests.RequestException:
        return {"error": "Failed to connect to KSRTC base server"}

    # 1. Look up Departure ID locally
    departure_id = get_value_by_name("departure (1).csv", departure_name)
    if not departure_id:
        return {"error": f"Invalid departure: '{departure_name}' not found in local CSV"}

    # 2. Query Remote Server for Destinations valid for this Departure
    try:
        dest_resp = session.get(
            "https://ksrtcedp.com/route/loaddest.php",
            params={"deptid": departure_id},
            timeout=10
        )
    except requests.RequestException:
        return {"error": "Failed to load destinations from live server"}

    # 3. Look up Destination ID dynamically from HTML response
    destination_id = get_value_from_html(dest_resp.text, destination_name)
    if not destination_id:
        return {
            "error": "Invalid destination",
            "message": f"Could not find destination matching '{destination_name}' for departure ID {departure_id}."
        }

    # 4. Fetch the Bus schedule results
    try:
        result_resp = session.post(
            "https://ksrtcedp.com/route/result.php",
            data={
                "seldept": departure_id,
                "seldest": destination_id,
                "submit": "Search"
            },
            timeout=10
        )
    except requests.RequestException:
        return {"error": "Failed to fetch bus routes from live server"}

    # 5. Parse the Scraped HTML Results Table
    soup = BeautifulSoup(result_resp.text, "html.parser")

    output = {
        "from": departure_name,
        "to": destination_name,
        "total_buses": 0,
        "buses": []
    }

    rows = soup.select("table tbody tr")

    for row in rows:
        cols = row.find_all("td")
        if len(cols) < 5:
            continue

        try:
            dep_block = cols[1]
            clock_element = dep_block.find("i", class_="fa-clock-o")
            departure_time = clock_element.parent.text.strip().split()[0] if clock_element else "N/A"
            
            from_place_tag = dep_block.find("span", class_="depthead")
            from_place = from_place_tag.text.strip() if from_place_tag else "Unknown"

            type_tag = cols[2].find("small", class_="typehead")
            bus_type = type_tag.text.strip() if type_tag else "Ordinary"

            via_tag = cols[2].find("span", class_="viahead")
            via = via_tag.text.replace("via:", "").strip() if via_tag else None

            arr_block = cols[3]
            arr_clock_element = arr_block.find("i", class_="fa-clock-o")
            arrival_time = arr_clock_element.parent.text.strip().split()[0] if arr_clock_element else "N/A"
            
            to_place_tag = arr_block.find("span", class_="depthead")
            to_place = to_place_tag.text.strip() if to_place_tag else "Unknown"

            output["buses"].append({
                "from": from_place,
                "to": to_place,
                "departure_time": departure_time,
                "arrival_time": arrival_time,
                "bus_type": bus_type,
                "via": via
            })
        except Exception:
            continue

    output["total_buses"] = len(output["buses"])
    return output


# -------------------------------
# JSON DATA ENDPOINT ROUTE
# -------------------------------
@app.route("/bus", methods=["GET"])
def bus():
    departure = request.args.get("from")
    destination = request.args.get("to")

    if not departure or not destination:
        return jsonify({
            "error": "Missing parameters",
            "usage": "/bus?from=KOZHIKKODE&to=ALUVA"
        }), 400

    data = get_bus_data(departure, destination)
    if "error" in data:
        return jsonify(data), 400

    return jsonify(data)


# -------------------------------
# TEXT TO SPEECH ROUTE
# -------------------------------
@app.route("/bus/speak", methods=["GET"])
def speak_bus():
    departure = request.args.get("from")
    destination = request.args.get("to")

    if not departure or not destination:
        return jsonify({"error": "Missing parameters"}), 400

    # 1. Fetch your bus data using your scraper function
    data = get_bus_data(departure, destination)
    if "error" in data:
        return jsonify(data), 400

    # 2. Construct the sentence text string
    if data["total_buses"] > 0:
        first_bus = data["buses"][0]
        via_info = f" via {first_bus['via']}" if first_bus['via'] else ""
        speech_text = (
            f"Found {data['total_buses']} buses from {data['from']} to {data['to']}. "
            f"The first bus is an {first_bus['bus_type']} service leaving at {first_bus['departure_time']}{via_info}."
        )
    else:
        speech_text = f"No buses found from {data['from']} to {data['to']} at this moment."

    # 3. Use gTTS to convert the text payload to audio data held in RAM
    tts = gTTS(text=speech_text, lang='en')
    audio_buffer = io.BytesIO()
    tts.write_to_fp(audio_buffer)
    audio_buffer.seek(0)

    # 4. Return it straight back as an MP3 file stream
    return send_file(audio_buffer, mimetype="audio/mp3")


# -------------------------------
# ROOT INDEX ROUTE
# -------------------------------
@app.route("/", methods=["GET"])
def home():
    return "OCR Bus Tracking API is running! Use the endpoint /bus?from=...&to=... to request data."


# -------------------------------
# RUN BACKEND SERVER
# -------------------------------
if __name__ == "__main__":
    app.run(debug=True, port=5000)