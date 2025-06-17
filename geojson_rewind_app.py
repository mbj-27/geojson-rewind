
import streamlit as st
# ✅ This must be the first Streamlit command
st.set_page_config(layout="wide", page_title="GeoJSON Rewinder")


import json
import os
import zipfile
from io import BytesIO
import tempfile
from geojson_rewind import rewind
import folium
from streamlit_folium import st_folium
from shapely.geometry import shape
from shapely.validation import explain_validity

# --- Theme Toggle ---
theme = st.sidebar.selectbox("Choose Theme", ["Light", "Dark"])
if theme == "Dark":
    st.markdown(
        """<style>
            body { background-color: #0e1117; color: white; }
            .stApp { background-color: #0e1117; }
            .css-1v0mbdj { background-color: #262730; }
            .css-1d391kg { color: white; }
        </style>""",
        unsafe_allow_html=True
    )

st.title("GeoJSON Winding Order Check/Change")

st.sidebar.header("Settings")
mode = st.sidebar.radio("Mode", ["Single File", "Batch Upload (ZIP)"])
desired_winding = st.sidebar.radio("Desired Winding", ["Counterclockwise", "Clockwise"])
force_ccw = desired_winding == "Counterclockwise"


def calculate_signed_area(coords):
    x, y = zip(*coords)
    return 0.5 * sum(x[i]*y[i+1] - x[i+1]*y[i] for i in range(len(coords) - 1))

def check_winding_and_geometry(geojson):
    results = []
    for feature in geojson.get("features", []):
        geom = feature.get("geometry", {})
        props = feature.get("properties", {})
        valid = True
        validity_msg = "Valid"
        stats = {}
        try:
            shp = shape(geom)
            valid = shp.is_valid
            if not valid:
                validity_msg = explain_validity(shp)
            stats["area"] = shp.area
            stats["length"] = shp.length
        except Exception as e:
            valid = False
            validity_msg = str(e)

        rings = []
        if geom["type"] == "Polygon":
            rings = geom["coordinates"]
        elif geom["type"] == "MultiPolygon":
            for p in geom["coordinates"]:
                rings.extend(p)

        for ring in rings:
            try:
                signed_area = calculate_signed_area(ring)
                winding = "Counterclockwise" if signed_area > 0 else "Clockwise"
                results.append({
                    "properties": props,
                    "winding": winding,
                    "valid": valid,
                    "validity_msg": validity_msg,
                    "area": stats.get("area", 0),
                    "length": stats.get("length", 0)
                })
            except:
                results.append({
                    "properties": props,
                    "winding": "Unknown",
                    "valid": False,
                    "validity_msg": "Error calculating area",
                    "area": 0,
                    "length": 0
                })
    return results

def styled_geojson_layer(geojson, results):
    fmap = folium.Map(location=[0, 0], zoom_start=2)
    for idx, feature in enumerate(geojson["features"]):
        ring_info = check_winding_and_geometry(
            {"features": [feature], "type": "FeatureCollection"}
        )[0]
        winding = ring_info["winding"]
        popup_content = (
            f"<strong>Winding:</strong> {winding}<br>"
            f"<strong>Area:</strong> {ring_info['area']:.2f}<br>"
            f"<strong>Length:</strong> {ring_info['length']:.2f}<br>"
            f"<strong>Validity:</strong> {ring_info['validity_msg']}<br>"
            f"<pre>{json.dumps(feature['properties'], indent=2)}</pre>"
        )
        color = "green" if winding == "Counterclockwise" else "red"
        folium.GeoJson(
            feature,
            name=f"Feature {idx+1}",
            style_function=lambda x, color=color: {
                "color": color,
                "weight": 2,
                "fillOpacity": 0.3
            },
            tooltip=f"Winding: {winding}",
            popup=folium.Popup(popup_content, max_width=400)
        ).add_to(fmap)
    return fmap

if mode == "Single File":
    file = st.file_uploader("Upload a GeoJSON file", type="geojson")
    if file:
        geojson_data = json.loads(file.read().decode("utf-8"))
        results = check_winding_and_geometry(geojson_data)
        clockwise_count = sum(1 for r in results if r["winding"] == "Clockwise")
        invalid_count = sum(1 for r in results if not r["valid"])
        st.info(f"🧭 Checked {len(results)} rings: {clockwise_count} clockwise, {invalid_count} invalid.")
        with st.expander("🗺 Interactive Map Preview"):
            fmap = styled_geojson_layer(geojson_data, results)
            st_folium(fmap, height=500)
        if (force_ccw and clockwise_count > 0) or (not force_ccw and clockwise_count < len(results)):
            if st.button("Fix Winding and Download"):
                corrected = rewind(geojson_data, rfc7946=force_ccw)
                st.success("✅ Winding order corrected.")
                st.download_button("Download Corrected GeoJSON",
                                   json.dumps(corrected, indent=2),
                                   file_name="corrected.geojson",
                                   mime="application/geo+json")
        else:
            st.success("✅ All rings already match desired winding.")

if mode == "Batch Upload (ZIP)":
    zip_file = st.file_uploader("Upload ZIP containing .geojson files", type="zip")
    if zip_file:
        with tempfile.TemporaryDirectory() as tmpdir:
            with zipfile.ZipFile(zip_file, 'r') as z:
                z.extractall(tmpdir)
            corrected_files = []
            log = []
            for root, _, files in os.walk(tmpdir):
                for fname in files:
                    if fname.lower().endswith(".geojson"):
                        path = os.path.join(root, fname)
                        with open(path, "r", encoding="utf-8") as f:
                            try:
                                data = json.load(f)
                                results = check_winding_and_geometry(data)
                                clockwise_count = sum(1 for r in results if r["winding"] == "Clockwise")
                                invalid_count = sum(1 for r in results if not r["valid"])
                                should_rewind = (force_ccw and clockwise_count > 0) or (not force_ccw and clockwise_count < len(results))
                                if should_rewind:
                                    data = rewind(data, rfc7946=force_ccw)
                                out_str = json.dumps(data, indent=2)
                                corrected_files.append((fname.replace(".geojson", f"_{desired_winding}.geojson"), out_str))
                                log.append(f"{fname}: {clockwise_count} clockwise, {invalid_count} invalid, {'rewound' if should_rewind else 'unchanged'}")
                            except Exception as e:
                                log.append(f"{fname}: ERROR - {e}")
            zip_buffer = BytesIO()
            with zipfile.ZipFile(zip_buffer, "w") as zf:
                for name, content in corrected_files:
                    zf.writestr(name, content)
                zf.writestr("processing_log.txt", "\n".join(log))
            zip_buffer.seek(0)
            st.success(f"{len(corrected_files)} files processed.")
            st.download_button("Download Corrected Files (ZIP)",
                               zip_buffer,
                               file_name="corrected_geojsons.zip",
                               mime="application/zip")
            with st.expander("📝 Processing Log"):
                st.text("\n".join(log))
