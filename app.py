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

# --- CURATED CHASE CWAs WITH APPROXIMATE CENTROIDS (LAT, LON) ---
CHASE_CWAS = {
    # High Plains / Rockies Foothills / Great Basin
    "BOU": (40.0, -105.0), "PUB": (38.3, -104.6), "CYS": (41.1, -104.8), "GLD": (39.3, -101.7), "ABQ": (35.0, -106.6),
    "GJT": (39.1, -108.5), "RIW": (43.1, -108.5), "SLC": (40.8, -111.9), "PIH": (42.9, -112.6),
    # Northern Plains & Northern Rockies
    "BYZ": (45.8, -108.5), "GGW": (48.2, -106.6), "TFX": (47.5, -111.4),
    "UNR": (44.1, -103.2), "ABR": (45.4, -98.4),  "FSD": (43.6, -96.7),  "FGF": (47.9, -97.1),  "BIS": (46.8, -100.7),
    # Central Plains
    "LBF": (41.1, -100.7), "GID": (40.9, -98.3),  "OAX": (41.3, -96.0),  "DDC": (37.8, -100.0), "TOP": (39.1, -95.6), "ICT": (37.6, -97.4),
    # Southern Plains & Red River
    "AMA": (35.2, -101.7), "LUB": (33.6, -101.8), "MAF": (32.0, -102.2), "SJT": (31.4, -100.5), "OUN": (35.2, -97.4),
    "TSA": (36.2, -95.9),  "FWD": (32.8, -97.3),
    # Desert Southwest
    "FGZ": (35.2, -111.8), "TWC": (32.2, -110.9), "PSR": (33.4, -112.0), "EPZ": (31.8, -106.4),
    # Texas Coastal / South
    "EWX": (29.7, -98.0),  "HGX": (29.5, -95.1),  "CRP": (27.8, -97.5),  "BRO": (25.9, -97.4),
    # Upper Midwest / Great Lakes / Corn Belt
    "MPX": (44.8, -93.6),  "ARX": (43.8, -91.2),  "DMX": (41.7, -93.7),  "DVN": (41.6, -90.6),  "LOT": (41.6, -88.1),
    "ILX": (40.1, -89.3),  "MKX": (43.0, -88.5),  "GRB": (44.5, -88.1),  "GRR": (42.9, -85.5),
    # Mid-Mississippi / Ohio Valley
    "EAX": (38.8, -94.3),  "SGF": (37.2, -93.4),  "LSX": (38.7, -90.7),  "PAH": (37.1, -88.8),  "ILN": (39.4, -83.8),
    "IND": (39.7, -86.3),  "IWX": (41.4, -85.2),  "LMK": (38.1, -85.7),  "JKL": (37.6, -83.3),  "CLE": (41.4, -81.8),
    # Mid-South / Delta / Southeast Fringe
    "LZK": (34.8, -92.3),  "SHV": (32.4, -93.8),  "LCH": (30.1, -93.2),  "MEG": (35.0, -89.9),  "JAN": (32.3, -90.1),
    "LIX": (30.3, -89.8),  "MOB": (30.7, -88.2),  "HUN": (34.7, -86.8),  "BMX": (33.2, -86.8),  "OHX": (36.2, -86.6),
    "MRX": (36.2, -83.4)
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

# --- NOAA ARCGIS DATA FETCHERS ---

@st.cache_data(ttl=900)
def fetch_spc_geojson(layer_id: int):
    """Queries NOAA's ArcGIS FeatureServer for real-time SPC outlook polygons."""
    url = f"https://mapservices.weather.noaa.gov/vector/rest/services/outlooks/SPC_wx_outlks/FeatureServer/{layer_id}/query"
    params = {"where": "1=1", "outFields": "*", "f": "geojson", "outSR": "4326"}
    headers = {"User-Agent": "(ChaseBriefGenerator/1.3, contact: stormbriefs@weatherops.org)"}
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
    params = {
        "where": "1=1",
        "outFields": "cwa,wfo",
        "f": "geojson",
        "outSR": "4326"
    }
    headers = {"User-Agent": "(ChaseBriefGenerator/1.3, contact: stormbriefs@weatherops.org)"}
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
            for cwa, (lat, lon) in CHASE_CWAS.items():
                if point_in_polygon(lat, lon, ring):
                    matching_cwas.add(cwa)
                    
    return list(matching_cwas)[:max_limit]

# --- SESSION STATE INITIALIZATION ---
if "selected_cwas" not in st.session_state:
    st.session_state.selected_cwas = user_cfg.get("last_selected_cwas", ["BOU", "GLD", "CYS", "LBF"])
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
        if set(st.session_state.selected_cwas) != set(HOME_BASE_CWAS):
            st.session_state.selected_cwas = HOME_BASE_CWAS.copy()
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
        user_cfg["last_selected_cwas"] = st.session_state.selected_cwas
        save_config(user_cfg)
        st.toast("Profile settings saved!", icon="💾")

# --- MAIN UI: SELECTION MAP ---
st.subheader("Interactive Office Selection & Threat Map")
st.caption(f"Click pins or anywhere within an office territory to toggle. Max allowed: {max_allowed_cwas}. Selected: {len(st.session_state.selected_cwas)}/{max_allowed_cwas}")

# Quick action bar
col_actions, col_auto, col_spacer = st.columns([1, 2, 3])
with col_actions:
    if st.button("Clear All", use_container_width=True):
        st.session_state.selected_cwas = []
        st.rerun()

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

with col_auto:
    if st.button("⚡ Auto-Select CWAs in Risk Area", use_container_width=True):
        auto_cwas = auto_detect_cwas_in_risk(spc_data, max_limit=max_allowed_cwas)
        if auto_cwas:
            st.session_state.selected_cwas = auto_cwas
            st.rerun()
        else:
            st.toast("No threat areas (Marginal+) detected covering chase CWAs.", icon="ℹ️")

# Build Base Map
m = folium.Map(
    location=[39.5, -100.0],
    zoom_start=5,
    min_zoom=4,
    max_zoom=9,
    tiles="OpenStreetMap"
)

# 1. Overlay Live SPC Polygons
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

# 2. Overlay NWS CWA County Boundaries
cwa_boundaries = fetch_cwa_boundaries()
if cwa_boundaries and cwa_boundaries.get("features"):
    def cwa_style(feature):
        props = feature.get("properties", {})
        cwa_code = props.get("CWA", "")
        is_sel = cwa_code in st.session_state.selected_cwas
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

# 3. Permanent Office Centroid Markers (rendered on top)
for cwa, (lat, lon) in CHASE_CWAS.items():
    is_sel = cwa in st.session_state.selected_cwas
    folium.CircleMarker(
        location=[lat, lon],
        radius=7 if is_sel else 4,
        color="#000000",
        weight=1.5 if is_sel else 1.0,
        fill=True,
        fill_color="#e53935" if is_sel else "#1e88e5",
        fill_opacity=1.0 if is_sel else 0.7,
        tooltip=f"WFO {cwa} (Click to toggle)"
    ).add_to(m)

map_state = st_folium(m, height=560, use_container_width=True, returned_objects=["last_clicked"])

# Handle Map Click Toggle
if map_state and map_state.get("last_clicked"):
    click_lat = map_state["last_clicked"]["lat"]
    click_lon = map_state["last_clicked"]["lng"]
    
    closest_cwa = None
    min_dist = 2.4
    for cwa, (lat, lon) in CHASE_CWAS.items():
        dist = ((lat - click_lat)**2 + (lon - click_lon)**2)**0.5
        if dist < min_dist:
            min_dist = dist
            closest_cwa = cwa
            
    if closest_cwa:
        if closest_cwa in st.session_state.selected_cwas:
            st.session_state.selected_cwas.remove(closest_cwa)
            st.rerun()
        elif len(st.session_state.selected_cwas) < max_allowed_cwas:
            st.session_state.selected_cwas.append(closest_cwa)
            st.rerun()
        else:
            st.toast(f"Limit of {max_allowed_cwas} offices reached.", icon="⚠️")

# Display Active Office Chips
if st.session_state.selected_cwas:
    st.write("**Active Offices:** " + " ".join([f"`{w}`" for w in st.session_state.selected_cwas]))
else:
    st.warning("Please select at least one office on the map above.")

# --- COMPILE BRIEFING ---
st.divider()
if st.button("🚀 Fetch Text & Assemble Prompt", type="primary", use_container_width=True):
    if not st.session_state.selected_cwas:
        st.error("No offices selected.")
    else:
        with st.spinner("Fetching NWS discussions and SPC outlook text in parallel..."):
            data = asyncio.run(fetch_all_discussions(st.session_state.selected_cwas, mode))
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
