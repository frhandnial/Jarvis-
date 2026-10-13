"""JARVIS heads-up display (Tkinter, no extra installs).

Reads events from events.q (posted by the assistant thread) and sends typed commands back to
Assistant.handle on a worker thread, so the window never freezes.
"""
import datetime
import math
import queue
import random
import threading
import time
import tkinter as tk
import tkinter.font as tkfont

import events
import timetable
import tools

# ---- palette ----
BG = "#050a12"
PANEL = "#0a1524"
PANEL_EDGE = "#14324d"
CYAN = "#00d8ff"
CYAN_DIM = "#0d5a73"
TEXT = "#d6f3ff"
MUTED = "#6c8aa3"
GREEN = "#3dffb0"
AMBER = "#ffb547"
RED = "#ff5c7a"

STATE_STYLE = {  # colour, label, ring speed
    "standby": (CYAN_DIM, "STANDBY  ·  SAY \"JARVIS\"", 0.6),
    "listening": (GREEN, "LISTENING", 1.4),
    "thinking": (AMBER, "THINKING", 4.0),
    "speaking": (CYAN, "SPEAKING", 2.0),
    "muted": (RED, "MICROPHONE OFF", 0.25),
    "offline": (RED, "OFFLINE", 0.2),
}

HEAD = "Bahnschrift"   # ships with Windows 10/11; Tk falls back automatically if missing
MONO = "Consolas"
FACE_SCALE = 0.6       # robot size relative to its 400 px design, leaving room for the week hologram


def blend(c1, c2, t):
    """Mix two #rrggbb colours (t=0 -> c1, t=1 -> c2)."""
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#{:02x}{:02x}{:02x}".format(*[int(x + (y - x) * t) for x, y in zip(a, b)])


class Hud(tk.Tk):
    def __init__(self, assistant, model):
        try:  # crisp text on high-DPI screens
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
        super().__init__()
        self.assistant = assistant
        self.model = model
        self.s = max(1.0, self.winfo_fpixels("1i") / 96.0)  # pixel scale for canvas drawing
        self.fs = self.s * FACE_SCALE                        # pixel scale for the robot
        self.state = "standby"
        self.angle = 0.0
        self.level = 0.0  # smoothed "voice" level for the core pulse

        self.title("J.A.R.V.I.S.")
        self.configure(bg=BG)
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        w = min(int(1240 * self.s), sw - int(40 * self.s))
        h = min(int(780 * self.s), sh - int(110 * self.s))  # leave room for the taskbar and title bar
        self.geometry("{}x{}+{}+{}".format(w, h, (sw - w) // 2, max(0, (sh - h - int(110 * self.s)) // 2)))
        self.minsize(int(1080 * self.s), int(680 * self.s))
        self.protocol("WM_DELETE_WINDOW", self.close)

        self._build_header()
        self._build_footer()  # packed before the body so the body can't squeeze it off-screen
        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True, padx=int(18 * self.s), pady=(0, int(8 * self.s)))
        body.columnconfigure(0, weight=0)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)
        left = tk.Frame(body, bg=BG)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, int(18 * self.s)))
        left.rowconfigure(0, weight=1)
        left.columnconfigure(0, weight=1)
        self._build_schedule(left)
        self._build_reactor(left)
        self._build_right(body)
        self.after(10, self._dark_title_bar)

        self.after(30, self._animate)
        self.after(50, self._poll_events)
        self.after(200, self._tick_clock)
        self.after(300, self._refresh_today)
        threading.Thread(target=self._load_weather, daemon=True).start()

    # ---------------- layout ----------------
    def _panel(self, parent, title):
        outer = tk.Frame(parent, bg=PANEL, highlightthickness=1, highlightbackground=PANEL_EDGE)
        tk.Label(outer, text=title, bg=PANEL, fg=CYAN, font=(HEAD, 10, "bold"), anchor="w").pack(
            fill="x", padx=12, pady=(10, 4))
        tk.Frame(outer, bg=PANEL_EDGE, height=1).pack(fill="x", padx=12)
        return outer

    def _build_header(self):
        h = tk.Frame(self, bg=BG)
        h.pack(fill="x", padx=int(18 * self.s), pady=(int(14 * self.s), int(6 * self.s)))
        left = tk.Frame(h, bg=BG)
        left.pack(side="left")
        tk.Label(left, text="J.A.R.V.I.S.", bg=BG, fg=CYAN, font=(HEAD, 24, "bold")).pack(anchor="w")
        tk.Label(left, text="JUST A RATHER VERY INTELLIGENT SYSTEM", bg=BG, fg=MUTED,
                 font=(HEAD, 8)).pack(anchor="w")
        right = tk.Frame(h, bg=BG)
        right.pack(side="right")
        self.clock = tk.Label(right, bg=BG, fg=TEXT, font=(HEAD, 24))
        self.clock.pack(anchor="e")
        self.date = tk.Label(right, bg=BG, fg=MUTED, font=(HEAD, 9))
        self.date.pack(anchor="e")
        tk.Frame(self, bg=PANEL_EDGE, height=1).pack(fill="x", padx=int(18 * self.s), pady=(0, int(10 * self.s)))

    def _build_schedule(self, parent):
        """Top-left hologram of the week's timetable; redrawn on resize and with the TODAY panel."""
        self.week = tk.Canvas(parent, width=int(560 * self.s), height=int(360 * self.s), bg=BG,
                              highlightthickness=0)
        self.week.grid(row=0, column=0, sticky="nsew", pady=(0, int(6 * self.s)))
        self.week.bind("<Configure>", lambda e: self._draw_week())
        self.week_font = tkfont.Font(family=HEAD, size=8)
        self.week_small = tkfont.Font(family=HEAD, size=7)
        self.week_grid = None   # (x0, y0, x1, y1) of the hour grid, for the scan line
        self.week_shift = 0     # sideways glitch offset currently applied, in pixels
        self.week_glitch_at = time.time() + 5

    def _build_reactor(self, parent):
        col = tk.Frame(parent, bg=BG)
        col.grid(row=1, column=0, sticky="ew")
        size = int(400 * self.fs)
        self.cv = tk.Canvas(col, width=size, height=size, bg=BG, highlightthickness=0)
        self.cv.pack(side="left")
        self.cx = self.cy = size / 2
        s = self.fs

        # static outer ring with tick marks
        r = 185 * s
        self.cv.create_oval(self.cx - r, self.cy - r, self.cx + r, self.cy + r, outline=PANEL_EDGE, width=1)
        for i in range(120):
            a = math.radians(i * 3)
            r1 = (178 if i % 5 else 170) * s
            self.cv.create_line(self.cx + r1 * math.cos(a), self.cy + r1 * math.sin(a),
                                self.cx + 185 * s * math.cos(a), self.cy + 185 * s * math.sin(a),
                                fill=PANEL_EDGE)
        # slow rotating ring around the face: (radius, width, segments, extent, direction, speed factor)
        self.rings = []
        for radius, width, segs, extent, direction, speed in ((172, 2, 4, 50, 1, 0.5),):
            rr = radius * s
            items = [self.cv.create_arc(self.cx - rr, self.cy - rr, self.cx + rr, self.cy + rr,
                                        start=i * 360 / segs, extent=extent, style="arc", width=width * s,
                                        outline=CYAN_DIM) for i in range(segs)]
            self.rings.append((items, segs, direction, speed))

        # friendly robot as a one-colour hologram, drawn back to front.
        # Each part is a crisp edge over a wide glow/fill layer.
        self.parts = {}
        self._part("shoulders", glow=False)
        self._part("neck", closed=True, smooth=False)
        self._part("antenna", smooth=False)
        self.antenna_ball = self.cv.create_oval(0, 0, 0, 0, width=2 * s)
        for side in "lr":
            self._part("ear_" + side, closed=True, smooth=False)
            self._part("ear_line_" + side, glow=False, smooth=False)
        self._part("head", closed=True, smooth=False)
        self._part("screen", closed=True, smooth=False)
        self.cheeks = [self.cv.create_oval(0, 0, 0, 0, outline="") for _ in range(2)]
        for side in "lr":
            self._part("eye_" + side, closed=True, smooth=True)
        self.pupils = [self.cv.create_oval(0, 0, 0, 0, outline="") for _ in range(2)]
        self.sparkles = [self.cv.create_oval(0, 0, 0, 0, outline="") for _ in range(2)]
        self._part("smile")
        self.bars = [self.cv.create_line(0, 0, 0, 0, width=6 * s, capstyle="round") for _ in range(7)]
        self._part("scan", glow=False, smooth=False)

        # robot animation state
        self.eye_open = 1.0
        self.eye_glow = 0.7
        self.gaze = [0.0, 0.0]
        self.glance = (0.0, 0.0)
        self.next_glance = 0.0
        self.next_blink = time.time() + 2.0
        self.blink_start = -10.0

        # status and buttons sit beside the robot so the week hologram above gets the height
        side = tk.Frame(col, bg=BG)
        side.pack(side="left", expand=True)
        self.status_label = tk.Label(side, bg=BG, fg=CYAN, font=(HEAD, 13, "bold"))
        self.status_label.pack(pady=(0, int(10 * self.s)))

        btns = tk.Frame(side, bg=BG)
        btns.pack()
        self.mic_btn = self._button(btns, "MIC: ON", self.toggle_mic)
        self.mic_btn.pack(side="left", padx=4)
        self._button(btns, "SHUT DOWN", self.close, fg=RED).pack(side="left", padx=4)

    def _button(self, parent, text, cmd, fg=CYAN):
        return tk.Button(parent, text=text, command=cmd, bg=PANEL, fg=fg, activebackground=PANEL_EDGE,
                         activeforeground=TEXT, relief="flat", bd=0, padx=14, pady=6, cursor="hand2",
                         font=(HEAD, 9, "bold"), highlightthickness=1, highlightbackground=PANEL_EDGE)

    def _build_right(self, body):
        col = tk.Frame(body, bg=BG)
        col.grid(row=0, column=1, sticky="nsew")
        col.rowconfigure(1, weight=1)
        col.columnconfigure(0, weight=1)

        today = self._panel(col, "TODAY")
        today.grid(row=0, column=0, sticky="ew", pady=(0, int(12 * self.s)))
        self.weather = tk.Label(today, text="Weather: loading…", bg=PANEL, fg=TEXT, font=(HEAD, 10),
                                anchor="w", justify="left")
        self.weather.pack(fill="x", padx=12, pady=(8, 2))
        self.classes = tk.Text(today, height=6, bg=PANEL, fg=TEXT, relief="flat", bd=0, wrap="word",
                               font=(HEAD, 10), cursor="arrow", highlightthickness=0, spacing1=2, spacing3=2)
        self.classes.pack(fill="x", padx=12, pady=(2, 10))
        self.classes.tag_configure("done", foreground=MUTED)
        self.classes.tag_configure("next", foreground=GREEN)
        self.classes.tag_configure("time", foreground=CYAN)
        self.classes.tag_configure("note", foreground=MUTED, font=(HEAD, 9))

        convo = self._panel(col, "CONVERSATION")
        convo.grid(row=1, column=0, sticky="nsew")
        entry_row = tk.Frame(convo, bg=PANEL)
        entry_row.pack(side="bottom", fill="x", padx=12, pady=(0, 12))  # before the log, so it always shows
        wrap = tk.Frame(convo, bg=PANEL)
        wrap.pack(fill="both", expand=True, padx=12, pady=(8, 8))
        self.log = tk.Text(wrap, bg=PANEL, fg=TEXT, relief="flat", bd=0, wrap="word", font=(HEAD, 10),
                           padx=4, pady=4, highlightthickness=0, cursor="arrow", spacing3=6)
        scroll = tk.Scrollbar(wrap, command=self.log.yview, bg=PANEL, troughcolor=PANEL, relief="flat",
                              bd=0, width=10)
        self.log.configure(yscrollcommand=scroll.set, state="disabled")
        scroll.pack(side="right", fill="y")
        self.log.pack(side="left", fill="both", expand=True)
        self.log.tag_configure("who_user", foreground=GREEN, font=(HEAD, 9, "bold"))
        self.log.tag_configure("who_jarvis", foreground=CYAN, font=(HEAD, 9, "bold"))
        self.log.tag_configure("tool", foreground=MUTED, font=(MONO, 9))
        self.log.tag_configure("error", foreground=RED)

        self.entry = tk.Entry(entry_row, bg="#0d1d31", fg=TEXT, insertbackground=CYAN, relief="flat",
                              font=(HEAD, 11), highlightthickness=1, highlightbackground=PANEL_EDGE,
                              highlightcolor=CYAN)
        self.entry.pack(side="left", fill="x", expand=True, ipady=6)
        self.entry.bind("<Return>", lambda e: self.send())
        self._button(entry_row, "SEND", self.send).pack(side="left", padx=(8, 0))
        self._placeholder = "Type a command and press Enter…"
        self._show_placeholder()
        self.entry.bind("<FocusIn>", lambda e: self._clear_placeholder())
        self.entry.bind("<FocusOut>", lambda e: self._show_placeholder())

    def _dark_title_bar(self):
        """Windows 10/11: make the title bar dark to match the HUD."""
        try:
            import ctypes
            hwnd = ctypes.windll.user32.GetParent(self.winfo_id())
            on = ctypes.c_int(1)
            for attr in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (new / older builds)
                if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(on), ctypes.sizeof(on)) == 0:
                    break
            self.withdraw()
            self.deiconify()  # redraw so the new title bar colour shows immediately
        except Exception:
            pass

    def _build_footer(self):
        f = tk.Frame(self, bg=BG)
        f.pack(side="bottom", fill="x", padx=int(18 * self.s), pady=(0, int(10 * self.s)))
        self.footer = tk.Label(f, bg=BG, fg=MUTED, font=(HEAD, 8), anchor="w")
        self.footer.pack(side="left")

    # ---------------- placeholder text ----------------
    def _show_placeholder(self):
        if not self.entry.get():
            self.entry.insert(0, self._placeholder)
            self.entry.configure(fg=MUTED)

    def _clear_placeholder(self):
        if self.entry.get() == self._placeholder:
            self.entry.delete(0, "end")
            self.entry.configure(fg=TEXT)

    # ---------------- actions ----------------
    def send(self):
        text = self.entry.get().strip()
        if not text or text == self._placeholder:
            return
        self.entry.delete(0, "end")
        threading.Thread(target=self.assistant.handle, args=(text,), daemon=True).start()

    def toggle_mic(self):
        self.assistant.muted = not self.assistant.muted
        self.mic_btn.configure(text="MIC: OFF" if self.assistant.muted else "MIC: ON",
                               fg=RED if self.assistant.muted else CYAN)
        self.set_state("muted" if self.assistant.muted else "standby")

    def close(self):
        self.assistant.stopped.set()
        self.destroy()

    def set_state(self, state):
        self.state = state if state in STATE_STYLE else "standby"
        colour, label, _ = STATE_STYLE[self.state]
        self.status_label.configure(text=label, fg=colour if self.state != "standby" else CYAN)

    def add_message(self, who, text, tag=None):
        self.log.configure(state="normal")
        stamp = datetime.datetime.now().strftime("%H:%M")
        if who == "tool":
            self.log.insert("end", "   ⚙ {}\n".format(text.replace("_", " ")), "tool")
        elif who == "error":
            self.log.insert("end", "⚠ {}\n".format(text), "error")
        else:
            self.log.insert("end", "{}  {}\n".format("YOU" if who == "user" else "JARVIS", stamp),
                            "who_" + who)
            self.log.insert("end", text + "\n")
        self.log.configure(state="disabled")
        self.log.see("end")

    # ---------------- periodic updates ----------------
    def _poll_events(self):
        try:
            while True:
                kind, data = events.q.get_nowait()
                if kind == "status":
                    self.set_state(data)
                elif kind in ("user", "jarvis", "tool", "error"):
                    self.add_message(kind, data)
                elif kind == "weather":
                    self.weather.configure(text=data)
                elif kind == "show":
                    self.deiconify()
                    self.lift()
                    self.attributes("-topmost", True)
                    self.after(300, lambda: self.attributes("-topmost", False))
                elif kind == "quit":
                    self.after(400, self.destroy)
                    return
        except queue.Empty:
            pass
        self.after(50, self._poll_events)

    def _tick_clock(self):
        now = datetime.datetime.now()
        self.clock.configure(text=now.strftime("%H:%M:%S"))
        uk = timetable.uk_now()
        same = now.astimezone().utcoffset() == uk.utcoffset()
        self.date.configure(text=now.strftime("%A, %d %B %Y").upper() +
                            ("" if same else "   ·   UK {}".format(uk.strftime("%H:%M"))))
        idle_left = max(0, int(self.assistant.last_active + self._idle_seconds() - time.monotonic()))
        self.footer.configure(text="MODEL  {}     ·     AUTO-SLEEP IN {}:{:02d}     ·     "
                                   "SAY \"JARVIS\" OR TYPE BELOW".format(self.model, idle_left // 60,
                                                                         idle_left % 60))
        self.after(1000, self._tick_clock)

    @staticmethod
    def _idle_seconds():
        import jarvis
        return jarvis.IDLE_SECONDS

    @staticmethod
    def _load_weather():
        """Background thread: fetch weather every 30 min and hand it to the UI thread via the queue."""
        import jarvis
        while True:
            try:
                city, w = tools.fetch_weather(jarvis.CITY, timeout=10)
                text = "{}   {}°C   (feels {}°C)   {}   ·   humidity {}%".format(
                    city, w["temp_C"], w["FeelsLikeC"], w["weatherDesc"][0]["value"].strip(), w["humidity"])
            except Exception:
                text = "Weather unavailable"
            events.q.put(("weather", text))
            time.sleep(1800)

    def _refresh_today(self):
        self._draw_week()
        t = self.classes
        t.configure(state="normal")
        t.delete("1.0", "end")
        try:
            now = timetable.uk_now()
            today = timetable.classes_on(now.date())
            if not timetable.load():
                t.insert("end", "No timetable file found (timetable.xlsx).", "note")
            elif not today:
                upcoming = [c for c in timetable.load() if c["start"] > now]
                t.insert("end", "No classes today.\n", "note")
                if upcoming:
                    c = upcoming[0]
                    t.insert("end", "Next: {} {}  {}".format(c["start"].strftime("%a %d %b"),
                                                            c["start"].strftime("%H:%M"),
                                                            timetable.short_name(c)), "note")
            else:
                nxt = next((c for c in today if c["end"] > now), None)
                for c in today:
                    done = c["end"] <= now
                    tag = "done" if done else ("next" if c is nxt else None)
                    mark = "✓" if done else ("▶" if c is nxt else "•")
                    t.insert("end", "{} ".format(mark), tag)
                    t.insert("end", "{}–{}  ".format(c["start"].strftime("%H:%M"), c["end"].strftime("%H:%M")),
                             tag or "time")
                    t.insert("end", "{} · {} · {}\n".format(timetable.short_name(c), c["activity"],
                                                            timetable.building(c)), tag)
                t.insert("end", "UK times", "note")
            t.update_idletasks()  # size the box to the wrapped lines
            lines = t.count("1.0", "end", "displaylines")
            t.configure(height=max(2, min(9, lines[0] if isinstance(lines, tuple) else (lines or 2))))
        except Exception as e:
            t.insert("end", "Timetable error: {}".format(e), "note")
        t.configure(state="disabled")
        self.after(30000, self._refresh_today)

    # ---------------- week hologram ----------------
    def _draw_week(self):
        """Timetable hologram: a column per weekday, a row per hour and a glowing block per class."""
        cv, s = self.week, self.s
        cv.delete("all")
        self.week_grid, self.week_shift = None, 0
        w, h = cv.winfo_width(), cv.winfo_height()
        if w < 100 or h < 100:
            return
        now = timetable.uk_now()
        classes = timetable.load()

        def in_week(monday):
            return [c for c in classes if monday <= c["date"] < monday + datetime.timedelta(days=7)]

        monday = now.date() - datetime.timedelta(days=now.weekday())
        week, label = in_week(monday), "THIS WEEK"
        if now.weekday() >= 5 and not any(c["date"] >= now.date() for c in week):
            monday += datetime.timedelta(days=7)  # weekend with nothing left: show the week ahead
            week, label = in_week(monday), "NEXT WEEK"
        ndays = 7 if any(c["date"].weekday() >= 5 for c in week) else 5
        last = monday + datetime.timedelta(days=ndays - 1)
        today_i = (now.date() - monday).days

        # frame: faint projection plane, thin edges and bright corner brackets
        pad, k = 2 * s, 16 * s
        cv.create_rectangle(pad, pad, w - pad, h - pad, fill=blend(BG, CYAN, 0.03),
                            outline=blend(CYAN, BG, 0.8), tags="holo")
        for x, y, dx, dy in ((pad, pad, 1, 1), (w - pad, pad, -1, 1), (pad, h - pad, 1, -1),
                             (w - pad, h - pad, -1, -1)):
            cv.create_line(x + dx * k, y, x, y, x, y + dy * k, fill=CYAN, width=2 * s, tags="holo")

        # title row
        tx, ly = pad + 12 * s, pad + 28 * s
        span = "{} – {}".format(monday.strftime("%d" if monday.month == last.month else "%d %b"),
                                last.strftime("%d %b"))
        cv.create_text(tx, pad + 15 * s, text="WEEKLY SCHEDULE", anchor="w", fill=CYAN,
                       font=(HEAD, 10, "bold"), tags="holo")
        cv.create_text(w - tx, pad + 15 * s, text="{}  ·  {}  ·  UK TIME".format(label, span).upper(),
                       anchor="e", fill=MUTED, font=(HEAD, 8), tags="holo")
        cv.create_line(tx, ly, w - tx, ly, fill=blend(CYAN, BG, 0.75), tags="holo")
        cv.create_line(tx, ly, tx + 70 * s, ly, fill=CYAN, width=2 * s, tags="holo")

        # grid: the hour range covers the whole term so it doesn't jump between weeks
        h0 = min((c["start"].hour for c in classes), default=9)
        h1 = max((c["end"].hour + (1 if c["end"].minute else 0) for c in classes), default=18)
        gx0, gx1 = tx + 22 * s, w - tx
        gy0, gy1 = ly + 26 * s, h - pad - 10 * s
        colw, hour_h = (gx1 - gx0) / ndays, (gy1 - gy0) / max(1, h1 - h0)
        self.week_grid = (gx0, gy0, gx1, gy1)

        def y_at(t):
            return gy0 + (t.hour + t.minute / 60 - h0) * hour_h

        if 0 <= today_i < ndays:
            x = gx0 + today_i * colw
            cv.create_rectangle(x, ly + 4 * s, x + colw, gy1, fill=blend(BG, CYAN, 0.07), outline="", tags="holo")
        for y in range(int(gy0), int(gy1), max(3, int(3 * s))):  # scanline texture
            cv.create_line(gx0, y, gx1, y, fill=blend(BG, CYAN, 0.045), tags="holo")
        for hour in range(h0, h1 + 1):
            y = gy0 + (hour - h0) * hour_h
            cv.create_line(gx0, y, gx1, y, fill=blend(CYAN, BG, 0.8), dash=(2, 4), tags="holo")
            cv.create_text(gx0 - 6 * s, y, text="{:02d}".format(hour), anchor="e", fill=blend(CYAN, BG, 0.4),
                           font=(MONO, 7), tags="holo")
        for i in range(ndays):
            day, x = monday + datetime.timedelta(days=i), gx0 + i * colw
            if i:
                cv.create_line(x, ly + 6 * s, x, gy1, fill=blend(CYAN, BG, 0.82), tags="holo")
            colour = CYAN if i == today_i else blend(CYAN, BG, 0.55 if i < today_i else 0.2)
            cv.create_text(x + colw / 2, ly + 13 * s, text=day.strftime("%a %d").upper(), fill=colour,
                           font=(HEAD, 9, "bold" if i == today_i else "normal"), tags="holo")
            if i == today_i:  # glowing underline under today
                for width, c in ((4 * s, blend(CYAN, BG, 0.65)), (1.5 * s, CYAN)):
                    cv.create_line(x + 10 * s, gy0 - 4 * s, x + colw - 10 * s, gy0 - 4 * s, fill=c,
                                   width=width, tags="holo")

        # class blocks: done = faded, next (or on now) = green, later = cyan
        nxt = next((c for c in week if c["end"] > now), None)
        f1, f2 = self.week_font, self.week_small
        l1, l2 = f1.metrics("linespace"), f2.metrics("linespace")
        for n, c in enumerate(week):
            i = c["date"].weekday()
            if i >= ndays:
                continue
            x0, x1 = gx0 + i * colw + 3 * s, gx0 + (i + 1) * colw - 3 * s
            y0, y1 = y_at(c["start"]) + 1, y_at(c["end"]) - 1
            if c["end"] <= now:
                edge, fill, ink = blend(CYAN, BG, 0.5), blend(BG, CYAN, 0.06), blend(TEXT, BG, 0.45)
            elif c is nxt:
                edge, fill, ink = GREEN, blend(BG, GREEN, 0.16), TEXT
            else:
                edge, fill, ink = CYAN, blend(BG, CYAN, 0.13), TEXT
            tag = "class{}".format(n)
            live = ("live",) if c["start"] <= now < c["end"] else ()
            g = 1.5 * s
            cv.create_rectangle(x0 - g, y0 - g, x1 + g, y1 + g, outline=blend(edge, BG, 0.7), width=3 * s,
                                tags="holo", state="disabled")
            cv.create_rectangle(x0, y0, x1, y1, outline=edge, fill=fill, tags=("holo", tag) + live)
            cv.create_rectangle(x0, y0, x0 + 3 * s, y1, outline="", fill=edge, tags="holo", state="disabled")

            room, tw = y1 - y0 - 2 * s, x1 - x0 - 10 * s
            name = self._fit(timetable.short_name(c), f1, tw, max(1, int(room // l1)))
            place = self._fit(timetable.building(c), f2, tw, 1) if room >= len(name) * l1 + l2 else []
            ty = y0 + max(s, (y1 - y0 - len(name) * l1 - len(place) * l2) / 2)
            cv.create_text(x0 + 7 * s, ty, text="\n".join(name), anchor="nw", fill=ink, font=f1,
                           tags="holo", state="disabled")
            if place:
                cv.create_text(x0 + 7 * s, ty + len(name) * l1, text=place[0], anchor="nw",
                               fill=blend(ink, BG, 0.35), font=f2, tags="holo", state="disabled")
            cv.tag_bind(tag, "<Enter>", lambda e, c=c, box=(x0, y0, x1, y1): self._week_tip(c, box))
            cv.tag_bind(tag, "<Leave>", lambda e: cv.delete("tip"))

        if not classes or not week:
            cv.create_text((gx0 + gx1) / 2, (gy0 + gy1) / 2, fill=MUTED, font=(HEAD, 10), tags="holo",
                           text="NO TIMETABLE FILE (timetable.xlsx)" if not classes else "NO CLASSES THIS WEEK")

        # current time across today's column
        if 0 <= today_i < ndays and h0 <= now.hour + now.minute / 60 <= h1:
            x, y = gx0 + today_i * colw, y_at(now)
            cv.create_line(x, y, x + colw, y, fill=AMBER, width=1.5 * s, tags="holo", state="disabled")
            cv.create_oval(x - 3 * s, y - 3 * s, x + 3 * s, y + 3 * s, fill=AMBER, outline="", tags="holo",
                           state="disabled")

        # scan line, moved by _animate_week
        cv.create_line(gx0, gy0, gx1, gy0, fill=blend(CYAN, BG, 0.82), width=4 * s, tags=("holo", "scan_glow"),
                       state="disabled")
        cv.create_line(gx0, gy0, gx1, gy0, fill=blend(CYAN, BG, 0.45), tags=("holo", "scan"), state="disabled")

    @staticmethod
    def _fit(text, font, width, lines):
        """Word-wrap text into at most `lines` lines of `width` pixels, ending in … if it's cut short."""
        def clip(line):
            while line and font.measure(line + "…") > width:
                line = line[:-1]
            return line.rstrip() + "…"

        words, out = text.split(), []
        while words and len(out) < lines:
            line = words.pop(0)
            while words and font.measure(line + " " + words[0]) <= width:
                line += " " + words.pop(0)
            out.append(line if font.measure(line) <= width else clip(line))
        if words and out and not out[-1].endswith("…"):
            out[-1] = clip(out[-1])
        return out

    def _week_tip(self, c, box):
        """Full details of a class beside its block while the mouse is over it."""
        cv, s = self.week, self.s
        cv.delete("tip")
        wrap = int(230 * s)
        title = cv.create_text(0, 0, anchor="nw", width=wrap, text=c["module"].split(";")[0].strip(),
                               fill=CYAN, font=(HEAD, 9, "bold"), tags="tip", state="disabled")
        lines = ["{}  ·  {}  {}–{}".format(c["activity"], c["start"].strftime("%a %d %b"),
                                           c["start"].strftime("%H:%M"), c["end"].strftime("%H:%M")),
                 c["location"]]
        if c["lecturers"]:
            lines.append(c["lecturers"])
        cv.create_text(0, cv.bbox(title)[3] + 3 * s, anchor="nw", width=wrap, text="\n".join(lines), fill=TEXT,
                       font=(HEAD, 8), tags="tip", state="disabled")
        bx0, by0, bx1, by1 = cv.bbox("tip")
        tw, th = bx1 - bx0 + 16 * s, by1 - by0 + 12 * s
        x = box[2] + 6 * s if box[2] + 6 * s + tw <= cv.winfo_width() else box[0] - 6 * s - tw
        x, y = max(2, x), min(max(2, box[1]), cv.winfo_height() - th - 2)
        cv.move("tip", x + 8 * s - bx0, y + 6 * s - by0)
        cv.tag_lower(cv.create_rectangle(x, y, x + tw, y + th, fill=PANEL, outline=CYAN, tags="tip",
                                         state="disabled"), title)

    def _animate_week(self, now):
        """Scan line sweeping down the hologram, a pulsing live class and the odd sideways glitch."""
        if not self.week_grid:
            return
        gx0, gy0, gx1, gy1 = self.week_grid
        y = gy0 + (now * 0.22 % 1) * (gy1 - gy0)
        for tag in ("scan", "scan_glow"):
            self.week.coords(tag, gx0 + self.week_shift, y, gx1 + self.week_shift, y)
        self.week.itemconfigure("live", outline=blend(GREEN, BG, 0.35 + 0.35 * math.sin(now * 4)))
        if now >= self.week_glitch_at:  # jolt sideways for a couple of frames, like a hologram losing lock
            dx = -self.week_shift if self.week_shift else random.choice((-3, 3)) * self.s
            self.week.move("holo", dx, 0)
            self.week_shift += dx
            self.week_glitch_at = now + (0.08 if self.week_shift else random.uniform(4, 9))

    # ---------------- face ----------------
    def _part(self, name, closed=False, glow=True, smooth=True):
        """Create one face stroke: an optional wide dim 'glow' line under a thin bright one."""
        s = self.fs
        make = self.cv.create_polygon if closed else self.cv.create_line
        opts = {"smooth": smooth, "joinstyle": "round" if smooth else "miter"}
        opts.update({"fill": ""} if closed else {"capstyle": "round"})
        items = []
        if glow:
            items.append(make(0, 0, 0, 0, 0, 0, width=6 * s, **opts))
        items.append(make(0, 0, 0, 0, 0, 0, width=(2 if glow else 1) * s, **opts))
        self.parts[name] = (items, closed)

    def _xy(self, x, y):
        return self.cx + x * self.fs, self.cy + y * self.fs

    def _draw(self, name, pts, colour, glow=None, fill=""):
        """Move a face stroke to pts (face units, centre = 0,0) and recolour it."""
        items, closed = self.parts[name]
        flat = [v for x, y in pts for v in self._xy(x, y)]
        for i, item in enumerate(items):
            self.cv.coords(item, *flat)
            c = glow if (len(items) == 2 and i == 0) else colour
            if closed:
                self.cv.itemconfigure(item, outline=c, fill=fill if i == 0 else "")
            else:
                self.cv.itemconfigure(item, fill=c)

    def _animate(self):
        colour, _, speed = STATE_STYLE.get(self.state, STATE_STYLE["standby"])
        now = time.time()
        self.angle = (self.angle + speed) % 360
        for items, segs, direction, factor in self.rings:
            base = self.angle * factor * direction
            for i, item in enumerate(items):
                self.cv.itemconfigure(item, start=(base + i * 360 / segs) % 360, outline=blend(colour, BG, 0.45))

        # voice level for the sound bars in the mouth
        target = random.uniform(0.15, 1.0) if self.state == "speaking" else 0.0
        self.level += (target - self.level) * (0.45 if self.state == "speaking" else 0.25)

        # eyes: brightness and how open they are by mood, plus a blink every few seconds
        if self.state == "thinking":
            wanted = random.uniform(0.6, 1.0)
        else:
            wanted = {"listening": 1.0, "speaking": 0.95, "muted": 0.35, "offline": 0.2}.get(
                self.state, 0.8 + 0.1 * math.sin(now * 2))
        self.eye_glow += (wanted - self.eye_glow) * 0.25
        self.eye_open += ({"listening": 1.12, "muted": 0.08, "offline": 0.05}.get(self.state, 1.0)
                          - self.eye_open) * 0.2
        if now >= self.next_blink:
            self.blink_start = now
            self.next_blink = now + random.uniform(2.5, 6.0)
        b = (now - self.blink_start) / 0.18
        o = self.eye_open * (abs(1 - 2 * b) if 0 <= b <= 1 else 1.0)

        # gaze: look up while thinking, at you while listening/speaking, wander otherwise
        if self.state == "thinking":
            goal = (8 * math.cos(now * 1.5), -7)
        elif self.state in ("listening", "speaking"):
            goal = (0, 0)
        else:
            if now >= self.next_glance:
                self.next_glance = now + random.uniform(1.5, 4.0)
                self.glance = (random.uniform(-9, 9), random.uniform(-5, 5))
            goal = self.glance
        self.gaze[0] += (goal[0] - self.gaze[0]) * 0.15
        self.gaze[1] += (goal[1] - self.gaze[1]) * 0.15
        gx, gy = self.gaze

        # one-colour hologram: every shade comes from the mood colour
        holo = CYAN if self.state == "standby" else colour
        edge = blend(holo, BG, 0.1)
        halo = blend(holo, BG, 0.72)
        detail = blend(holo, BG, 0.5)
        light = blend(holo, "#ffffff", 0.6)

        def rrect(x0, y0, x1, y1, r):
            """Rounded rectangle outline as points."""
            pts = []
            for cx, cy, start in ((x1 - r, y0 + r, -90), (x1 - r, y1 - r, 0), (x0 + r, y1 - r, 90),
                                  (x0 + r, y0 + r, 180)):
                for i in range(7):
                    a = math.radians(start + i * 15)
                    pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
            return pts

        def oval(item, x, y, rx, ry, fill, visible=True):
            self.cv.coords(item, *self._xy(x - rx, y - ry), *self._xy(x + rx, y + ry))
            self.cv.itemconfigure(item, fill=fill, state="normal" if visible else "hidden")

        # body: shoulders, neck, antenna, ears, head and face screen
        self._draw("shoulders", [(-130, 150), (-70, 124), (0, 118), (70, 124), (130, 150)], edge, halo)
        self._draw("neck", rrect(-34, 88, 34, 116, 6), edge, halo, fill=blend(BG, holo, 0.08))
        self._draw("antenna", [(0, -116), (0, -150)], edge, halo)
        if self.state == "thinking":
            ant = 1.0 if math.sin(now * 12) > 0 else 0.2  # busy blinking
        elif self.state == "speaking":
            ant = 0.4 + 0.6 * self.level
        else:
            ant = 0.45 + 0.35 * math.sin(now * 3)
        oval(self.antenna_ball, 0, -159, 9, 9, blend(BG, light, ant))
        self.cv.itemconfigure(self.antenna_ball, outline=edge)
        for side, k in ((-1, "l"), (1, "r")):
            x0, x1 = sorted((112 * side, 134 * side))
            self._draw("ear_" + k, rrect(x0, -50, x1, 14, 8), edge, halo, fill=blend(BG, holo, 0.1))
            self._draw("ear_line_" + k, [(123 * side, -38), (123 * side, 2)], detail)
        self._draw("head", rrect(-112, -116, 112, 90, 44), edge, halo, fill=blend(BG, holo, 0.05))
        self._draw("screen", rrect(-90, -92, 90, 68, 30), edge, halo, fill=blend(BG, holo, 0.12))

        # rosy cheeks
        for i, side in enumerate((-1, 1)):
            oval(self.cheeks[i], 66 * side, 24, 12, 7, blend(BG, holo, 0.3))

        # big round eyes with pupils and a sparkle
        for i, (side, k) in enumerate(((-1, "l"), (1, "r"))):
            ex, ey, r = 44 * side, -26, 26
            ry = r * max(o, 0.06)
            ring = [(ex + r * math.cos(math.radians(t)), ey + ry * math.sin(math.radians(t)))
                    for t in range(0, 360, 20)]
            self._draw("eye_" + k, ring, blend(detail, light, self.eye_glow),
                       blend(BG, light, self.eye_glow * 0.5), fill=blend(BG, holo, 0.25 * self.eye_glow))
            px, py = ex + gx, ey + gy * o
            pr = 11 * min(1.0, o * 1.4)
            show = o > 0.25
            oval(self.pupils[i], px, py, pr, pr, blend(BG, light, self.eye_glow), show)
            oval(self.sparkles[i], px - 4, py - 4, 3.5, 3.5, "#ffffff", show)

        # mouth: a smile, which turns into bouncing sound bars while talking
        talking = self.state == "speaking"
        items, _ = self.parts["smile"]
        for item in items:
            self.cv.itemconfigure(item, state="hidden" if talking else "normal")
        self._draw("smile", [(-34, 34), (-18, 46), (0, 50), (18, 46), (34, 34)], edge, halo)
        for j, bar in enumerate(self.bars):
            x = -36 + j * 12
            h = 3 + 26 * self.level * random.uniform(0.4, 1.0) * (1 - abs(j - 3) * 0.12)
            self.cv.coords(bar, *self._xy(x, 42 - h / 2), *self._xy(x, 42 + h / 2))
            self.cv.itemconfigure(bar, fill=light, state="normal" if talking else "hidden")

        # hologram scan line sweeping down the head
        y = -116 + (now * 50) % 206
        self._draw("scan", [(-110, y), (110, y)], blend(holo, BG, 0.6))
        self._animate_week(now)
        self.after(33, self._animate)
