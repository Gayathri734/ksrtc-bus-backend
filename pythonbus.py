import requests
import csv
import json
import time  
import threading  # Clean multi-threaded audio isolation
from html.parser import HTMLParser
from bs4 import BeautifulSoup
import pyttsx3

# Audio processing core
import sounddevice as sd
import scipy.io.wavfile as wav
import speech_recognition as sr

# --- TRICK: GLOBAL SPEAKER THREAD WORKER ---
def speak_isolated(text_payload, rate=145):
    """Creates a completely clean, isolated audio voice window 
    and destroys it immediately after speaking to prevent hardware locks."""
    def worker():
        try:
            worker_engine = pyttsx3.init()
            worker_engine.setProperty('rate', rate)
            worker_engine.say(text_payload)
            worker_engine.runAndWait()
            # Explicitly de-reference to free up COM layers
            del worker_engine 
        except Exception as e:
            print(f"[Speech Engine Warning]: {e}")

    t = threading.Thread(target=worker)
    t.start()
    t.join()  # Hold cleanly until speaking concludes

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
            clean_data = data.strip().replace("\n", "").replace("\t", "").upper()
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
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://ksrtcedp.com/route/index.php",
        "Origin": "https://ksrtcedp.com"
    })

    session.get("https://ksrtcedp.com/route/index.php", timeout=10)

    departure_id = get_value_by_name("departure (1).csv", departure_name)
    if not departure_id:
        raise ValueError(f"Invalid departure name: '{departure_name}'")

    dest_resp = session.get(
        "https://ksrtcedp.com/route/loaddest.php",
        params={"deptid": departure_id},
        timeout=10
    )

    destination_id = get_value_from_html(dest_resp.text, destination_name)
    if not destination_id:
        raise ValueError(f"Invalid destination '{destination_name}' for this departure")

    result_resp = session.post(
        "https://ksrtcedp.com/route/result.php",
        data={"seldept": departure_id, "seldest": destination_id, "submit": "Search"},
        timeout=10
    )

    soup = BeautifulSoup(result_resp.text, "html.parser")
    output = {"from": departure_name, "to": destination_name, "total_buses": 0, "buses": []}
    rows = soup.select("table tbody tr")

    for row in rows:
        cols = row.find_all("td")
        if len(cols) < 5:
            continue
        try:
            dep_block = cols[1]
            departure_time = dep_block.find("i", class_="fa-clock-o").parent.text.strip().split()[0]
            from_place = dep_block.find("span", class_="depthead").text.strip()
            bus_type = cols[2].find("small", class_="typehead").text.strip()
            via_tag = cols[2].find("span", class_="viahead")
            via = via_tag.text.replace("via:", "").strip() if via_tag else None
            arr_block = cols[3]
            arrival_time = arr_block.find("i", class_="fa-clock-o").parent.text.strip().split()[0]
            to_place = arr_block.find("span", class_="depthead").text.strip()

            output["buses"].append({
                "from": from_place, "to": to_place, "departure_time": departure_time,
                "arrival_time": arrival_time, "bus_type": bus_type, "via": via
            })
        except:
            continue

    output["total_buses"] = len(output["buses"])
    return output

# -------------------------------
# STABLE VOICE INPUT FUNCTION
# -------------------------------
def listen_for_location(prompt_text):
    print(f"\n[Bot]: {prompt_text}")
    
    # Speak prompt using our fresh independent worker thread
    speak_isolated(prompt_text)
    time.sleep(0.4)  # Small pause to clear systemic hardware channels
    
    fs = 16000  
    seconds = 4  
    
    print("Listening... Speak now...")
    try:
        myrecording = sd.rec(int(seconds * fs), samplerate=fs, channels=1, dtype='int16')
        sd.wait()  
        print("Processing voice input...")
        
        wav.write('temp_voice.wav', fs, myrecording)
        
        recognizer = sr.Recognizer()
        with sr.AudioFile('temp_voice.wav') as source:
            recognizer.adjust_for_ambient_noise(source, duration=0.1)
            audio_data = recognizer.record(source)
            text = recognizer.recognize_google(audio_data)
            print(f"You said: '{text}'")
            return text.strip().upper()
            
    except Exception:
        print("Voice parsing missed or audio device busy.")
        return None

# -------------------------------
# MAIN APPLICATION MANAGEMENT
# -------------------------------
if __name__ == "__main__":
    print("=== KSRTC Bus Voice Assistant ===")
    
    # 1. Get Departure Route
    departure_input = None
    while not departure_input:
        departure_input = listen_for_location("Please say your departure place.")
        if not departure_input:
            fallback = input("Voice processing failed. Please type departure manually: ")
            if fallback.strip():
                departure_input = fallback.strip().upper()

    # 2. Get Destination Route
    destination_input = None
    while not destination_input:
        prompt = f"Please say your destination from {departure_input}."
        destination_input = listen_for_location(prompt)
        if not destination_input:
            fallback = input("Voice processing failed. Please type destination manually: ")
            if fallback.strip():
                destination_input = fallback.strip().upper()

    # 3. Request Live Data Pipelines
    print(f"\nSearching for buses from {departure_input} to {destination_input}...")
    try:
        data = get_bus_data(departure_input, destination_input)
        
        speech_text = f"Fetching bus details from {data['from']} to {data['to']}. "
        if data["total_buses"] == 0:
            speech_text += "No buses are found active on this system routing."
        else:
            speech_text += f"There are {data['total_buses']} buses available. "
            for bus in data["buses"][:2]:
                via_info = f" via {bus['via']}" if bus['via'] else ""
                speech_text += f"A {bus['bus_type']} bus leaves at {bus['departure_time']}{via_info}. "
        
        print("\n[Speaking Response]...")
        print(speech_text)
        
        # This will fire cleanly because previous engines have been fully destroyed
        speak_isolated(speech_text)

    except Exception as e:
        print(f"Error executing scraper data layer: {e}")
        error_speech = "Sorry, I ran into an error connecting to the live bus schedule information."
        speak_isolated(error_speech)