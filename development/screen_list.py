"""The pages capture_screens.py photographs. Edit here to add a view.

Each entry: name, url, optional wait (s after load), steps (JS strings to run, numbers = seconds to
pause), full (full-page vs viewport), size (w, h).
"""
M, P, C, T = "http://localhost:8000", "http://localhost:5001", "http://localhost:5000", "http://localhost:5050"


def click(sel):
    return f"document.querySelector({sel!r}).click()"


def plant_view(v):
    return {"name": f"master_plant_view_{v}", "url": M + "/", "wait": 6, "full": False,
            "steps": [click(f"#views button[data-v={v}]"), 3]}


SHOTS = [
    {"name": "master_plant_3d", "url": M + "/", "wait": 8, "full": False,
     "steps": ["document.querySelectorAll('#stations .st')[1].click()", 2]},
    plant_view("camera"), plant_view("acoustic"), plant_view("gantry"), plant_view("bins"), plant_view("master"),
    {"name": "master_open_guis_menu", "url": M + "/", "wait": 5, "full": False,
     "steps": ["document.getElementById('guisMenu').setAttribute('open','')", 1]},
    {"name": "master_analysis", "url": M + "/analysis", "wait": 6},
    {"name": "master_troubleshoot", "url": M + "/troubleshoot", "wait": 6},
    {"name": "master_control_panel_nodes", "url": M + "/nodes", "wait": 6},
    {"name": "master_insights_ai_error", "url": M + "/insights", "wait": 5,
     "steps": [click("#explain"), 6]},
    {"name": "master_pi_record_tiles", "url": M + "/tiles", "wait": 8},
    {"name": "master_pi_record_tile_detail", "url": M + "/tiles", "wait": 8,
     "steps": ["document.querySelector('#rows tr').click()", 4]},
    {"name": "master_pi_record_integrity", "url": M + "/tiles", "wait": 8,
     "steps": [click("#reconcile"), 5]},
    {"name": "master_classic", "url": M + "/classic", "wait": 6},
    {"name": "station_run", "url": P + "/", "wait": 8},
    {"name": "station_service", "url": P + "/service", "wait": 8},
    {"name": "camera_dashboard", "url": C + "/", "wait": 25, "max_h": 1450},
    {"name": "review_sheet_labelling_html", "url": "file:///C:/Users/MithunvelKL/PycharmProjects/Tile_Sorting/data/review/review_sheet_2026-10-11_15-39-20.html", "wait": 6, "max_h": 2200},
    {"name": "pickplace_twin_idle", "url": T + "/", "wait": 6, "full": False},
    {"name": "pickplace_twin_running_jobs", "url": T + "/", "wait": 4, "full": False,
     "steps": [click("#act-home"), 25, click("#mode-jobs"), 30]},
    {"name": "pickplace_twin_top_view", "url": T + "/", "wait": 4, "full": False,
     "steps": [click("[data-view=top]"), 3]},
]
