import streamlit as st
import folium
from streamlit_folium import st_folium
import asyncio
import httpx
import json
import os
from engine import fetch_all_discussions, build_forecaster_prompt

# --- PAGE SETUP ---
st.set_page_config(page_title="Chase Brief Generator", page_icon="⚡", layout="wide")

CONFIG_FILE = "config.json"

DEFAULT_CONFIG = {
    "start_location": "Milliken, CO",
    "chase_objective": "Tornado Dominant (Boundary, Low LCL, SRH + Secondary Salvage)",
    "max_drive_hours": 6,
    "last_selected_cwas": ["BOU", "GLD", "CYS", "LBF"]
}

def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                return {**DEFAULT_CONFIG, **json.load(f)}
        except Exception:
            return DEFAULT_CONFIG
    return DEFAULT_CONFIG

def save_config(cfg):
    try:
        with open(CONFIG_FILE, "w") as f:
            json.dump(cfg, f, indent=2)
    except Exception:
        pass

user_cfg = load_config()

# --- CURATED CHASE CWAs WITH METADATA & CENTROIDS ---
CHASE_CWAS = {
    # High Plains / Rockies Foothills / Great Basin
    "BOU": {"lat": 40.0, "lon": -105.0, "name": "Denver / Boulder, CO"},
    "PUB": {"lat": 38.3, "lon": -104.6, "name": "Pueblo, CO"},
    "CYS": {"lat": 41.1, "lon": -104.8, "name": "Cheyenne, WY"},
    "GLD": {"lat": 39.3, "lon": -101.7, "name": "Goodland, KS"},
    "ABQ": {"lat": 35.0, "lon": -106.6, "name": "Albuquerque, NM"},
    "GJT": {"lat": 39.1, "lon": -108.5, "name": "Grand Junction, CO"},
    "RIW": {"lat": 43.1, "lon": -108.5, "name": "Riverton, WY"},
    "SLC": {"lat": 40.8, "lon": -111.9, "name": "Salt Lake City, UT"},
    "PIH": {"lat": 42.9, "lon": -112.6, "name": "Pocatello / Idaho Falls, ID"},

    # Northern Plains & Northern Rockies
    "BYZ": {"lat": 45.8, "lon": -108.5, "name": "Billings, MT"},
    "GGW": {"lat": 48.2, "lon": -106.6, "name": "Glasgow, MT"},
    "TFX": {"lat": 47.5, "lon": -111.4, "name": "Great Falls, MT"},
    "UNR": {"lat": 44.1, "lon": -103.2, "name": "Rapid City, SD"},
    "ABR": {"lat": 45.4, "lon": -98.4,  "name": "Aberdeen, SD"},
    "FSD": {"lat": 43.6, "lon": -96.7,  "name": "Sioux Falls, SD"},
    "FGF": {"lat": 47.9, "lon": -97.1,  "name": "Eastern ND / Grand Forks, ND"},
    "BIS": {"lat": 46.8, "lon": -100.7, "name": "Bismarck, ND"},

    # Central Plains
    "LBF": {"lat": 41.1, "lon": -100.7, "name": "North Platte, NE"},
    "GID": {"lat": 40.9, "lon": -98.3,  "name": "Hastings, NE"},
    "OAX": {"lat": 41.3, "lon": -96.0,  "name": "Omaha, NE"},
    "DDC": {"lat": 37.8, "lon": -100.0, "name": "Dodge City, KS"},
    "TOP": {"lat": 39.1, "lon": -95.6,  "name": "Topeka, KS"},
    "ICT": {"lat": 37.6, "lon": -97.4,  "name": "Wichita, KS"},

    # Southern Plains & Red River
    "AMA": {"lat": 35.2, "lon": -101.7, "name": "Amarillo, TX"},
    "LUB": {"lat": 33.6, "lon": -101.8, "name": "Lubbock, TX"},
    "MAF": {"lat": 32.0, "lon": -102.2, "name": "Midland / Odessa, TX"},
    "SJT": {"lat": 31.4, "lon": -100.5, "name": "San Angelo, TX"},
    "OUN": {"lat": 35.2, "lon": -97.4,  "name": "Norman / OKC, OK"},
    "TSA": {"lat": 36.2, "lon": -95.9,  "name": "Tulsa, OK"},
    "FWD": {"lat": 32.8, "lon": -97.3,  "name": "Dallas / Fort Worth, TX"},

    # Desert Southwest
    "FGZ": {"lat": 35.2, "lon": -111.8, "name": "Flagstaff, AZ"},
    "TWC": {"lat": 32.2, "lon": -110.9, "name": "Tucson, AZ"},
    "PSR": {"lat": 33.4, "lon": -112.0, "name": "Phoenix, AZ"},
    "EPZ": {"lat": 31.8, "lon": -106.4, "name": "El Paso, TX"},

    # Texas Coastal / South
    "EWX": {"lat": 29.7, "lon": -98.0,  "name": "Austin / San Antonio, TX"},
    "HGX": {"lat": 29.5, "lon": -95.1,  "name": "Houston / Galveston, TX"},
    "CRP": {"lat": 27.8, "lon": -97.5,  "name": "Corpus Christi, TX"},
    "BRO": {"lat": 25.9, "lon": -97.4,  "name": "Brownsville, TX"},

    # Upper Midwest / Great Lakes / Corn Belt
    "MPX": {"lat": 44.8, "lon": -93.6,  "name": "Twin Cities / Chanhassen, MN"},
    "ARX": {"lat": 43.8, "lon": -91.2,  "name": "La Crosse, WI"},
    "DMX": {"lat": 41.7, "lon": -93.7,  "name": "Des Moines, IA"},
    "DVN": {"lat": 41.6, "lon": -90.6,  "name": "Quad Cities / Davenport, IA"},
    "LOT": {"lat": 41.6, "lon": -88.1,  "name": "Chicago, IL"},
    "ILX": {"lat": 40.1, "lon": -89.3,  "name": "Central Illinois / Lincoln, IL"},
    "MKX": {"lat": 43.0, "lon": -88.5,  "name": "Milwaukee, WI"},
    "GRB": {"lat": 44.5, "lon": -88.1,  "name": "Green Bay, WI"},
    "GRR": {"lat": 42.9, "lon": -85.5,  "name": "Grand Rapids, MI"},

    # Mid-Mississippi / Ohio Valley
    "EAX": {"lat": 38.8, "lon": -94.3,  "name": "Kansas City / Pleasant Hill, MO"},
    "SGF": {"lat": 37.2, "lon": -93.4,  "name": "Springfield, MO"},
    "LSX": {"lat": 38.7, "lon": -90.7,  "name": "St. Louis, MO"},
    "PAH": {"lat": 37.1, "lon": -88.8,  "name": "Paducah, KY"},
    "ILN": {"lat": 39.4, "lon": -83.8,  "name": "Wilmington / Cincinnati, OH"},
    "IND": {"lat": 39.7, "lon": -86.3,  "name": "Indianapolis, IN"},
    "IWX": {"lat": 41.4, "lon": -85.2,  "name": "Northern Indiana, IN"},
    "LMK": {"lat": 38.1, "lon": -85.7,  "name": "Louisville, KY"},
    "JKL": {"lat": 37.6, "lon": -83.3,  "name": "Jackson, KY"},
    "CLE": {"lat": 41.4, "lon": -81.8,  "name": "Cleveland, OH"},

    # Mid-South / Delta / Southeast Fringe
    "LZK": {"lat": 34.8, "lon": -92.3,  "name": "Little Rock, AR"},
    "SHV": {"lat": 32.4, "lon": -93.8,  "name": "Shreveport, LA"},
    "LCH": {"lat": 30.1, "lon": -93.2,  "name": "Lake Charles, LA"},
    "MEG": {"lat": 35.0, "lon": -89.9,  "name": "Memphis, TN"},
    "JAN": {"lat": 32.3, "lon": -90.1,  "name": "Jackson, MS"},
    "LIX": {"lat": 30.3, "lon": -89.8,  "name": "New Orleans / Slidell, LA"},
    "MOB": {"lat": 30.7, "lon": -88.2,  "name": "Mobile, AL"},
    "HUN": {"lat": 34.7, "lon": -86.8,  "name": "Huntsville, AL"},
    "BMX": {"lat": 33.2, "lon": -86.8,  "name": "Birmingham, AL"},
    "OHX": {"lat": 36.2, "lon": -86.6,  "name": "Nashville, TN"},
    "MRX": {"lat": 36.2, "lon": -83.4,  "name": "Morristown / Knoxville, TN"}
}

HOME_BASE_CWAS = ["BOU", "PUB", "GJT", "CYS", "LBF", "GLD", "DDC"]

SPC_COLORS = {
    "TSTM": "#c1e9c1",
    "MRGL": "#66a366",
    "SLGT": "#ffe066",
    "ENH":  "#ffa500",
    "MDT":  "#e60000",
    "HIGH": "#ff00ff"
}

OFFICE_OPTIONS = [f"{cwa} — {data['name']}" for cwa, data in CHASE_CWAS.items()]
CWA_TO_OPTION = {cwa: f"{cwa} — {data['name']}" for cwa, data in CHASE_CWAS.items()}
OPTION_TO_CWA = {f"{cwa} — {data['name']}": cwa for cwa, data in CHASE_CWAS.items()}

# --- NOAA ARCGIS DATA FETCHERS ---

@st.cache_data(ttl=900)
def fetch_spc_geojson(layer_id: int):
    """Queries NOAA's ArcGIS FeatureServer for real-time SPC outlook polygons."""
    url = f"https://mapservices.weather.noaa.gov/vector/rest/services/outlooks/SPC_wx_outlks/FeatureServer/{layer_id}/query"
    params = {"where": "1=1", "outFields": "*", "f": "geojson", "outSR": "4326"}
    headers = {"User-Agent": "(ChaseBriefGenerator/1.6, contact: stormbriefs@weatherops.org)"}
    try:
        r = httpx.get(url, params=params, headers=headers, timeout=7.0)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return None

@st.cache_data(ttl=604800)
def fetch_cwa_boundaries():
    """Queries official NOAA NWS Reference MapServer for CWA polygons."""
    url = "https://mapservices.weather.noaa.gov/static/rest/services/nws_reference_maps/nws_reference_map/FeatureServer/1/query"
    params = {"where": "1=1", "outFields": "cwa,wfo", "f": "geojson", "outSR": "4326"}
    headers = {"User-Agent": "(ChaseBriefGenerator/1.6, contact: stormbriefs@weatherops.org)"}
    try:
        r = httpx.get(url, params=params, headers=headers, timeout=12.0)
        if r.status_code == 200:
            full_data = r.json()
            filtered_features = []
            for feat in full_data.get("features", []):
                props = feat.get("properties", {})
                cwa_code = str(props.get("cwa") or props.get("CWA") or props.get("wfo") or "").upper()
                if cwa_code in CHASE_CWAS:
                    props["CWA"] = cwa_code
                    filtered_features.append(feat)
            return {"type": "FeatureCollection", "features": filtered_features}
    except Exception:
        pass
    return None

def point_in_polygon(lat, lon, polygon):
    """Ray casting algorithm to determine if a point is inside a polygon."""
    inside = False
    n = len(polygon)
    p1x, p1y = polygon[0]
    for i in range(n + 1):
        p2x, p2y = polygon[i % n]
        if min(p1y, p2y) < lat <= max(p1y, p2y):
            if lon <= max(p1x, p2x):
                if p1y != p2y:
                    xinters = (lat - p1y) * (p2x - p1x) / (p2y - p1y) + p1x
                if p1x == p2x or lon <= xinters:
                    inside = not inside
        p1x, p1y = p2x, p2y
    return inside

def auto_detect_cwas_in_risk(spc_geojson, max_limit=10):
    """Detects which chase CWAs fall within non-general-thunder SPC polygons."""
    if not spc_geojson or not spc_geojson.get("features"):
        return []
    
    matching_cwas = set()
    for feat in spc_geojson["features"]:
        props = feat.get("properties", {})
        label = str(props.get("label2") or props.get("LABEL2") or props.get("label") or "").upper()
        if "TSTM" in label or label == "":
            continue
            
        geom = feat.get("geometry", {})
        coords = geom.get("coordinates", [])
        gtype = geom.get("type", "")
        
        polygons = []
        if gtype == "Polygon":
            polygons = coords
        elif gtype == "MultiPolygon":
            for poly in coords:
                polygons.extend(poly)
                
        for ring in polygons:
            for cwa, data in CHASE_CWAS.items():
                if point_in_polygon(data["lat"], data["lon"], ring):
                    matching_cwas.add(cwa)
                    
    return list(matching_cwas)[:max_limit]

# --- SESSION STATE INITIALIZATION ---
if "applied_cwas" not in st.session_state:
    st.session_state.applied_cwas = user_cfg.get("last_selected_cwas", ["BOU", "GLD", "CYS", "LBF"])
if "cached_data" not in st.session_state:
    st.session_state.cached_data = None
if "generated_prompt" not in st.session_state:
    st.session_state.generated_prompt = ""

# --- SIDEBAR: MISSION PARAMETERS ---
with st.sidebar:
    st.title("⚡ Chase Brief Setup")
    
    mode = st.radio(
        "Operational Mode",
        options=[
            "Mode 1: Day-Of (Nowcast / Morning Launch)",
            "Mode 2: Day-Before (Corridor Strategy)",
            "Mode 3: Extended (Days 3-8 Synoptic)",
            "Mode 4: Home-Base Synopsis (Colorado & Regional)"
        ],
        index=0
    )
    
    st.divider()
    
    if "Home-Base" in mode:
        start_location = st.text_input("Home Location", value=user_cfg.get("start_location", "Milliken, CO"))
        chase_objective = "General Regional Weather & Hazards"
        max_drive_hours = 0
        max_allowed_cwas = len(HOME_BASE_CWAS)
        if set(st.session_state.applied_cwas) != set(HOME_BASE_CWAS):
            st.session_state.applied_cwas = HOME_BASE_CWAS.copy()
            st.rerun()
    else:
        start_location = st.text_input("Starting Location", value=user_cfg.get("start_location", "Milliken, CO"))
        
        obj_options = [
            "Tornado Dominant (Boundary, Low LCL, SRH + Secondary Salvage)",
            "Severe Production / Video Potential (Extreme Hail, Outflow, Visuals)",
            "High Plains Photogenic / Structure (Clean LP/Classic Storm Bases)"
        ]
        saved_obj_idx = obj_options.index(user_cfg["chase_objective"]) if user_cfg["chase_objective"] in obj_options else 0
        chase_objective = st.selectbox("Chase Objective", options=obj_options, index=saved_obj_idx)
        
        max_drive_hours = st.slider("Max Travel Window", 2, 14, user_cfg.get("max_drive_hours", 6), format="%d hrs")
        max_allowed_cwas = 4 if "Extended" in mode else 10

    st.divider()
    st.subheader("Map Threat Overlays")
    spc_layer_choice = st.selectbox(
        "SPC Risk Layer",
        options=["Categorical", "Tornado (Day 1 Only)", "Hail (Day 1 Only)", "Wind (Day 1 Only)"],
        index=0
    )
    
    if st.button("💾 Save Profile As Default", use_container_width=True):
        user_cfg["start_location"] = start_location
        user_cfg["chase_objective"] = chase_objective
        user_cfg["max_drive_hours"] = max_drive_hours
        user_cfg["last_selected_cwas"] = st.session_state.applied_cwas
        save_config(user_cfg)
        st.toast("Profile settings saved!", icon="💾")

# --- MAIN UI: SELECTION MAP & BATCH SELECTION ---
st.subheader("Target Selection & Threat Map")

# Determine NOAA FeatureServer Layer ID
if "Day-Before" in mode:
    layer_id = 9
elif "Extended" in mode:
    layer_id = 17
else:
    if "Tornado" in spc_layer_choice:
        layer_id = 3
    elif "Hail" in spc_layer_choice:
        layer_id = 5
    elif "Wind" in spc_layer_choice:
        layer_id = 7
    else:
        layer_id = 1

spc_data = fetch_spc_geojson(layer_id)

# Mobile-Optimized Manual Target Control Bar
col_auto, col_clear, col_spacer = st.columns([1.5, 1, 3.5])
with col_auto:
    if st.button("⚡ Auto-Select in Risk", use_container_width=True):
        auto_cwas = auto_detect_cwas_in_risk(spc_data, max_limit=max_allowed_cwas)
        if auto_cwas:
            st.session_state.applied_cwas = auto_cwas
            st.rerun()
        else:
            st.toast("No threat areas (Marginal+) detected covering chase CWAs.", icon="ℹ️")

with col_clear:
    if st.button("Clear All", use_container_width=True):
        st.session_state.applied_cwas = []
        st.rerun()

# 1. Multi-Select Target Input (Queue your selections without auto-reloading)
initial_dropdown_selection = [CWA_TO_OPTION[c] for c in st.session_state.applied_cwas if c in CWA_TO_OPTION]

col_select, col_apply = st.columns([4, 1.5])

with col_select:
    st_selected_options = st.multiselect(
        f"Select Target CWAs (Max: {max_allowed_cwas})",
        options=OFFICE_OPTIONS,
        default=initial_dropdown_selection,
        max_selections=max_allowed_cwas,
        key="target_office_multiselect",
        help="Select or search offices. Tap 'Update Map' when ready to apply."
    )

# Extract CWA codes from multi-select
queued_cwas = [OPTION_TO_CWA[opt] for opt in st_selected_options]

with col_apply:
    st.write("")  # Alignment spacing
    st.write("")
    if st.button("🔄 Update Map", type="secondary", use_container_width=True):
        st.session_state.applied_cwas = queued_cwas
        st.rerun()

# Active Target Chip Readout
if st.session_state.applied_cwas:
    st.write("**Currently Active:** " + " ".join([f"`{w}`" for w in st.session_state.applied_cwas]))
else:
    st.warning("No offices currently active. Choose offices above and tap 'Update Map'.")

# Build Base Map
m = folium.Map(
    location=[39.5, -100.0],
    zoom_start=5,
    min_zoom=4,
    max_zoom=9,
    tiles="OpenStreetMap"
)

# 2. Overlay Live SPC Polygons
if spc_data and spc_data.get("features"):
    first_props = spc_data["features"][0].get("properties", {})
    tooltip_field = "label2" if "label2" in first_props else ("LABEL2" if "LABEL2" in first_props else "label")
    
    def spc_style(feature):
        props = feature.get("properties", {})
        label = str(props.get("label2") or props.get("LABEL2") or props.get("label") or "").upper()
        fill_color = props.get("fill") or "#3388ff"
        for k, hex_code in SPC_COLORS.items():
            if k in label:
                fill_color = hex_code
                break
        return {"fillColor": fill_color, "color": props.get("stroke") or "#222222", "weight": 1.2, "fillOpacity": 0.40}
        
    folium.GeoJson(
        spc_data,
        style_function=spc_style,
        tooltip=folium.GeoJsonTooltip(fields=[tooltip_field], aliases=["Threat Level:"], localize=True),
        name="SPC Convective Outlook"
    ).add_to(m)

# 3. Overlay NWS CWA County Boundaries
cwa_boundaries = fetch_cwa_boundaries()
if cwa_boundaries and cwa_boundaries.get("features"):
    def cwa_style(feature):
        props = feature.get("properties", {})
        cwa_code = props.get("CWA", "")
        is_sel = cwa_code in st.session_state.applied_cwas
        return {
            "fillColor": "#e53935" if is_sel else "#607d8b",
            "color": "#b71c1c" if is_sel else "#37474f",
            "weight": 2.2 if is_sel else 1.2,
            "fillOpacity": 0.35 if is_sel else 0.05
        }
    
    folium.GeoJson(
        cwa_boundaries,
        style_function=cwa_style,
        tooltip=folium.GeoJsonTooltip(fields=["CWA"], aliases=["NWS Office:"], localize=True),
        name="NWS Boundaries"
    ).add_to(m)

# 4. Permanent Office Centroid Markers (rendered on top)
for cwa, data in CHASE_CWAS.items():
    is_sel = cwa in st.session_state.applied_cwas
    folium.CircleMarker(
        location=[data["lat"], data["lon"]],
        radius=7 if is_sel else 4,
        color="#000000",
        weight=1.5 if is_sel else 1.0,
        fill=True,
        fill_color="#e53935" if is_sel else "#1e88e5",
        fill_opacity=1.0 if is_sel else 0.7,
        tooltip=f"{cwa} — {data['name']}"
    ).add_to(m)

# Notice returned_objects=[] -> Completely eliminates map reloads on mobile screen touches
st_folium(m, height=540, use_container_width=True, returned_objects=[])

# --- COMPILE BRIEFING ---
st.divider()
if st.button("🚀 Fetch Text & Assemble Prompt", type="primary", use_container_width=True):
    if not st.session_state.applied_cwas:
        st.error("No offices selected. Please choose offices and click 'Update Map'.")
    else:
        with st.spinner("Fetching NWS discussions and SPC outlook text in parallel..."):
            data = asyncio.run(fetch_all_discussions(st.session_state.applied_cwas, mode))
            st.session_state.cached_data = data
            st.session_state.generated_prompt = build_forecaster_prompt(
                data=data,
                mode=mode,
                origin=start_location,
                objective=chase_objective,
                max_drive_hours=max_drive_hours
            )

# --- PROMPT DISPLAY & CLIPBOARD COMPONENT ---
if st.session_state.generated_prompt:
    st.success("Prompt assembled! Click below to copy or review:")
    
    if st.session_state.cached_data and "offices" in st.session_state.cached_data:
        cols = st.columns(len(st.session_state.cached_data["offices"]))
        for i, (wfo, info) in enumerate(st.session_state.cached_data["offices"].items()):
            with cols[i]:
                st.metric(label=f"WFO {wfo}", value=info["age"])
    
    copy_code = f"""
    <script>
    function copyPromptText() {{
        const textToCopy = `{st.session_state.generated_prompt.replace('`', '\\`')}`;
        navigator.clipboard.writeText(textToCopy).then(() => {{
            const btn = document.getElementById("copyBtn");
            btn.innerText = "✅ Copied to Clipboard!";
            setTimeout(() => {{ btn.innerText = "📋 Copy Prompt to Clipboard"; }}, 2500);
        }});
    }}
    </script>
    <button id="copyBtn" onclick="copyPromptText()" style="
        background-color: #2e7d32;
        color: white;
        border: none;
        padding: 10px 20px;
        text-align: center;
        text-decoration: none;
        display: inline-block;
        font-size: 15px;
        font-weight: bold;
        margin: 8px 2px;
        cursor: pointer;
        border-radius: 6px;
        width: 100%;">
        📋 Copy Prompt to Clipboard
    </button>
    """
    st.components.v1.html(copy_code, height=60)
    
    with st.expander("Review Assembled Prompt Text", expanded=True):
        st.text_area("Full Prompt Payload", value=st.session_state.generated_prompt, height=350)
