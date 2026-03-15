"""
export.py
---------
Saves results to CSV and builds an interactive Folium map.
The map is a self-contained HTML file — open it in any browser, no server needed.
"""

import folium
from folium.plugins import MarkerCluster
import pandas as pd

_CONF_COLOR = {'HIGH': 'green', 'MEDIUM': 'orange', 'LOW': 'red'}


def save_csv(towers: pd.DataFrame, path: str = 'calculated_towers.csv') -> None:
    towers.sort_values('confidence_score', ascending=False).to_csv(path, index=False)
    print(f"  ✅ {path}  ({len(towers):,} towers)")


def build_map(towers: pd.DataFrame, path: str = 'towers_map.html') -> None:
    """Build interactive Folium map with confidence-colored markers."""
    center = [towers['predicted_lat'].median(), towers['predicted_lon'].median()]
    m = folium.Map(location=center, zoom_start=11, tiles='CartoDB positron')

    high   = (towers['confidence'] == 'HIGH').sum()
    medium = (towers['confidence'] == 'MEDIUM').sum()
    low    = (towers['confidence'] == 'LOW').sum()

    layers = {
        'HIGH':   MarkerCluster(name=f'🟢 HIGH ({high:,})',   show=True).add_to(m),
        'MEDIUM': MarkerCluster(name=f'🟠 MEDIUM ({medium:,})', show=True).add_to(m),
        'LOW':    MarkerCluster(name=f'🔴 LOW ({low:,})',     show=False).add_to(m),
    }

    for _, row in towers.iterrows():
        conf  = row['confidence']
        color = _CONF_COLOR.get(conf, 'gray')

        popup_html = f"""
        <div style="font-family:Arial;font-size:13px;min-width:220px">
          <b style="font-size:15px">📡 eNBid {row['enb_id']}</b>
          <hr style="margin:4px 0">
          <b>Confidence:</b> {conf} ({row['confidence_score']:.1f}/100)<br>
          <b>Coordinates:</b> {row['predicted_lat']}, {row['predicted_lon']}<br>
          <b>Measurements:</b> {row['n_measurements']:,}<br>
          <b>Mean residual:</b> {row['mean_residual_m']} m<br>
          <b>Angular spread:</b> {row['angular_spread']}°<br>
          <b>Sectors:</b> {row['unique_sectors']}<br>
          <b>Mean RSRP:</b> {row['mean_rsrp']} dBm<br>
          <hr style="margin:4px 0">
          <a href="https://www.google.com/maps/@{row['predicted_lat']},{row['predicted_lon']},18z/data=!3m1!1e3"
             target="_blank">📍 Open in Google Maps</a>
        </div>"""

        folium.Marker(
            location=[row['predicted_lat'], row['predicted_lon']],
            popup=folium.Popup(popup_html, max_width=280),
            tooltip=f"eNBid {row['enb_id']} | {conf} | n={row['n_measurements']:,}",
            icon=folium.Icon(color=color, icon='signal', prefix='fa'),
        ).add_to(layers[conf])

    folium.LayerControl(collapsed=False).add_to(m)

    # Stats box
    total = len(towers)
    stats_html = f"""
    <div style="position:fixed;bottom:30px;left:30px;z-index:1000;
         background:white;padding:14px 18px;border-radius:8px;
         box-shadow:0 2px 10px rgba(0,0,0,0.2);font-family:Arial;font-size:13px">
      <b style="font-size:14px;color:#1e3a5f">📊 Discovery Results</b>
      <hr style="margin:6px 0;border-color:#2e86ab">
      Towers located: <b>{total:,}</b><br>
      🟢 HIGH: <b>{high:,}</b> &nbsp;
      🟠 MED:  <b>{medium:,}</b> &nbsp;
      🔴 LOW:  <b>{low:,}</b><br>
      Ring residual: ~<b>{int(towers['mean_residual_m'].median())}m</b> median
    </div>"""
    m.get_root().html.add_child(folium.Element(stats_html))

    m.save(path)
    print(f"  ✅ {path}")


def build_validation_map(matched: pd.DataFrame,
                         path: str = 'validation_map.html') -> None:
    """
    Map showing predicted (circle) vs ground-truth (star) for each matched tower.
    Lines connect prediction to actual — color encodes error distance.
    """
    center = [matched['predicted_lat'].median(), matched['predicted_lon'].median()]
    m = folium.Map(location=center, zoom_start=12, tiles='CartoDB positron')

    layers = {
        'HIGH':   folium.FeatureGroup(name='🟢 HIGH — predicted',  show=True).add_to(m),
        'MEDIUM': folium.FeatureGroup(name='🟠 MEDIUM — predicted', show=True).add_to(m),
        'LOW':    folium.FeatureGroup(name='🔴 LOW — predicted',   show=False).add_to(m),
        'gt':     folium.FeatureGroup(name='⭐ Ground truth',       show=True).add_to(m),
        'lines':  folium.FeatureGroup(name='📏 Error lines',        show=True).add_to(m),
    }

    for _, row in matched.iterrows():
        err   = row['error_m']
        conf  = row['confidence']
        color = _CONF_COLOR.get(conf, 'gray')
        line_color = ('#2ecc71' if err < 100 else
                      '#f39c12' if err < 300 else '#e74c3c')

        folium.PolyLine(
            [[row['predicted_lat'], row['predicted_lon']],
             [row['gt_lat'],        row['gt_lon']]],
            color=line_color, weight=1.5, opacity=0.6,
            tooltip=f"Error: {err:.0f} m",
        ).add_to(layers['lines'])

        popup_html = f"""
        <div style="font-family:Arial;font-size:13px;min-width:230px">
          <b>📡 eNBid {row['enb_id']}</b> — {row.get('gt_site_name','?')}
          <hr style="margin:4px 0">
          <b style="color:{'#27ae60' if err<100 else '#e67e22' if err<300 else '#e74c3c'}">
            Error: {err:.0f} m</b><br>
          Confidence: {conf} ({row['confidence_score']:.1f}/100)<br>
          Measurements: {row['n_measurements']:,}<br>
          Ring residual: {row['mean_residual_m']:.0f} m<br>
          <a href="https://www.google.com/maps/@{row['gt_lat']},{row['gt_lon']},18z/data=!3m1!1e3"
             target="_blank">📍 Actual site on Maps</a>
        </div>"""

        folium.CircleMarker(
            location=[row['predicted_lat'], row['predicted_lon']],
            radius=6, color=color, fill=True, fill_color=color, fill_opacity=0.7,
            popup=folium.Popup(popup_html, max_width=280),
            tooltip=f"Predicted | {row['enb_id']} | err={err:.0f}m",
        ).add_to(layers[conf])

        folium.Marker(
            location=[row['gt_lat'], row['gt_lon']],
            icon=folium.Icon(color='blue', icon='star', prefix='fa'),
            tooltip=f"ACTUAL: {row.get('gt_site_name','?')}",
        ).add_to(layers['gt'])

    folium.LayerControl(collapsed=False).add_to(m)
    m.save(path)
    print(f"  ✅ {path}")
