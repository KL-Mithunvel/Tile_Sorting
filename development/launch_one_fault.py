"""Demo master whose AI-insights data has exactly ONE planted fault (reject_rate, hold_rate, timeouts, agreement, transit, throughput, alarm_burst).
Synthetic, for screenshots only.  usage: python development/launch_one_fault.py <fault> <port>"""
import sys, random
import os; sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "master_node", "python"))
import master.app as app_mod
import master.live_master as lm

FAULT, PORT = sys.argv[1], int(sys.argv[2])
_detect = app_mod.detect

def rows_for(now, win):
    random.seed(11)
    k = win / 3600
    rows, t = [], now - win
    def row(t, g, ag, transit, to):
        return dict(first_ts=t, camera_ts=t, final_ts=t + transit, final_grade=g, camera_grade="Reject" if g == "Reject" else "Grade A",
                    acoustic_grade=ag, timed_out=to)
    while t < now - win * 0.25:                       # normal behaviour
        cam = "Reject" if random.random() < 0.05 else "Grade A"
        rows.append(row(t, cam, cam if random.random() < .9 else "Grade B", 2.6, False)); t += 20 * k
    gap = 45 * k if FAULT == "throughput" else 20 * k  # recent stretch: one thing changes
    while t < now:
        rej = 0.40 if FAULT == "reject_rate" else 0.05
        hold = 0.35 if FAULT == "hold_rate" else 0.0
        cam = "Reject" if random.random() < rej else "Grade A"
        g = "Hold" if random.random() < hold else cam
        dis = 0.6 if FAULT == "agreement" else 0.1
        ag = "Grade B" if random.random() < dis else cam
        rows.append(row(t, g, ag if FAULT != "agreement" else ag, 6.0 if FAULT == "transit" else 2.6,
                        FAULT == "timeouts" and random.random() < .25)); t += gap
    return rows

def alarms_for(now):
    return [dict(kind="camera_silent", level="alarm", ts=now - 100 * i) for i in range(1, 7)] if FAULT == "alarm_burst" else []

app_mod.detect = lambda rows, alarms, now, win: _detect(rows_for(now, win), alarms_for(now), now, win)
app_mod.diagnose = lambda *a, **k: []
_lc = lm.load_config
def load_config():
    c = _lc(); c["dashboard"]["port"] = PORT; return c
lm.load_config = load_config
lm.main(["--demo", "--ai", "ollama", "--no-sync"])
